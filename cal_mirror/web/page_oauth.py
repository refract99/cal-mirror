import dataclasses
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from cal_mirror.ports import Store
from cal_mirror.web.deps import current_user, get_store, templates

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

router = APIRouter()

STATE_MISMATCH = "OAuth state does not match"
SIGN_IN_FAILED = "Google sign-in failed"


def get_flow() -> Flow:
    client_path = os.environ["CAL_MIRROR_GOOGLE_CLIENT"]
    redirect = os.environ["CAL_MIRROR_OAUTH_REDIRECT"]
    return Flow.from_client_secrets_file(
        client_path,
        scopes=[CALENDAR_SCOPE],
        redirect_uri=redirect,
        autogenerate_code_verifier=False,
    )


def _error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "oauth.html", {"state": "", "message": message}, status_code=400
    )


@router.get("/google", response_class=HTMLResponse)
def google_page(
    request: Request,
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
) -> HTMLResponse:
    token = store.get_secret("google_token")
    if not token:
        state = "Connect"
    elif store.get_status().token_ok:
        state = "Connected"
    else:
        state = "Reconnect"
    return templates.TemplateResponse(request, "oauth.html", {"state": state, "message": ""})


@router.get("/google/connect")
def google_connect(
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
    flow: Annotated[Flow, Depends(get_flow)],
) -> RedirectResponse:
    url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    store.set_meta("oauth_state", state)
    return RedirectResponse(url, status_code=303)


@router.get("/google/callback", response_model=None)
def google_callback(
    request: Request,
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
    flow: Annotated[Flow, Depends(get_flow)],
    state: str | None = None,
    code: str | None = None,
) -> HTMLResponse | RedirectResponse:
    expected = store.get_meta("oauth_state")
    if not state or not expected or not secrets.compare_digest(state.encode(), expected.encode()):
        return _error(request, STATE_MISMATCH)
    if not code:
        return _error(request, SIGN_IN_FAILED)
    try:
        flow.fetch_token(code=code)
        token_json = flow.credentials.to_json()
    except (OAuth2Error, ValueError, Warning):
        return _error(request, SIGN_IN_FAILED)
    store.set_secret("google_token", token_json)
    store.set_status(dataclasses.replace(store.get_status(), token_ok=True))
    store.set_meta("oauth_state", "")
    return RedirectResponse("/google", status_code=303)
