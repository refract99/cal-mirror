# Sign-in page

`cal_mirror/web/page_signin.py` exports `router`, `hash_password`, `verify_password`,
`MAX_FAILURES = 5`, `LOCK_SECONDS = 900`, `SESSION_SECONDS = 43200` and
`PENDING_COOKIE = "cal_mirror_pending"`. There is one user. Every time comes from the app clock.
Passwords and recovery codes are stored as argon2 hashes only.

## Routes

| Route | What it does |
|---|---|
| `GET /setup`, `POST /setup` | Set the first password (fields `password`, `confirm`). Only while no password is set, otherwise `303` to `/signin`. Under 12 characters or a mismatch gives `400`. |
| `GET /signin` | The password form. With no password set, `303` to `/setup`. |
| `POST /signin` | Field `password`. Right password with 2FA off: new session, `303` to `/status`. With 2FA on: pending cookie, `303` to `/signin/totp`. |
| `GET /signin/totp`, `POST /signin/totp` | Field `code`: an authenticator code (one step either side) or a recovery code. A recovery code works once. Without a valid pending cookie, `303` to `/signin`. |
| `POST /signout` | Deletes the session and the cookie, `303` to `/signin`. |
| `GET /account/2fa`, `POST /account/2fa` | Turn 2FA on or off. Needs a signed-in user. |

## Lock rule

Wrong passwords and wrong second-factor codes share one counter. The 5th failure sets a lock for
900 seconds and resets the counter; that response and every sign-in during the lock is `429`
"Sign-in is locked. Try again later." and the password is not checked. Once the time has passed,
sign-in works again. Only a completed sign-in resets the counter and clears the lock time: a right password with 2FA off, or a good authenticator or recovery code; a right password with 2FA on changes neither. A wrong attempt answers `401`
"Wrong password or code."

## Cookies

Both are `HttpOnly` and `Secure`; `cal_mirror_session` is `SameSite=Lax` so Google's redirect to `/google/callback` carries it, `cal_mirror_pending` is `SameSite=Strict`; and only a SHA-256 hash of the token is stored.

- `cal_mirror_session`: lives 43200 seconds (12 hours).
- `cal_mirror_pending`: lives 300 seconds, between the password step and the code step. It is
  removed once the code is accepted.

## Turning 2FA on and off

`POST /account/2fa` takes `action` and `code`.

1. `enable` (while off): creates a TOTP secret and 10 recovery codes. The page shows the
   `otpauth://` URI, the secret and the codes once, with a confirm form. 2FA is still off.
2. `confirm`: a valid code turns 2FA on. A bad code gives `400` "Invalid code." and it stays off.
3. `disable` (while on): a valid authenticator code turns it off, deletes the recovery codes and
   clears the secret. A bad code gives `400` and it stays on.

Any other action gives `400`. `Settings.two_factor` is not read; the switch is
`AuthRecord.totp_enabled`.
