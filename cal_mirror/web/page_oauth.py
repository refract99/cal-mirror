import dataclasses
import json
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from cal_mirror.ports import Store
from cal_mirror.web.deps import current_user, get_store, templates

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

router = APIRouter()

STATE_MISMATCH = "OAuth state does not match"
SIGN_IN_FAILED = "Google sign-in failed"


class FlowConfigError(Exception):
    """The Google OAuth client is not set up; the message is safe to show."""


CLIENT_SECRET = "google_client"  # noqa: S105 (store key name)
NOT_SET_UP = "Enter your Google OAuth client ID and secret below first"
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 (public URL)


def redirect_uri(request: Request) -> str:
    return os.environ.get("CAL_MIRROR_OAUTH_REDIRECT") or f"{request.base_url}google/callback"


def load_client(store: Store) -> dict | None:
    stored = store.get_secret(CLIENT_SECRET)
    if stored:
        try:
            config = json.loads(stored)
        except ValueError:
            raise FlowConfigError(
                "The stored Google client is unreadable; enter it again"
            ) from None
        if isinstance(config, dict):
            return config
        raise FlowConfigError("The stored Google client is unreadable; enter it again")
    path = os.environ.get("CAL_MIRROR_GOOGLE_CLIENT", "")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        raise FlowConfigError(
            f"The Google client file ({path}) is unreadable: {type(err).__name__}"
        ) from None
    return config if isinstance(config, dict) else None


def get_flow(request: Request, store: Annotated[Store, Depends(get_store)]) -> Flow:
    config = load_client(store)
    if config is None:
        raise FlowConfigError(NOT_SET_UP)
    try:
        return Flow.from_client_config(
            config,
            scopes=[CALENDAR_SCOPE],
            redirect_uri=redirect_uri(request),
            autogenerate_code_verifier=False,
        )
    except (ValueError, KeyError) as err:
        raise FlowConfigError(
            f"The Google client is not a Web application OAuth client: {type(err).__name__}"
        ) from None


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
    return templates.TemplateResponse(
        request,
        "oauth.html",
        {
            "state": state,
            "message": "",
            "has_client": load_client(store) is not None,
            "redirect_uri": redirect_uri(request),
        },
    )


@router.post("/google/client", response_model=None)
def google_client(
    request: Request,
    user: Annotated[str, Depends(current_user)],
    store: Annotated[Store, Depends(get_store)],
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        return _error(request, "Both the client ID and the client secret are required")
    config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
        }
    }
    store.set_secret(CLIENT_SECRET, json.dumps(config))
    return RedirectResponse("/google", status_code=303)


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
