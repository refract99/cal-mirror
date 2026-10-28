import dataclasses
import re
import zoneinfo
from datetime import time
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from cal_mirror.model import SYNC_NOW, Settings
from cal_mirror.ports import Store
from cal_mirror.web.deps import current_user, get_store, templates

router = APIRouter()

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
HOURS_RE = re.compile(r"(([01]\d|2[0-3]):[0-5]\d)-(([01]\d|2[0-3]):[0-5]\d)")


def _is_http_url(text: str) -> bool:
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _parse_hours(text: str) -> tuple[time, time] | None:
    match = HOURS_RE.fullmatch(text)
    if match is None:
        return None
    start = time.fromisoformat(match.group(1))
    end = time.fromisoformat(match.group(3))
    return (start, end) if start < end else None


def _format_hours(window: tuple[time, time]) -> str:
    return f"{window[0]:%H:%M}-{window[1]:%H:%M}"


def _values_from_settings(s: Settings) -> dict[str, str]:
    values = {
        "sync_interval_minutes": str(s.sync_interval_minutes),
        "ntfy_server": s.ntfy_server,
        "ntfy_topic": s.ntfy_topic,
        "archive_destinations": "\n".join(s.archive_destinations),
        "owner_email": s.owner_email,
        "time_zone": s.time_zone,
        "web_url": s.web_url,
    }
    for day in range(7):
        window = s.allowed_hours.get(day)
        values[f"allowed_hours_{day}"] = _format_hours(window) if window else ""
    return values


def _render(request: Request, values, checks, error: str, status_code: int):
    context = {
        "values": values,
        "checks": checks,
        "error": error,
        "days": DAYS,
        "notice": "Sync requested. The worker starts it within 15 seconds."
        if request.query_params.get("synced")
        else "",
    }
    return templates.TemplateResponse(request, "settings.html", context, status_code=status_code)


@router.get("/settings")
def show_settings(
    request: Request,
    _user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
):
    s = store.get_settings()
    checks = {"check_no_link": s.check_no_link, "check_hours": s.check_hours}
    return _render(request, _values_from_settings(s), checks, "", 200)


@router.post("/settings/sync-now")
def sync_now(
    _user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
) -> RedirectResponse:
    store.set_meta(SYNC_NOW, "1")
    return RedirectResponse("/settings?synced=1", status_code=303)


@router.post("/settings")
async def save_settings(
    request: Request,
    _user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
):
    form = await request.form()
    names = ["sync_interval_minutes", "ntfy_server", "ntfy_topic", "archive_destinations"]
    names += ["owner_email", "time_zone", "web_url"]
    names += [f"allowed_hours_{day}" for day in range(7)]
    values = {name: str(form.get(name, "")).strip() for name in names}
    checks = {name: name in form for name in ("check_no_link", "check_hours")}

    def fail(message: str):
        return _render(request, values, checks, message, 400)

    try:
        interval = int(values["sync_interval_minutes"])
    except ValueError:
        interval = 0
    if not 1 <= interval <= 1440:
        return fail("Sync interval must be a whole number of minutes from 1 to 1440.")
    if not _is_http_url(values["ntfy_server"]):
        return fail("ntfy server must be an http or https URL.")
    if TOPIC_RE.fullmatch(values["ntfy_topic"]) is None:
        return fail("ntfy topic must be 1 to 64 letters, digits, - or _.")
    lines = [line.strip() for line in values["archive_destinations"].splitlines()]
    destinations = tuple(line for line in lines if line)
    if not all(PurePosixPath(line).is_absolute() for line in destinations):
        return fail("Each archive destination must be an absolute path.")
    email = values["owner_email"]
    local, at, domain = email.partition("@")
    if not (at and local and domain and "@" not in domain) or any(c.isspace() for c in email):
        return fail("Owner email must be an email address.")
    if values["time_zone"] not in zoneinfo.available_timezones():
        return fail("Time zone must be a zone name such as America/Chicago.")
    if not _is_http_url(values["web_url"]):
        return fail("Web URL must be an http or https URL.")
    allowed_hours = {}
    for day, name in enumerate(DAYS):
        text = values[f"allowed_hours_{day}"]
        if not text:
            continue
        window = _parse_hours(text)
        if window is None:
            return fail(f"Allowed hours for {name} must be HH:MM-HH:MM with start before end.")
        allowed_hours[day] = window

    store.save_settings(
        dataclasses.replace(
            store.get_settings(),
            sync_interval_minutes=interval,
            ntfy_server=values["ntfy_server"],
            ntfy_topic=values["ntfy_topic"],
            archive_destinations=destinations,
            owner_email=email,
            time_zone=values["time_zone"],
            web_url=values["web_url"],
            check_no_link=checks["check_no_link"],
            check_hours=checks["check_hours"],
            allowed_hours=allowed_hours,
        )
    )
    return RedirectResponse("/settings", status_code=303)
