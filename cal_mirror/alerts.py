"""Failure alert state machine and ntfy client."""

import base64
from datetime import datetime

import httpx

from cal_mirror.model import FAIL_API, AlertState, Failure
from cal_mirror.ports import NotifyError

API_ERROR_THRESHOLD = 2


def step_alert(
    state: AlertState, failure: Failure | None, now: datetime
) -> tuple[AlertState, str | None]:
    if state.active != "":
        if failure is not None:
            return state, None
        return AlertState(), f"cal-mirror recovered: {state.active}"
    if failure is None:
        return AlertState(), None
    if failure.kind == FAIL_API:
        n = state.api_errors + 1
        if n >= API_ERROR_THRESHOLD:
            return (
                AlertState(active=FAIL_API, since=now, api_errors=n),
                "cal-mirror failure: google_api",
            )
        return AlertState(api_errors=n), None
    return (
        AlertState(active=failure.kind, since=now, api_errors=0),
        f"cal-mirror failure: {failure.kind}",
    )


class NtfyNotifier:
    def __init__(
        self,
        server: str,
        topic: str,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._server = server
        self._topic = topic
        self._timeout = timeout
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def send(self, title: str, message: str, click: str = "") -> None:
        url = f"{self._server.rstrip('/')}/{self._topic}"
        if not title.isascii():
            encoded = base64.b64encode(title.encode("utf-8")).decode("ascii")
            title = f"=?UTF-8?B?{encoded}?="
        headers = {"Title": title}
        if click != "":
            headers["Click"] = click
        try:
            response = self._client.post(
                url,
                content=message.encode("utf-8"),
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"ntfy send failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise NotifyError(f"ntfy send failed: HTTP {response.status_code}")
