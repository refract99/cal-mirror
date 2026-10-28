import dataclasses
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from cal_mirror.model import (
    REQ_CANCELLED,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    RULE_GUEST_DOMAIN,
    RULE_ORGANIZER,
    RULE_TITLE_WORD,
    Rule,
)
from cal_mirror.model import Request as ChangeRequest
from cal_mirror.rules import validate_rule
from cal_mirror.web.deps import current_user, get_store, templates

router = APIRouter()

KINDS = (RULE_GUEST_DOMAIN, RULE_ORGANIZER, RULE_TITLE_WORD)
MAX_ROWS = 200

User = Annotated[str, Depends(current_user)]
StoreDep = Annotated[Any, Depends(get_store)]


def _current_request(store) -> ChangeRequest | None:
    found = [r for r in store.list_requests() if r.kind == REQ_RULES]
    return max(found, key=lambda r: r.id) if found else None


def _placement_row(placement) -> dict:
    if placement.organizer_email:
        kind, value = RULE_ORGANIZER, placement.organizer_email
    elif placement.guest_domains:
        kind, value = RULE_GUEST_DOMAIN, placement.guest_domains[0]
    else:
        kind, value = RULE_TITLE_WORD, ""
    return {"kind": kind, "value": value, "calendar": ""}


def _calendar_names(store) -> dict[str, str]:
    try:
        return {c.id: c.summary for c in store.list_calendars() if c.summary}
    except AttributeError:
        return {}


def _render(request, store, status_code=200, message="", placement=None):
    rules = store.list_rules()
    rows = [
        {"kind": r.kind, "value": r.value, "calendar": r.calendar_id, "position": i + 1}
        for i, r in enumerate(rules)
    ]
    blank = _placement_row(placement) if placement else {"kind": "", "value": "", "calendar": ""}
    rows.append({**blank, "position": len(rules) + 1})
    current = _current_request(store)
    moves = 0
    if current is not None and current.state == REQ_READY:
        moves = int((current.result or {}).get("moves", 0))
    return templates.TemplateResponse(
        request,
        "rules.html",
        {
            "rows": rows,
            "count": len(rows),
            "kinds": KINDS,
            "calendars": store.get_settings().secondary_calendars,
            "names": _calendar_names(store),
            "message": message,
            "placement": placement,
            "current": current,
            "moves": moves,
            "proposed": list((current.params or {}).get("rules", [])) if current else [],
            "error": (current.result or {}).get("error", "") if current else "",
            "refresh": current is not None and current.state in (REQ_PENDING, REQ_CONFIRMED),
        },
        status_code=status_code,
    )


def _load(store, request_id):
    req = store.get_request(request_id)
    if req is None or req.kind != REQ_RULES:
        raise HTTPException(404)
    return req


@router.get("/rules")
def rules_page(request: Request, user: User, store: StoreDep, from_placement: int | None = None):
    placement = None
    if from_placement is not None:
        placement = store.get_placement(from_placement)
        if placement is None:
            raise HTTPException(404)
    return _render(request, store, placement=placement)


@router.post("/rules")
async def rules_save(request: Request, user: User, store: StoreDep):
    form = await request.form()
    try:
        count = int(form.get("count", ""))
    except ValueError:
        count = -1
    if not 0 <= count <= MAX_ROWS:
        return _render(request, store, 400, "Bad form.")
    calendars = store.get_settings().secondary_calendars
    kept = []
    for i in range(count):
        if f"delete_{i}" in form:
            continue
        kind = str(form.get(f"kind_{i}", ""))
        value = str(form.get(f"value_{i}", "")).strip()
        calendar = str(form.get(f"calendar_{i}", ""))
        if not kind and not value and not calendar:
            continue
        n = i + 1
        if kind not in KINDS:
            return _render(request, store, 400, f"Rule {n}: unknown kind.")
        if not value:
            return _render(request, store, 400, f"Rule {n}: the value is empty.")
        if calendar not in calendars:
            return _render(request, store, 400, f"Rule {n}: pick one of the secondary calendars.")
        try:
            position = int(str(form.get(f"position_{i}", "")))
        except ValueError:
            return _render(request, store, 400, f"Rule {n}: the position must be a whole number.")
        try:
            validate_rule(Rule(id=n, kind=kind, value=value, calendar_id=calendar))
        except ValueError as exc:
            return _render(request, store, 400, f"Rule {n}: {exc}")
        kept.append((position, i, {"kind": kind, "value": value, "calendar_id": calendar}))
    kept.sort(key=lambda row: (row[0], row[1]))
    for req in store.list_requests():
        if req.kind == REQ_RULES and req.state in (REQ_PENDING, REQ_READY):
            store.update_request(dataclasses.replace(req, state=REQ_CANCELLED))
    store.add_request(REQ_RULES, {"rules": [row[2] for row in kept]})
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/requests/{request_id}/confirm")
def rules_confirm(request_id: int, user: User, store: StoreDep):
    req = _load(store, request_id)
    if req.state != REQ_READY:
        raise HTTPException(409, "This change cannot be confirmed.")
    store.update_request(dataclasses.replace(req, state=REQ_CONFIRMED))
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/requests/{request_id}/cancel")
def rules_cancel(request_id: int, user: User, store: StoreDep):
    req = _load(store, request_id)
    if req.state in (REQ_DONE, REQ_CONFIRMED):
        raise HTTPException(409, "This change can no longer be cancelled.")
    store.update_request(dataclasses.replace(req, state=REQ_CANCELLED))
    return RedirectResponse("/rules", status_code=303)
