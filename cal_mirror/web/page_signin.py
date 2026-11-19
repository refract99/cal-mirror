import os
import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Annotated

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cal_mirror.model import AuthRecord
from cal_mirror.ports import Clock, Store
from cal_mirror.web.deps import (
    SESSION_COOKIE,
    current_user,
    get_clock,
    get_store,
    session_hash,
    templates,
)
from cal_mirror.web.signin_guard import (
    FAILURE_MESSAGE,
    LOCK_MESSAGE,
    PENDING_COOKIE,
    PENDING_SECONDS,
    SESSION_SECONDS,  # noqa: F401 (re-exported)
    _cookie_options,
    complete_signin,
    is_locked,
    pending_hash,
    record_failure,
    render_account,
    render_signin,
    second_factor_enrolled,
)

router = APIRouter()

MIN_PASSWORD_LENGTH = 12
RECOVERY_CODE_COUNT = 10
TOTP_STORE_NAME = "totp_secret"
SETUP_CODE_ENV = "CAL_MIRROR_SETUP_CODE"

_hasher = PasswordHasher()

StoreDep = Annotated[Store, Depends(get_store)]
ClockDep = Annotated[Clock, Depends(get_clock)]
UserDep = Annotated[str, Depends(current_user)]


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerificationError, InvalidHashError):
        return False


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _page(request: Request, name: str, status_code: int = 200, **context: object) -> Response:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _locked_page(request: Request, name: str) -> Response:
    return render_signin(request, name, 429, error=LOCK_MESSAGE)


def _fail(request: Request, name: str) -> Response:
    if record_failure(request):
        return _locked_page(request, name)
    return render_signin(request, name, 401, error=FAILURE_MESSAGE)


def _totp_valid(store: Store, code: str, now: datetime) -> bool:
    secret = store.get_secret(TOTP_STORE_NAME)
    if not secret:
        return False
    return bool(pyotp.TOTP(secret).verify(code.strip(), for_time=now, valid_window=1))


def _setup_code() -> str:
    return os.environ.get(SETUP_CODE_ENV, "")


def _setup_unconfigured(request: Request) -> Response:
    return _page(
        request, "setup.html", 403, error="The setup code is not configured.", no_form=True
    )


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, store: StoreDep) -> Response:
    if store.get_auth().password_hash:
        return _redirect("/signin")
    if not _setup_code():
        return _setup_unconfigured(request)
    return _page(request, "setup.html")


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    store: StoreDep,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    setup_code: Annotated[str, Form()] = "",
) -> Response:
    if store.get_auth().password_hash:
        return _redirect("/signin")
    expected = _setup_code()
    if not expected:
        return _setup_unconfigured(request)
    if not secrets.compare_digest(setup_code.encode(), expected.encode()):
        return _page(request, "setup.html", 403, error="Wrong setup code.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return _page(request, "setup.html", 400, error="Password must be at least 12 characters.")
    if password != confirm:
        return _page(request, "setup.html", 400, error="Passwords do not match.")
    store.save_auth(AuthRecord(password_hash=hash_password(password)))
    return _redirect("/signin")


@router.get("/signin", response_class=HTMLResponse)
def signin_form(request: Request, store: StoreDep) -> Response:
    if not store.get_auth().password_hash:
        return _redirect("/setup")
    return render_signin(request, "signin.html")


@router.post("/signin", response_class=HTMLResponse)
def signin_submit(
    request: Request,
    store: StoreDep,
    clock: ClockDep,
    password: Annotated[str, Form()],
) -> Response:
    auth = store.get_auth()
    if not auth.password_hash:
        return _redirect("/setup")
    if is_locked(request):
        return _locked_page(request, "signin.html")
    if not verify_password(auth.password_hash, password):
        return _fail(request, "signin.html")
    now = clock.now()
    if not second_factor_enrolled(store):
        response = _redirect("/account/2fa")
        complete_signin(request, response)
        return response
    pending = secrets.token_urlsafe(32)
    store.create_session(
        session_hash("pending:" + pending), now + timedelta(seconds=PENDING_SECONDS)
    )
    response = _redirect("/signin/totp")
    response.set_cookie(
        PENDING_COOKIE, pending, max_age=PENDING_SECONDS, **_cookie_options("strict")
    )
    return response


@router.get("/signin/totp", response_class=HTMLResponse)
def totp_form(request: Request) -> Response:
    return render_signin(request, "signin_totp.html")


def _recovery_match(auth: AuthRecord, code: str) -> str | None:
    candidate = code.strip().lower()
    for hashed in auth.recovery_hashes:
        if verify_password(hashed, candidate):
            return hashed
    return None


@router.post("/signin/totp", response_class=HTMLResponse)
def totp_submit(
    request: Request,
    store: StoreDep,
    clock: ClockDep,
    code: Annotated[str, Form()],
) -> Response:
    auth = store.get_auth()
    now = clock.now()
    if is_locked(request):
        return _locked_page(request, "signin_totp.html")
    if pending_hash(request) is None:
        return _redirect("/signin")
    if not auth.totp_enabled:
        return _fail(request, "signin_totp.html")
    if not _totp_valid(store, code, now):
        used = _recovery_match(auth, code)
        if used is None:
            return _fail(request, "signin_totp.html")
        remaining = tuple(h for h in auth.recovery_hashes if h != used)
        store.save_auth(replace(auth, recovery_hashes=remaining))
    response = _redirect("/status")
    complete_signin(request, response)
    return response


@router.post("/signout")
def signout(request: Request, store: StoreDep) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(session_hash(token))
    response = _redirect("/signin")
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/account/2fa", response_class=HTMLResponse)
def account_form(request: Request, store: StoreDep, user: UserDep) -> Response:
    return render_account(request)


@router.post("/account/2fa", response_class=HTMLResponse)
def account_submit(
    request: Request,
    store: StoreDep,
    clock: ClockDep,
    user: UserDep,
    action: Annotated[str, Form()],
    code: Annotated[str, Form()] = "",
) -> Response:
    auth = store.get_auth()
    now = clock.now()
    enabled = auth.totp_enabled
    if action == "enable" and not enabled:
        secret = pyotp.random_base32()
        codes = [secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT)]
        store.set_secret(TOTP_STORE_NAME, secret)
        hashes = tuple(hash_password(c) for c in codes)
        store.save_auth(replace(auth, recovery_hashes=hashes))
        uri = pyotp.TOTP(secret).provisioning_uri(name="owner", issuer_name="cal-mirror")
        return render_account(
            request,
            enabled=False,
            setup=True,
            uri=uri,
            secret=secret,
            codes=codes,
        )
    if action == "confirm" and not enabled:
        if not _totp_valid(store, code, now):
            return render_account(
                request,
                400,
                enabled=False,
                confirming=True,
                error="Invalid code.",
            )
        store.save_auth(replace(auth, totp_enabled=True))
        return _redirect("/account/2fa")
    if action == "disable" and enabled:
        if not _totp_valid(store, code, now):
            return render_account(request, 400, enabled=True, error="Invalid code.")
        if not store.list_passkeys():
            return render_account(
                request,
                400,
                enabled=True,
                error="Turning off TOTP would leave no second factor.",
            )
        store.save_auth(replace(auth, totp_enabled=False, recovery_hashes=()))
        store.set_secret(TOTP_STORE_NAME, None)
        return _redirect("/account/2fa")
    return render_account(request, 400, enabled=enabled, error="Unknown action.")
