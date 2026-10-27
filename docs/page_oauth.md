# page_oauth

`cal_mirror/web/page_oauth.py` leaves `router`, `get_flow` and `CALENDAR_SCOPE`
(`https://www.googleapis.com/auth/calendar`). Every route needs `current_user` (no user: 303
to `/signin`) and uses the store from `get_store`. The page never reads or writes calendar
events, and it never shows the token, the code, the state value or an exception text.

## Routes

### `GET /google`

Shows the connection state in `<p id="google-state">`:

- `Connect`: no `google_token` secret (missing or empty). Link button `Connect Google`.
- `Connected`: a token is stored and `Status.token_ok` is true. Link button `Reconnect Google`.
- `Reconnect`: a token is stored and `Status.token_ok` is false. Link button `Reconnect Google`.

### `GET /google/connect`

Calls `flow.authorization_url(access_type="offline", prompt="consent",
include_granted_scopes="true")`, stores the returned state in meta `oauth_state` and
redirects (303) to Google's URL.

### `GET /google/callback?state=...&code=...`

- `state` missing or empty, no stored `oauth_state`, or a mismatch (`secrets.compare_digest`):
  400 page `OAuth state does not match`; nothing stored.
- `code` missing or empty (Google sends `?error=access_denied`): 400 page
  `Google sign-in failed`.
- Otherwise `flow.fetch_token(code=code)`, then `google_token` is stored from
  `flow.credentials.to_json()`, `Status.token_ok` is set to true, `oauth_state` is cleared
  (a replayed callback fails the state check) and the reply is 303 to `/google`.
- `OAuth2Error`, `ValueError` or `Warning` from the exchange: 400 page
  `Google sign-in failed`; nothing stored, `oauth_state` kept.

Error pages show the message in `<p id="oauth-error">` and a `Try again` link to
`/google/connect`.

## `get_flow()`

A FastAPI dependency that builds a `Flow` per request, reading the environment inside the
function (never at import):

- `CAL_MIRROR_GOOGLE_CLIENT`: path to the OAuth client JSON.
- `CAL_MIRROR_OAUTH_REDIRECT`: HTTPS redirect URI ending `/google/callback`.

The flow is rebuilt on each request, so no PKCE verifier would survive to the callback;
`autogenerate_code_verifier=False` keeps connect and callback consistent.
