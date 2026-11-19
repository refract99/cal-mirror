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
"""

from fastapi import APIRouter

router = APIRouter()
