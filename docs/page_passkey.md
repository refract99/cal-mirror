# Passkeys

`cal_mirror/web/page_passkey.py` exports `router` (WebAuthn passkeys as a second factor).
`static/passkey.js` is the browser side; it holds no inline script, so it works under the
`default-src 'self'` policy. The sign-in rules (lock, pending sign-in, session) live in
`signin_guard`, see [signin_guard.md](signin_guard.md) and [page_signin.md](page_signin.md).

The relying party ID is the host the app is reached at (`request.url.hostname`), the expected
origin is `scheme://netloc` of the request, the relying party name is `cal-mirror` and the one
user is `owner`. All routes are `POST`; the app's origin check applies to each.

## Routes

| Route | What it does |
|---|---|
| `/account/passkeys/options` | Signed in, also while enrolling. Registration options as JSON, excluding registered credentials. |
| `/account/passkeys` | Form `name`, `credential` (the registration JSON). Verified against the issued challenge, RP ID and origin: stores the passkey and `303` to `/account/2fa` (this also completes enrollment for a password-only session). Any failure: `400` with an error, nothing stored. |
| `/account/passkeys/delete` | Form `credential_id`. Deleting the last passkey while TOTP is off: `400`, nothing changes. Otherwise deletes and `303` to `/account/2fa`. |
| `/signin/passkey/options` | Public, needs a pending sign-in (else `303` to `/signin`). Authentication options as JSON with the registered credentials. |
| `/signin/passkey` | Public, form `credential`. Locked: `429`. No pending sign-in: `303` to `/signin`. Success: sign count saved, session set, `303` to `/status`. Failure: no session, one counted failure, `signin_totp.html` with `401` (`429` when that failure set the lock). |

A sign-in fails when the credential is not registered, the challenge is not the one issued to
this pending sign-in, the signature is bad, or a non-zero sign count is not greater than the
stored one. A zero count is accepted (some authenticators have no counter) and never lowers the
stored count.

## Storage

Per passkey: credential ID (base64url), name, public key (COSE bytes from the verified
registration), sign count and created (app clock). No private key exists on the server.

## Challenges

A challenge is kept in store meta, one slot per purpose (`passkey_registration` and
`passkey_signin`), as the owner hash (the session hash for registration, the pending sign-in
hash for sign-in), the challenge and an expiry of 300 s (`PENDING_SECONDS`) on the app clock.
Asking for new options replaces the slot. The verify route takes and deletes the slot before it
verifies, so a challenge is used at most once and is spent whether the attempt succeeds or not.
A challenge issued to another session or pending sign-in, or past its expiry, is not accepted.
