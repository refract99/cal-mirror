"""Shared sign-in rules: per-address failure lock, pending sign-ins, completing a sign-in.

Every sign-in route (password, TOTP, passkey, Google) uses these helpers so the lock rule and
the session rules live in one place. The store and clock come from `request.app.state`; the
lock settings come from `Settings.signin_failure_limit` and `Settings.signin_lock_seconds`.

Model and store additions that go with this module (owned by the same intent):

- `cal_mirror.model.Passkey(credential_id: str, name: str, public_key: bytes, sign_count: int,
  created: datetime)`, frozen dataclass, fields in that order.
- `cal_mirror.model.GoogleLink(sub: str, email: str)`, frozen dataclass.
- `Store.list_passkeys() -> list[Passkey]` (oldest `created` first),
  `Store.add_passkey(passkey: Passkey) -> None`,
  `Store.set_passkey_sign_count(credential_id: str, sign_count: int) -> None`,
  `Store.delete_passkey(credential_id: str) -> None` (unknown ID: no-op),
  `Store.get_google_link() -> GoogleLink | None`,
  `Store.set_google_link(link: GoogleLink | None) -> None` (None unlinks).
"""

import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Request, Response

from cal_mirror.ports import Store
from cal_mirror.web import deps
from cal_mirror.web.deps import SESSION_COOKIE, session_hash

SESSION_SECONDS = 43200
PENDING_SECONDS = 300
PENDING_COOKIE = "cal_mirror_pending"
ENROLL_PATH = "/account/2fa"
ENROLLMENT_PATHS = frozenset(
    {ENROLL_PATH, "/account/passkeys/options", "/account/passkeys", "/signout"}
)
LOCK_MESSAGE = "Sign-in is locked. Try again later."
FAILURE_MESSAGE = "Wrong password or code."

_STATE_ATTR = "signin_lock_state"
_create_lock = threading.Lock()


@dataclass
class _Entry:
    failures: int
    last_failure: datetime
    locked_until: datetime | None = None


@dataclass
class _LockState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    entries: dict[str, _Entry] = field(default_factory=dict)


def _lock_state(request: Request) -> _LockState:
    state = request.app.state
    with _create_lock:
        existing = getattr(state, _STATE_ATTR, None)
        if existing is None:
            existing = _LockState()
            setattr(state, _STATE_ATTR, existing)
        return existing


def _prune(entries: dict[str, _Entry], now: datetime, lock_seconds: int) -> None:
    window = timedelta(seconds=lock_seconds)
    for address in [
        a
        for a, e in entries.items()
        if now - e.last_failure >= window and not (e.locked_until and e.locked_until > now)
    ]:
        del entries[address]


def _cookie_options(samesite: Literal["lax", "strict"]) -> dict[str, object]:
    return {"httponly": True, "secure": True, "samesite": samesite}


def _new_session(store: Store, now: datetime, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    store.create_session(session_hash(token), now + timedelta(seconds=SESSION_SECONDS))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, **_cookie_options("lax"))


def client_address(request: Request) -> str:
    """The client address uvicorn gives (`request.client.host`): the peer, or the
    `X-Forwarded-For` client when the peer is in `FORWARDED_ALLOW_IPS`."""
    return request.client.host if request.client else ""


def is_locked(request: Request) -> bool:
    """True while this request's client address is locked out of sign-in (app clock)."""
    now = request.app.state.clock.now()
    lock_seconds = request.app.state.store.get_settings().signin_lock_seconds
    state = _lock_state(request)
    with state.lock:
        _prune(state.entries, now, lock_seconds)
        entry = state.entries.get(client_address(request))
        return entry is not None and entry.locked_until is not None and entry.locked_until > now


def record_failure(request: Request) -> bool:
    """Count one failed sign-in attempt for this client address.

    Returns True when this failure reached `signin_failure_limit` and set the lock for
    `signin_lock_seconds`.
    """
    now = request.app.state.clock.now()
    settings = request.app.state.store.get_settings()
    state = _lock_state(request)
    with state.lock:
        _prune(state.entries, now, settings.signin_lock_seconds)
        address = client_address(request)
        entry = state.entries.get(address)
        failures = (entry.failures if entry else 0) + 1
        locked_until = entry.locked_until if entry else None
        if failures >= settings.signin_failure_limit:
            state.entries[address] = _Entry(
                0, now, now + timedelta(seconds=settings.signin_lock_seconds)
            )
            return True
        state.entries[address] = _Entry(failures, now, locked_until)
        return False


def pending_hash(request: Request) -> str | None:
    """Session hash of this request's valid, unexpired pending sign-in, or None."""
    token = request.cookies.get(PENDING_COOKIE)
    if not token:
        return None
    digest = session_hash("pending:" + token)
    if request.app.state.store.session_valid(digest, request.app.state.clock.now()):
        return digest
    return None


def complete_signin(request: Request, response: Response) -> None:
    """Finish a sign-in: clear this address's failures and lock only, end any pending
    sign-in (store and cookie), and set a new `cal_mirror_session` cookie on `response`."""
    store = request.app.state.store
    state = _lock_state(request)
    with state.lock:
        state.entries.pop(client_address(request), None)
    pending = request.cookies.get(PENDING_COOKIE)
    if pending:
        store.delete_session(session_hash("pending:" + pending))
    response.delete_cookie(PENDING_COOKIE, **_cookie_options("strict"))
    _new_session(store, request.app.state.clock.now(), response)


def second_factor_enrolled(store: Store) -> bool:
    """True when TOTP is on or at least one passkey is registered."""
    return store.get_auth().totp_enabled or bool(store.list_passkeys())


def render_signin(
    request: Request, name: str, status_code: int = 200, **context: object
) -> Response:
    """Render `signin.html` or `signin_totp.html` with the context every sign-in page needs.

    Always passes `google_linked` (bool), `totp_enabled` (bool) and `passkeys`
    (`Store.list_passkeys()`), plus `context` (for example `error`).
    """
    store = request.app.state.store
    ctx: dict[str, object] = {
        "google_linked": store.get_google_link() is not None,
        "totp_enabled": store.get_auth().totp_enabled,
        "passkeys": store.list_passkeys(),
        **context,
    }
    return deps.templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_account(request: Request, status_code: int = 200, **context: object) -> Response:
    """Render `account_2fa.html` with the context the account page needs.

    Always passes `enabled` (TOTP on), `passkeys` (`Store.list_passkeys()`), `google_link`
    (`Store.get_google_link()`) and `signin_redirect` (`signin_redirect_uri(request)`), plus
    `context` (for example `error`).
    """
    store = request.app.state.store
    ctx: dict[str, object] = {
        "enabled": store.get_auth().totp_enabled,
        "passkeys": store.list_passkeys(),
        "google_link": store.get_google_link(),
        "signin_redirect": signin_redirect_uri(request),
        **context,
    }
    return deps.templates.TemplateResponse(
        request, "account_2fa.html", ctx, status_code=status_code
    )


def signin_redirect_uri(request: Request) -> str:
    """`CAL_MIRROR_SIGNIN_REDIRECT`, else `<request base URL>signin/google/callback`."""
    return (
        os.environ.get("CAL_MIRROR_SIGNIN_REDIRECT") or f"{request.base_url}signin/google/callback"
    )
