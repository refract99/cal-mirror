"""Sign in with Google: link one Google account, unlink it, and sign in with it.

`router` holds the routes below; `cal_mirror.web.app` includes it. The OAuth request is separate
from the calendar grant: scopes `openid` and `email` only, redirect URI
`signin_guard.signin_redirect_uri(request)`, and it never reads or writes `google_token`.

- `POST /account/google/link` (session): 303 to Google.
- `POST /account/google/unlink` (session): forget the link; 303 to `/account/2fa`.
- `POST /signin/google`: 303 to Google while an account is linked.
- `GET /signin/google/callback` (public): finish linking (303 to `/account/2fa`) or signing in
  (303 to `/status` with a session); a refused sign-in answers 401 and is counted.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from google_auth_oauthlib.flow import Flow

from cal_mirror.ports import Store
from cal_mirror.web.deps import get_store

router = APIRouter()

SIGNIN_SCOPES = ("openid", "email")


def get_signin_flow(request: Request, store: Annotated[Store, Depends(get_store)]) -> Flow:
    """FastAPI dependency: the sign-in OAuth flow on the stored client (`load_client`).

    Every route here takes the flow from this dependency, so tests can override it. Raises
    `FlowConfigError` when no client is stored.
    """
    raise NotImplementedError
