# Web app

`cal_mirror/web/app.py` builds one FastAPI app from the seven page routers and starts it
with TLS only. It writes nothing itself: the pages write config and requests. It never reads
or writes calendar events.

Entry point: `python -m cal_mirror.web.app` (`main()`).

## Public paths

`PUBLIC_PATHS` is `/signin`, `/signin/totp` and `/setup`. No static files are mounted.
`GET /` redirects to `/status` (303) after the session check.

## Session middleware

On every request the middleware reads the `cal_mirror_session` cookie. When the cookie is
non-empty and `session_valid` accepts its SHA-256 hash at `app.state.clock.now()` (read
fresh each request), it sets `request.state.user` to `steve`. A public path with a valid
session still sets the user. Any other path with no user is a 303 to `/signin` and the
route does not run. Each page's `current_user` checks the same rule again.

## Security headers

Every response, including redirects and 404s, sets:

- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Content-Security-Policy: default-src 'self'`
- `Strict-Transport-Security: max-age=31536000`

There are no API doc pages.

## `main()` environment

Read only inside `main()`, in this order:

1. `CAL_MIRROR_TLS_CERT` and `CAL_MIRROR_TLS_KEY`. Either missing or empty exits before a
   store or cipher is built:
   `CAL_MIRROR_TLS_CERT and CAL_MIRROR_TLS_KEY must both be set; cal-mirror never serves plain HTTP`
2. `CAL_MIRROR_DSN`. Missing or empty: `CAL_MIRROR_DSN is not set`.
3. `CAL_MIRROR_SECRET_KEY`, via `cipher_from_env`. A `ValueError` exits with that message.
4. `CAL_MIRROR_PORT`, default `8443`. Not an integer: `CAL_MIRROR_PORT must be an integer`.
5. `CAL_MIRROR_BIND`, or all interfaces when unset or empty.
6. `uvicorn.Server(uvicorn.Config(...)).run()` of `create_app(PgStore(dsn, box))` with that
   host, port, and the two TLS files. A daemon thread checks the mtime and size of both TLS
   files every 60 seconds and, when either changes, asks uvicorn to shut down so the pod
   restarts on the renewed certificate. There is no plain-HTTP listener.
