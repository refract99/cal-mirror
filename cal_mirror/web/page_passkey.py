"""Passkeys (WebAuthn) as a second factor: register, list, delete, and sign in.

`router` holds the routes below; `cal_mirror.web.app` includes it. Relying party ID is the host
the app is reached at (`request.url.hostname`); the expected origin is the app's own origin.

- `POST /account/passkeys/options` (session): JSON registration options.
- `POST /account/passkeys` (session, form `name`, `credential`): verify and store; 303 to
  `/account/2fa`; 400 on a bad credential.
- `POST /account/passkeys/delete` (session, form `credential_id`): 303 to `/account/2fa`; 400
  when it is the last second factor.
- `POST /signin/passkey/options` (pending sign-in): JSON authentication options.
- `POST /signin/passkey` (pending sign-in, form `credential`): 303 to `/status` with a session,
  or a counted failure.

Challenges live in store meta, one slot per purpose (`passkey_registration`, `passkey_signin`),
bound to the session or pending sign-in hash that asked for them, valid for `PENDING_SECONDS`
and taken (deleted) by the first verify attempt. Details in `docs/page_passkey.md`.
"""

import json
import threading
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import PublicKeyCredentialDescriptor

from cal_mirror.model import Passkey
from cal_mirror.web.deps import SESSION_COOKIE, session_hash
from cal_mirror.web.signin_guard import (
    ENROLL_PATH,
    LOCK_MESSAGE,
    PENDING_SECONDS,
    complete_signin,
    is_locked,
    pending_hash,
    record_failure,
    render_account,
    render_signin,
)

RP_NAME = "cal-mirror"
USER_NAME = "owner"
DEFAULT_NAME = "Passkey"
NAME_LIMIT = 80
REGISTRATION_SLOT = "passkey_registration"
SIGNIN_SLOT = "passkey_signin"
REGISTER_ERROR = "Passkey could not be added."
SIGNIN_ERROR = "Passkey sign-in failed."
LAST_FACTOR_ERROR = "Keep at least one second factor: turn on an authenticator app first."

# Parsing a credential from the browser can fail in any of these ways; none is a server error.
_BAD_CREDENTIAL = (WebAuthnException, ValueError, KeyError, TypeError)

router = APIRouter()
_slot_lock = threading.Lock()


def _issue(request: Request, slot: str, owner: str, value: bytes) -> None:
    expires = request.app.state.clock.now() + timedelta(seconds=PENDING_SECONDS)
    record = {
        "owner": owner,
        "challenge": bytes_to_base64url(value),
        "expires": expires.isoformat(),
    }
    with _slot_lock:
        request.app.state.store.set_meta(slot, json.dumps(record))


def _take(request: Request, slot: str, owner: str) -> bytes | None:
    """The challenge issued to `owner` and still valid, deleting the slot either way."""
    store = request.app.state.store
    with _slot_lock:
        raw = store.get_meta(slot)
        store.set_meta(slot, "")
    if not raw:
        return None
    record = json.loads(raw)
    if record["owner"] != owner:
        return None
    if datetime.fromisoformat(record["expires"]) <= request.app.state.clock.now():
        return None
    return base64url_to_bytes(record["challenge"])


def _rp_id(request: Request) -> str:
    return request.url.hostname or ""


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _descriptors(passkeys: list[Passkey]) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id)) for p in passkeys]


def _redirect(path: str) -> Response:
    return RedirectResponse(path, status_code=303)


def _options_response(options_json: str) -> Response:
    return JSONResponse(json.loads(options_json))


def _session_owner(request: Request) -> str:
    return session_hash(request.cookies.get(SESSION_COOKIE, ""))


@router.post("/account/passkeys/options")
def registration_options(request: Request) -> Response:
    options = generate_registration_options(
        rp_id=_rp_id(request),
        rp_name=RP_NAME,
        user_name=USER_NAME,
        user_id=USER_NAME.encode(),
        exclude_credentials=_descriptors(request.app.state.store.list_passkeys()),
    )
    _issue(request, REGISTRATION_SLOT, _session_owner(request), options.challenge)
    return _options_response(options_to_json(options))


@router.post("/account/passkeys")
def add_passkey(
    request: Request,
    name: Annotated[str, Form()] = "",
    credential: Annotated[str, Form()] = "",
) -> Response:
    store = request.app.state.store
    challenge = _take(request, REGISTRATION_SLOT, _session_owner(request))
    verified = None
    if challenge is not None:
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=_rp_id(request),
                expected_origin=_origin(request),
            )
        except _BAD_CREDENTIAL:
            verified = None
    credential_id = bytes_to_base64url(verified.credential_id) if verified else ""
    if verified is None or any(p.credential_id == credential_id for p in store.list_passkeys()):
        return render_account(request, 400, error=REGISTER_ERROR)
    store.add_passkey(
        Passkey(
            credential_id,
            name.strip()[:NAME_LIMIT] or DEFAULT_NAME,
            verified.credential_public_key,
            verified.sign_count,
            request.app.state.clock.now(),
        )
    )
    return _redirect(ENROLL_PATH)


@router.post("/account/passkeys/delete")
def delete_passkey(request: Request, credential_id: Annotated[str, Form()] = "") -> Response:
    store = request.app.state.store
    passkeys = store.list_passkeys()
    remaining = [p for p in passkeys if p.credential_id != credential_id]
    if not remaining and passkeys and not store.get_auth().totp_enabled:
        return render_account(request, 400, error=LAST_FACTOR_ERROR)
    store.delete_passkey(credential_id)
    return _redirect(ENROLL_PATH)


@router.post("/signin/passkey/options")
def signin_options(request: Request) -> Response:
    pending = pending_hash(request)
    if pending is None:
        return _redirect("/signin")
    options = generate_authentication_options(
        rp_id=_rp_id(request),
        allow_credentials=_descriptors(request.app.state.store.list_passkeys()),
    )
    _issue(request, SIGNIN_SLOT, pending, options.challenge)
    return _options_response(options_to_json(options))


def _signin_failed(request: Request) -> Response:
    if record_failure(request):
        return render_signin(request, "signin_totp.html", 429, error=LOCK_MESSAGE)
    return render_signin(request, "signin_totp.html", 401, error=SIGNIN_ERROR)


def _verified_count(request: Request, challenge: bytes, credential: str) -> tuple[str, int] | None:
    """Credential ID and the sign count to store when `credential` is a valid assertion."""
    try:
        credential_id = json.loads(credential)["id"]
        stored = next(
            p for p in request.app.state.store.list_passkeys() if p.credential_id == credential_id
        )
        # The library's own counter check also refuses a zero count after a non-zero one; the
        # rule here is only that a non-zero count must grow, so it is applied below.
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(request),
            expected_origin=_origin(request),
            credential_public_key=stored.public_key,
            credential_current_sign_count=0,
        )
    except (*_BAD_CREDENTIAL, StopIteration):
        return None
    count = verified.new_sign_count
    if count and count <= stored.sign_count:
        return None
    return credential_id, max(count, stored.sign_count)


@router.post("/signin/passkey")
def signin(request: Request, credential: Annotated[str, Form()] = "") -> Response:
    if is_locked(request):
        return render_signin(request, "signin_totp.html", 429, error=LOCK_MESSAGE)
    pending = pending_hash(request)
    if pending is None:
        return _redirect("/signin")
    challenge = _take(request, SIGNIN_SLOT, pending)
    result = _verified_count(request, challenge, credential) if challenge else None
    if result is None:
        return _signin_failed(request)
    request.app.state.store.set_passkey_sign_count(*result)
    response = _redirect("/status")
    complete_signin(request, response)
    return response
