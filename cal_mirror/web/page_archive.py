import dataclasses
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from cal_mirror.model import (
    REQ_ARCHIVE,
    REQ_CANCELLED,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_PENDING,
    REQ_READY,
)
from cal_mirror.web.deps import current_user, get_clock, get_store, templates

router = APIRouter()

IN_PROGRESS = (REQ_PENDING, REQ_READY, REQ_CONFIRMED)
LIST_LIMIT = 20
REFRESH_STATES = (REQ_PENDING, REQ_CONFIRMED)

User = Annotated[str, Depends(current_user)]
StoreDep = Annotated[Any, Depends(get_store)]
ClockDep = Annotated[Any, Depends(get_clock)]


def _archive_requests(store):
    return [r for r in store.list_requests() if r.kind == REQ_ARCHIVE]


def _render_list(request, store, status_code=200, message=""):
    destinations = store.get_settings().archive_destinations
    recent = sorted(_archive_requests(store), key=lambda r: r.id, reverse=True)[:LIST_LIMIT]
    return templates.TemplateResponse(
        request,
        "archive.html",
        {"destinations": destinations, "requests": recent, "message": message},
        status_code=status_code,
    )


def _load(store, request_id):
    req = store.get_request(request_id)
    if req is None or req.kind != REQ_ARCHIVE:
        raise HTTPException(404)
    return req


def _kept_unbounded(result) -> int:
    raw = result.get("kept_unbounded")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return 0


def _count_rows(counts):
    """Rows of (cells, is_total) for the dry-run table."""
    by_calendar: dict[str, list[tuple[str, int]]] = {}
    for key, n in counts.items():
        calendar, _, year = key.rpartition("/")
        by_calendar.setdefault(calendar, []).append((year, n))
    rows = []
    grand = 0
    for calendar in sorted(by_calendar):
        subtotal = 0
        for year, n in sorted(by_calendar[calendar]):
            rows.append(((calendar, year, n), False))
            subtotal += n
        rows.append(((f"{calendar} total", "", subtotal), True))
        grand += subtotal
    rows.append((("All calendars total", "", grand), True))
    return rows


@router.get("/archive")
def archive_page(request: Request, user: User, store: StoreDep):
    return _render_list(request, store)


@router.post("/archive")
def archive_start(
    request: Request,
    user: User,
    store: StoreDep,
    clock: ClockDep,
    cutoff: Annotated[str, Form()] = "",
    destination: Annotated[str, Form()] = "",
):
    try:
        day = date.fromisoformat(cutoff)
    except ValueError:
        day = None
    if day is None or day >= clock.now().date():
        return _render_list(request, store, 400, "The cutoff must be a date before today.")
    if destination not in store.get_settings().archive_destinations:
        return _render_list(request, store, 400, "Pick one of the archive destinations.")
    if any(r.state in IN_PROGRESS for r in _archive_requests(store)):
        return _render_list(request, store, 409, "An archive is already in progress.")
    new_id = store.add_request(REQ_ARCHIVE, {"cutoff": day.isoformat(), "destination": destination})
    return RedirectResponse(f"/archive/{new_id}", status_code=303)


@router.get("/archive/{request_id}")
def archive_show(request: Request, request_id: int, user: User, store: StoreDep):
    req = _load(store, request_id)
    result = req.result or {}
    counts = result.get("counts") or {}
    rows = _count_rows(counts) if counts else []
    extras = sorted((k, v) for k, v in result.items() if k not in ("counts", "check"))
    headers = {"Refresh": "5"} if req.state in REFRESH_STATES else None
    return templates.TemplateResponse(
        request,
        "archive_request.html",
        {
            "req": req,
            "rows": rows,
            "check_ok": result.get("check") == "ok",
            "kept_unbounded": _kept_unbounded(result),
            "extras": extras,
            "error": result.get("error"),
        },
        headers=headers,
    )


@router.post("/archive/{request_id}/confirm")
def archive_confirm(request_id: int, user: User, store: StoreDep):
    req = _load(store, request_id)
    if req.state != REQ_READY or (req.result or {}).get("check") != "ok":
        raise HTTPException(409, "This archive cannot be confirmed.")
    store.update_request(dataclasses.replace(req, state=REQ_CONFIRMED))
    return RedirectResponse(f"/archive/{request_id}", status_code=303)


@router.post("/archive/{request_id}/cancel")
def archive_cancel(request_id: int, user: User, store: StoreDep):
    req = _load(store, request_id)
    if req.state in (REQ_DONE, REQ_CONFIRMED):
        raise HTTPException(409, "This archive can no longer be cancelled.")
    store.update_request(dataclasses.replace(req, state=REQ_CANCELLED))
    return RedirectResponse(f"/archive/{request_id}", status_code=303)
