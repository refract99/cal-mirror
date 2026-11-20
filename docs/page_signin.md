# Sign-in page

`cal_mirror/web/page_signin.py` exports `router`, `hash_password`, `verify_password`,
`SESSION_SECONDS = 43200` and `PENDING_COOKIE = "cal_mirror_pending"` (the cookie name is defined
in `cal_mirror/web/signin_guard.py`, see [signin_guard.md](signin_guard.md), and re-exported
here). There is one user. Every time comes from the app clock. Passwords and recovery codes are
stored as argon2 hashes only.

Sign-in has three parts that share the rules below: the password page (this doc), passkeys
([page_passkey.md](page_passkey.md)) and Sign in with Google
([page_google_signin.md](page_google_signin.md)). The lock rule, the pending sign-in and the
session cookie live in `signin_guard`, so every route applies them the same way.

## Routes

| Route | What it does |
|---|---|
| `GET /setup`, `POST /setup` | Set the first password (fields `password`, `confirm`, `setup_code`). Only while no password is set, otherwise `303` to `/signin`. See [Setup code](#setup-code). |
| `GET /signin` | The password form, plus the Sign in with Google button while an account is linked. With no password set, `303` to `/setup`. |
| `POST /signin` | Field `password`. Right password with a second factor enrolled: pending cookie, `303` to `/signin/totp`. Right password with none enrolled: new session, `303` to `/account/2fa`. |
| `GET /signin/totp`, `POST /signin/totp` | Field `code`: an authenticator code (one step either side) or a recovery code. A recovery code works once. The page shows the code form only while TOTP is on, and the passkey button when a passkey is registered. Without a valid pending cookie, `303` to `/signin`. |
| `/signin/passkey/options`, `/signin/passkey` | Finish a pending sign-in with a passkey. See [page_passkey.md](page_passkey.md). |
| `/signin/google`, `/signin/google/callback` | Sign in with the linked Google account. See [page_google_signin.md](page_google_signin.md). |
| `POST /signout` | Deletes the session and the cookie, `303` to `/signin`. |
| `GET /account/2fa`, `POST /account/2fa` | Turn TOTP on or off, and the page that lists passkeys and the Google link. Needs a signed-in user. |
| `/account/passkeys/options`, `/account/passkeys`, `/account/passkeys/delete` | Register and remove passkeys. See [page_passkey.md](page_passkey.md). |
| `/account/google/link`, `/account/google/unlink` | Link and unlink the Google account for sign-in. See [page_google_signin.md](page_google_signin.md). |

`/setup`, `/signin`, `/signin/totp`, `/signin/passkey/options`, `/signin/passkey`,
`/signin/google` and `/signin/google/callback` are public; every other route needs a session.

## Setup code

`POST /setup` sets the first password, so it needs proof that the person at the page controls the
deployment. The proof is the deployment secret `CAL_MIRROR_SETUP_CODE`, read from the environment
on every request. It is never stored and never logged. The order for `GET /setup` and `POST /setup`:

1. A password is already set: `303` to `/signin`.
2. `CAL_MIRROR_SETUP_CODE` unset or empty: `403`, the page says "The setup code is not
   configured." and shows no form. Nothing can be set up until the operator sets the code.
3. `POST` only: the form field `setup_code` (default empty) is compared with
   `secrets.compare_digest`. Missing or wrong gives `403` and nothing is stored.
4. Then the length and mismatch rules: a password under 12 characters or a `password`/`confirm`
   mismatch gives `400`.

## Second factor

Signing in needs the password and a second factor. A second factor is **enrolled** when TOTP is on
or at least one passkey is registered (`signin_guard.second_factor_enrolled`).

- Right password, second factor enrolled: a pending sign-in starts (`cal_mirror_pending`, 300
  seconds, `SameSite=Strict`) and the user goes to `/signin/totp` to finish with an authenticator
  code, a recovery code or a passkey. This is the same whether TOTP is on or only passkeys exist.
- Right password, nothing enrolled: the session starts at once, but it may reach only the
  enrollment paths (`signin_guard.ENROLLMENT_PATHS`: `/account/2fa`, `/account/passkeys/options`,
  `/account/passkeys`, `/signout`), the public paths and `/static/`. Every other path and method
  answers `303` to `/account/2fa`. When TOTP is confirmed or a passkey is added, the same session
  reaches `/status`.
- `POST /signin/totp` while TOTP is off is a counted failure (a passkey-only account finishes
  with its passkey). Recovery codes still work while TOTP is on.
- The last second factor cannot be removed: `POST /account/2fa` with `disable` while no passkey is
  registered gives `400` "Turning off TOTP would leave no second factor." and changes nothing; so
  does deleting the last passkey while TOTP is off.

## Lock rule

Lock state is kept per client address (`request.client.host`): the connection's peer, or, when
the peer is a proxy listed in `FORWARDED_ALLOW_IPS`, the client its `X-Forwarded-For` names
(uvicorn does this; the app reads no forwarded header itself). Wrong passwords, wrong second-factor codes, bad passkeys and
refused Google sign-ins all count against the same address.

- The limits are `Settings.signin_failure_limit` (default 5) and `Settings.signin_lock_seconds`
  (default 900), read from the store on each check and changed on the Settings page.
- The failure that reaches the limit sets the lock for `signin_lock_seconds`. That response, and
  every sign-in route during the lock, is `429` "Sign-in is locked. Try again later." The
  credentials are not checked and no cookie is set. When the time has passed, sign-in works again.
- A wrong attempt below the limit answers `401` "Wrong password or code."
- The password step alone clears nothing. Only a completed sign-in clears that address's failures
  and lock; it does not touch any other address.
- The state is in memory on the app (`request.app.state`), so it is **lost on restart**. An
  address is forgotten once `signin_lock_seconds` have passed since its last failure and no lock
  is running, so memory stays bounded by recent activity.

## Cookies

Both are `HttpOnly` and `Secure`; `cal_mirror_session` is `SameSite=Lax` so Google's redirects
(`/google/callback` and `/signin/google/callback`) carry it, `cal_mirror_pending` is
`SameSite=Strict`; and only a SHA-256 hash of the token is stored (the pending token is hashed as
`"pending:" + token`).

- `cal_mirror_session`: lives 43200 seconds (12 hours).
- `cal_mirror_pending`: lives 300 seconds, between the password step and the second step. It is
  removed once the sign-in completes.

## Passkeys

A passkey (WebAuthn credential) is a second factor on its own. Register one on `/account/2fa`;
sign in with it on `/signin/totp` after the password. Details, routes and failure handling are in
[page_passkey.md](page_passkey.md).

## Sign in with Google

`/account/2fa` can link one Google account. When one is linked, `/signin` shows a "Sign in with
Google" button. The request asks for `openid` and `email` only and is separate from the calendar
grant. The redirect URI is `CAL_MIRROR_SIGNIN_REDIRECT`, else `<base URL>/signin/google/callback`.
Details are in [page_google_signin.md](page_google_signin.md).

## Origin check

Every `POST` must carry an `Origin` header equal to the app's own origin (scheme, host and port as
the request reached the app). A missing, `null` or different `Origin` gets `403` "Cross-origin
request refused." before anything else runs: no cookie, no store change. `GET` and `HEAD` need no
`Origin`. `Referrer-Policy` is `same-origin`, since browsers send `Origin: null` on same-origin
form posts under `no-referrer`. See [web_app.md](web_app.md).

## Turning TOTP on and off

`POST /account/2fa` takes `action` and `code`.

1. `enable` (while off): creates a TOTP secret and 10 recovery codes. The page shows the
   `otpauth://` URI, the secret and the codes once, with a confirm form. TOTP is still off.
2. `confirm`: a valid code turns TOTP on. A bad code gives `400` "Invalid code." and it stays off.
3. `disable` (while on): a valid authenticator code turns it off, deletes the recovery codes and
   clears the secret, unless that would leave no second factor (a passkey must exist). A bad code
   gives `400` and it stays on.

Any other action gives `400`. The switch is `AuthRecord.totp_enabled`.
