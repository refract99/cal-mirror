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

from fastapi import Request, Response

from cal_mirror.ports import Store

PENDING_COOKIE = "cal_mirror_pending"
ENROLL_PATH = "/account/2fa"
ENROLLMENT_PATHS = frozenset(
    {ENROLL_PATH, "/account/passkeys/options", "/account/passkeys", "/signout"}
)
LOCK_MESSAGE = "Sign-in is locked. Try again later."
FAILURE_MESSAGE = "Wrong password or code."


def client_address(request: Request) -> str:
    """The connection's peer address (`request.client.host`); never a forwarded header."""
    raise NotImplementedError


def is_locked(request: Request) -> bool:
    """True while this request's client address is locked out of sign-in (app clock)."""
    raise NotImplementedError


def record_failure(request: Request) -> bool:
    """Count one failed sign-in attempt for this client address.

    Returns True when this failure reached `signin_failure_limit` and set the lock for
    `signin_lock_seconds`.
    """
    raise NotImplementedError


def pending_hash(request: Request) -> str | None:
    """Session hash of this request's valid, unexpired pending sign-in, or None."""
    raise NotImplementedError


def complete_signin(request: Request, response: Response) -> None:
    """Finish a sign-in: clear this address's failures and lock only, end any pending
    sign-in (store and cookie), and set a new `cal_mirror_session` cookie on `response`."""
    raise NotImplementedError


def second_factor_enrolled(store: Store) -> bool:
    """True when TOTP is on or at least one passkey is registered."""
    raise NotImplementedError


def render_signin(
    request: Request, name: str, status_code: int = 200, **context: object
) -> Response:
    """Render `signin.html` or `signin_totp.html` with the context every sign-in page needs.

    Always passes `google_linked` (bool), `totp_enabled` (bool) and `passkeys`
    (`Store.list_passkeys()`), plus `context` (for example `error`).
    """
    raise NotImplementedError


def render_account(request: Request, status_code: int = 200, **context: object) -> Response:
    """Render `account_2fa.html` with the context the account page needs.

    Always passes `enabled` (TOTP on), `passkeys` (`Store.list_passkeys()`), `google_link`
    (`Store.get_google_link()`) and `signin_redirect` (`signin_redirect_uri(request)`), plus
    `context` (for example `error`).
    """
    raise NotImplementedError


def signin_redirect_uri(request: Request) -> str:
    """`CAL_MIRROR_SIGNIN_REDIRECT`, else `<request base URL>signin/google/callback`."""
    raise NotImplementedError
