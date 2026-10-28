import hashlib
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from cal_mirror import __version__
from cal_mirror.ports import Clock, Store

SESSION_COOKIE = "cal_mirror_session"


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


def current_user(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(303, headers={"Location": "/signin"})
    return user


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["app_version"] = __version__
