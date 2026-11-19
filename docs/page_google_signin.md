# Sign in with Google

`cal_mirror/web/page_google_signin.py` exports `router`, `SIGNIN_SCOPES = ("openid", "email")`
and the dependency `get_signin_flow`. It links one Google account to the owner and lets that
account sign in. Rules shared with the other sign-in parts (failure lock, session) live in
`signin_guard`, see [page_signin.md](page_signin.md).

The sign-in request is separate from the calendar grant ([page_oauth.md](page_oauth.md)). It
asks for `openid` and `email` only, never a calendar scope, and never reads, writes or deletes
the secret `google_token`. It uses the stored OAuth web client (`page_oauth.load_client`).

## Redirect URI

`CAL_MIRROR_SIGNIN_REDIRECT`, else `<base URL>signin/google/callback`. The owner adds it to the
Google client; the account page shows it.

## Routes

| Route | What it does |
|---|---|
| `POST /account/google/link` | Signed in. `303` to Google, purpose `link`. |
| `POST /account/google/unlink` | Signed in. Forgets the link, `303` to `/account/2fa`. |
| `POST /signin/google` | Public. While an account is linked, `303` to Google, purpose `signin`. With no link: `401`, counted as a failure. |
| `GET /signin/google/callback?state=&code=` | Public. See below. |

`get_signin_flow` builds the flow per request; every route takes it through `Depends`, so tests
override it. With no stored client it raises `FlowConfigError`.

## State

`flow.authorization_url` returns `(url, state)`. The state and its purpose are kept in store
meta `google_signin_state`. It is single use: a matching callback spends it. A missing or
different state (`secrets.compare_digest`) is a refusal.

## Callback

1. `is_locked`: `429`.
2. State not the issued one, or no `code`: refused.
3. `flow.fetch_token(code=code)`. Google returns the email scope as
   `https://www.googleapis.com/auth/userinfo.email`, which oauthlib reports as a scope change,
   so the token scope check is relaxed (`OAUTHLIB_RELAX_TOKEN_SCOPE`) for this exchange only,
   under a lock, and restored afterwards. An exchange error is a refusal.
4. The `id_token` payload is decoded without a signature fetch (it came from Google's token
   endpoint over TLS, OIDC Core 3.1.3.7). `iss` must be `accounts.google.com` or
   `https://accounts.google.com`, `aud` the stored client ID, `exp` later than the app clock.
5. Purpose `link`: needs a signed-in session; stores `GoogleLink(sub, email)`, replacing any old
   link, and answers `303` to `/account/2fa`.
6. Purpose `signin`: needs a link whose `sub` equals the token's; then `complete_signin` (a full
   session, no second-factor step) and `303` to `/status`.

Every refusal gives no session, calls `record_failure` and answers `401` with the sign-in page
and "Google sign-in refused."

## Templates

`signin_google.html` (included by `signin.html` only while linked) has the "Sign in with
Google" button. `account_google.html` (included by `account_2fa.html`) shows the linked email
or "not linked", the Link and Unlink forms and the redirect URI.
