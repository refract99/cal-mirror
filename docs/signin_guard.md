# signin_guard

`cal_mirror/web/signin_guard.py` holds the sign-in rules every sign-in route shares: the
per-address failure lock, the pending sign-in, completing a sign-in, and rendering the sign-in and
account pages. The password, TOTP, passkey and Google routes call it, so the rules live in one
place. The store and clock come from `request.app.state`. See [page_signin.md](page_signin.md)
for the rules as the user sees them.

## Constants

| Name | Value |
|---|---|
| `PENDING_COOKIE` | `"cal_mirror_pending"` (`page_signin` re-exports it) |
| `ENROLL_PATH` | `"/account/2fa"` |
| `ENROLLMENT_PATHS` | `/account/2fa`, `/account/passkeys/options`, `/account/passkeys`, `/signout`: the only non-public paths a session with no second factor may reach |
| `LOCK_MESSAGE` | "Sign-in is locked. Try again later." (`429`) |
| `FAILURE_MESSAGE` | "Wrong password or code." (`401`) |

The session lives `SESSION_SECONDS = 43200` and the pending sign-in `PENDING_SECONDS = 300`.

## Functions

- `client_address(request) -> str`: `request.client.host`. That is the peer address, unless the
  peer is a proxy listed in uvicorn's `FORWARDED_ALLOW_IPS`; then uvicorn has already replaced it
  with the client named in `X-Forwarded-For`. The app reads no forwarded header itself.
- `is_locked(request) -> bool`: true while this address is locked, by the app clock. Every
  sign-in route calls it first; locked means `429` `LOCK_MESSAGE`, credentials unchecked, no
  cookie set.
- `record_failure(request) -> bool`: counts one failed attempt for this address. Returns true
  when this failure reached `Settings.signin_failure_limit` and set the lock for
  `Settings.signin_lock_seconds`; the password and TOTP steps then answer `429`, otherwise `401`
  `FAILURE_MESSAGE`. The limits are read from `store.get_settings()` on every call.
- `pending_hash(request) -> str | None`: the session hash of this request's valid, unexpired
  pending sign-in, or `None`.
- `complete_signin(request, response) -> None`: clears this address's failures and lock only,
  ends any pending sign-in (store and cookie), and sets a new `cal_mirror_session` cookie
  (`HttpOnly`, `Secure`, `SameSite=Lax`, 43200 seconds). The password step alone never calls it.
- `second_factor_enrolled(store) -> bool`: true when TOTP is on or at least one passkey exists.
- `render_signin(request, name, status_code=200, **context) -> Response`: renders `signin.html` or
  `signin_totp.html` with `google_linked`, `totp_enabled` and `passkeys`, plus `context`.
- `render_account(request, status_code=200, **context) -> Response`: renders `account_2fa.html`
  with `enabled`, `passkeys`, `google_link` and `signin_redirect`, plus `context`.
- `signin_redirect_uri(request) -> str`: `CAL_MIRROR_SIGNIN_REDIRECT`, else
  `<request base URL>signin/google/callback`.

## Lock state

The state is a dict on `request.app.state`: address to failures, last failure time and
`locked_until`. A `threading.Lock` guards it because sync routes run in a thread pool. All times
come from the app clock. An address is forgotten once `signin_lock_seconds` have passed since its
last failure and no lock is running, so memory is bounded by recent activity. The state is not
stored: it is lost when the app restarts.

## Store additions

The model and store changes that go with this module are in [architecture.md](architecture.md):
`Passkey`, `GoogleLink`, and the passkey and Google link `Store` methods.
