"""Web app shell: page routers, session middleware, TLS-only startup."""

import ipaddress
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from cal_mirror.crypto import cipher_from_env
from cal_mirror.ports import Clock, Store, SystemClock
from cal_mirror.store_pg import PgStore
from cal_mirror.web import (
    page_archive,
    page_calendars,
    page_oauth,
    page_rules,
    page_settings,
    page_signin,
    page_status,
)
from cal_mirror.web.deps import SESSION_COOKIE, session_hash, templates
from cal_mirror.web.page_oauth import FlowConfigError

PUBLIC_PATHS = frozenset({"/signin", "/signin/totp", "/setup"})
STATIC_PREFIX = "/static/"
STATIC_DIR = Path(__file__).parent / "static"

PAGES = (
    page_signin,
    page_oauth,
    page_calendars,
    page_settings,
    page_rules,
    page_archive,
    page_status,
)

DEFAULT_BIND = str(ipaddress.IPv4Address(0))

TLS_REQUIRED = (
    "CAL_MIRROR_TLS_CERT and CAL_MIRROR_TLS_KEY must both be set; "
    "cal-mirror never serves plain HTTP"
)

CERT_CHECK_SECONDS = 60

_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'",
    "Strict-Transport-Security": "max-age=31536000",
}


def create_app(store: Store, clock: Clock | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.clock = clock or SystemClock()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for page in PAGES:
        app.include_router(page.router)

    @app.exception_handler(FlowConfigError)
    def flow_config_error(request: Request, exc: FlowConfigError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "oauth.html", {"state": "", "message": str(exc)}, status_code=500
        )

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/status", status_code=303)

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            now = request.app.state.clock.now()
            if request.app.state.store.session_valid(session_hash(token), now):
                request.state.user = "owner"
        if getattr(request.state, "user", None) is None and (
            request.url.path not in PUBLIC_PATHS and not request.url.path.startswith(STATIC_PREFIX)
        ):
            response = RedirectResponse("/signin", status_code=303)
        else:
            response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    return app


def cert_stamp(paths: Sequence[str]) -> tuple:
    stamps = []
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            stamps.append((path, None, None))
        else:
            stamps.append((path, st.st_mtime_ns, st.st_size))
    return tuple(stamps)


def cert_changed(before: tuple, after: tuple) -> bool:
    return before != after


def watch_cert(server, paths: Sequence[str], sleep=time.sleep) -> None:
    before = cert_stamp(paths)
    while True:
        sleep(CERT_CHECK_SECONDS)
        if server.should_exit:
            return
        after = cert_stamp(paths)
        if cert_changed(before, after):
            server.should_exit = True
            return


def main() -> None:
    cert = os.environ.get("CAL_MIRROR_TLS_CERT", "")
    key = os.environ.get("CAL_MIRROR_TLS_KEY", "")
    if not cert or not key:
        raise SystemExit(TLS_REQUIRED)
    dsn = os.environ.get("CAL_MIRROR_DSN", "")
    if not dsn:
        raise SystemExit("CAL_MIRROR_DSN is not set")
    try:
        box = cipher_from_env(os.environ)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        port = int(os.environ.get("CAL_MIRROR_PORT", "8443"))
    except ValueError:
        raise SystemExit("CAL_MIRROR_PORT must be an integer") from None
    host = os.environ.get("CAL_MIRROR_BIND") or DEFAULT_BIND
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(PgStore(dsn, box)),
            host=host,
            port=port,
            ssl_certfile=cert,
            ssl_keyfile=key,
        )
    )
    threading.Thread(
        target=watch_cert, args=(server, (cert, key)), name="cert-watch", daemon=True
    ).start()
    server.run()


if __name__ == "__main__":
    main()
