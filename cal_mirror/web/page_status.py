import json
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request

from cal_mirror.model import CHECK_HOURS, CHECK_NO_LINK
from cal_mirror.web.deps import current_user, get_store, templates

router = APIRouter()

PLACEMENT_LIMIT = 50
CHECK_WORDS = {CHECK_NO_LINK: "no call link", CHECK_HOURS: "outside hours"}

User = Annotated[str, Depends(current_user)]
StoreDep = Annotated[Any, Depends(get_store)]


def _format_time(t: datetime, zone: ZoneInfo) -> str:
    t = t.astimezone(zone)
    return f"{t:%a %Y-%m-%d} {t.hour % 12 or 12}:{t:%M %p %Z}"


def _load_notices(store) -> list[dict]:
    raw = store.get_meta("recent_notices")
    if raw is None:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _notice_view(item: dict, zone: ZoneInfo) -> dict[str, str]:
    start = str(item.get("start", ""))
    try:
        start = _format_time(datetime.fromisoformat(start), zone)
    except ValueError:
        pass
    check = str(item.get("check", ""))
    return {
        "source": str(item.get("source", "")),
        "title": str(item.get("title", "")),
        "start": start,
        "check": CHECK_WORDS.get(check, check),
    }


@router.get("/status")
def status_page(
    request: Request,
    user: User,
    store: StoreDep,
    notice: str | None = None,
):
    settings = store.get_settings()
    status = store.get_status()
    placements = store.list_placements(PLACEMENT_LIMIT)
    zone = ZoneInfo(settings.time_zone)
    notices = [_notice_view(item, zone) for item in _load_notices(store)]
    context = {
        "last_good_sync": (
            _format_time(status.last_good_sync, zone) if status.last_good_sync else "never"
        ),
        "failure": status.failure or "none",
        "token_ok": status.token_ok,
        "counts": sorted(status.counts.items()),
        "placements": [
            {
                "id": p.id,
                "at": _format_time(p.at, zone),
                "title": p.title,
                "organizer_email": p.organizer_email,
                "guest_domains": ", ".join(p.guest_domains),
                "calendar_id": p.calendar_id,
            }
            for p in placements
        ],
        "notices": notices,
        "show_notice": notice is not None,
        "matched": [n for n in notices if n["source"] == notice] if notice is not None else [],
    }
    return templates.TemplateResponse(request, "status.html", context)
