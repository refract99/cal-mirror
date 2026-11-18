"""Sign in with Google: link one Google account, unlink it, and sign in with it.

`router` holds the routes below; `cal_mirror.web.app` includes it. The OAuth request is separate
from the calendar grant: scopes `openid` and `email` only, redirect URI
`signin_guard.signin_redirect_uri(request)`, and it never reads or writes `google_token`.

- `POST /account/google/link` (session): 303 to Google.
- `POST /account/google/unlink` (session): forget the link; 303 to `/account/2fa`.
- `POST /signin/google`: 303 to Google while an account is linked.
- `GET /signin/google/callback` (public): finish linking (303 to `/account/2fa`) or signing in
  (303 to `/status` with a session); a refused sign-in answers 401 and is counted.
"""

import base64
import binascii
import contextlib
import json
import os
import secrets
import threading
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from cal_mirror.model import GoogleLink
from cal_mirror.ports import Store
from cal_mirror.web.deps import current_user, get_store
from cal_mirror.web.page_oauth import NOT_SET_UP, FlowConfigError, load_client
from cal_mirror.web.signin_guard import (
    ENROLL_PATH,
    LOCK_MESSAGE,
    complete_signin,
    is_locked,
    record_failure,
    render_signin,
    signin_redirect_uri,
)

router = APIRouter()

SIGNIN_SCOPES = ("openid", "email")

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
REFUSED = "Google sign-in refused."

_STATE_META = "google_signin_state"
_PURPOSES = ("link", "signin")
_RELAX_ENV = "OAUTHLIB_RELAX_TOKEN_SCOPE"
_relax_lock = threading.Lock()

StoreDep = Annotated[Store, Depends(get_store)]
UserDep = Annotated[str, Depends(current_user)]


def get_signin_flow(request: Request, store: Annotated[Store, Depends(get_store)]) -> Flow:
    """FastAPI dependency: the sign-in OAuth flow on the stored client (`load_client`).

    Every route here takes the flow from this dependency, so tests can override it. Raises
    `FlowConfigError` when no client is stored.
    """
    client = load_client(store)
    if client is None:
        raise FlowConfigError(NOT_SET_UP)
    try:
        return Flow.from_client_config(
            client,
            scopes=list(SIGNIN_SCOPES),
            redirect_uri=signin_redirect_uri(request),
            autogenerate_code_verifier=False,
        )
    except (ValueError, KeyError) as err:
        raise FlowConfigError(
            f"The Google client is not a Web application OAuth client: {type(err).__name__}"
        ) from None


FlowDep = Annotated[Flow, Depends(get_signin_flow)]


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _refuse(request: Request) -> Response:
    record_failure(request)
    return render_signin(request, "signin.html", 401, error=REFUSED)


def _start(store: Store, flow: Flow, purpose: str) -> RedirectResponse:
    url, state = flow.authorization_url(prompt="select_account")
    store.set_meta(_STATE_META, f"{purpose}:{state}")
    return _redirect(url)


def _take_state(store: Store, given: str | None) -> str | None:
    """The purpose of the issued state when `given` is it; the state is then spent."""
    purpose, _, expected = (store.get_meta(_STATE_META) or "").partition(":")
    if purpose not in _PURPOSES or not given or not expected:
        return None
    if not secrets.compare_digest(given.encode(), expected.encode()):
        return None
    store.set_meta(_STATE_META, "")
    return purpose


@contextlib.contextmanager
def _relaxed_token_scope() -> Iterator[None]:
    """Google returns `email` as a userinfo URL; oauthlib reads the relax switch from the
    process environment only, so set it for this exchange alone and put it back."""
    with _relax_lock:
        before = os.environ.get(_RELAX_ENV)
        os.environ[_RELAX_ENV] = "1"
        try:
            yield
        finally:
            if before is None:
                os.environ.pop(_RELAX_ENV, None)
            else:
                os.environ[_RELAX_ENV] = before


def _id_token_claims(token: object, client_id: object, now: float) -> dict | None:
    """The claims of Google's ID token when issuer, audience and expiry are right.

    The token came straight from Google's token endpoint over TLS (OIDC Core 3.1.3.7), so the
    signature is not fetched or checked.
    """
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if (
        claims.get("iss") not in GOOGLE_ISSUERS
        or not isinstance(client_id, str)
        or claims.get("aud") != client_id
        or isinstance(exp, bool)
        or not isinstance(exp, int | float)
        or exp <= now
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
    ):
        return None
    return claims


@router.post("/account/google/link")
def link_google(user: UserDep, store: StoreDep, flow: FlowDep) -> RedirectResponse:
    return _start(store, flow, "link")


@router.post("/account/google/unlink")
def unlink_google(user: UserDep, store: StoreDep) -> RedirectResponse:
    store.set_google_link(None)
    return _redirect(ENROLL_PATH)


@router.post("/signin/google")
def signin_google(request: Request, store: StoreDep, flow: FlowDep) -> Response:
    if store.get_google_link() is None:
        return _refuse(request)
    return _start(store, flow, "signin")


@router.get("/signin/google/callback")
def google_callback(
    request: Request,
    store: StoreDep,
    flow: FlowDep,
    state: str | None = None,
    code: str | None = None,
) -> Response:
    if is_locked(request):
        return render_signin(request, "signin.html", 429, error=LOCK_MESSAGE)
    purpose = _take_state(store, state)
    if purpose is None or not code:
        return _refuse(request)
    try:
        with _relaxed_token_scope():
            token = flow.fetch_token(code=code)
    except (OAuth2Error, ValueError, Warning, OSError):
        return _refuse(request)
    client = (load_client(store) or {}).get("web") or {}
    claims = _id_token_claims(
        token.get("id_token"), client.get("client_id"), request.app.state.clock.now().timestamp()
    )
    if claims is None:
        return _refuse(request)
    if purpose == "link":
        if getattr(request.state, "user", None) is None:
            return _refuse(request)
        store.set_google_link(GoogleLink(sub=claims["sub"], email=str(claims.get("email", ""))))
        return _redirect(ENROLL_PATH)
    link = store.get_google_link()
    if link is None or not secrets.compare_digest(link.sub.encode(), claims["sub"].encode()):
        return _refuse(request)
    response = _redirect("/status")
    complete_signin(request, response)
    return response
