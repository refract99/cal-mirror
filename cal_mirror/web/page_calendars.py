import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from cal_mirror.ports import Store
from cal_mirror.web.deps import current_user, get_store, templates

router = APIRouter()

NO_CALENDARS = "The worker has not read your calendars yet. Connect Google and wait one sync."


def _owned(store: Store) -> list:
    return [c for c in store.list_calendars() if c.access_role == "owner"]


def _render(
    request: Request,
    owned: list,
    main: str,
    secondary: list[str],
    default: str,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    context = {
        "calendars": owned,
        "main": main,
        "secondary": secondary,
        "default": default,
        "error": error,
        "empty_message": NO_CALENDARS,
    }
    return templates.TemplateResponse(request, "calendars.html", context, status_code=status_code)


def _first_error(owned_ids: set[str], main: str, secondary: list[str], default: str) -> str:
    if not main:
        return "Pick a main calendar."
    if not secondary:
        return "Pick at least one secondary calendar."
    given = [main, *secondary, *([default] if default else [])]
    if any(calendar_id not in owned_ids for calendar_id in given):
        return "Pick only calendars you own."
    if main in secondary:
        return "The main calendar cannot also be a secondary calendar."
    if not default or default not in secondary:
        return "The default calendar must be one of the secondary calendars."
    return ""


@router.get("/calendars", response_class=HTMLResponse)
def show_calendars(
    request: Request,
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
) -> HTMLResponse:
    settings = store.get_settings()
    return _render(
        request,
        _owned(store),
        settings.main_calendar,
        list(settings.secondary_calendars),
        settings.default_calendar,
    )


@router.post("/calendars")
async def save_calendars(
    request: Request,
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
):
    form = await request.form()
    main = str(form.get("main", "")).strip()
    default = str(form.get("default", "")).strip()
    secondary: list[str] = []
    for value in form.getlist("secondary"):
        calendar_id = str(value).strip()
        if calendar_id and calendar_id not in secondary:
            secondary.append(calendar_id)
    owned = _owned(store)
    error = _first_error({c.id for c in owned}, main, secondary, default)
    if error:
        return _render(request, owned, main, secondary, default, error, status_code=400)
    store.save_settings(
        dataclasses.replace(
            store.get_settings(),
            main_calendar=main,
            secondary_calendars=tuple(secondary),
            default_calendar=default,
        )
    )
    return RedirectResponse("/calendars", status_code=303)
