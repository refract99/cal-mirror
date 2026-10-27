import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from cal_mirror.alerts import API_ERROR_THRESHOLD, NtfyNotifier, step_alert
from cal_mirror.model import (
    FAIL_API,
    FAIL_ARCHIVE,
    FAIL_DB,
    FAIL_TOKEN,
    AlertState,
    Failure,
)
from cal_mirror.ports import NotifyError

T0 = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 24, 10, 5, tzinfo=UTC)
T2 = datetime(2026, 10, 24, 10, 10, tzinfo=UTC)
API = Failure(FAIL_API, "HTTP 503")
TOK = Failure(FAIL_TOKEN, "fixture-detail-1")


def run(steps):
    state = AlertState()
    messages = []
    states = []
    for failure, now in steps:
        state, message = step_alert(state, failure, now)
        states.append(state)
        if message is not None:
            messages.append(message)
    return states, messages


def test_threshold_is_two():
    assert API_ERROR_THRESHOLD == 2


def test_first_api_error_counts_without_message():
    assert step_alert(AlertState(), API, T0) == (AlertState(api_errors=1), None)


def test_second_api_error_alerts():
    state, _ = step_alert(AlertState(), API, T0)
    assert step_alert(state, API, T1) == (
        AlertState(active="google_api", since=T1, api_errors=2),
        "cal-mirror failure: google_api",
    )


def test_api_none_api_sends_nothing():
    _, messages = run([(API, T0), (None, T1), (API, T2)])
    assert messages == []


def test_api_none_resets_state():
    states, _ = run([(API, T0), (None, T1)])
    assert states[1] == AlertState()


def test_api_api_api_none_messages():
    _, messages = run([(API, T0), (API, T1), (API, T2), (None, T2)])
    assert messages == ["cal-mirror failure: google_api", "cal-mirror recovered: google_api"]


def test_token_failure_and_recovery_messages():
    _, messages = run([(TOK, T0), (TOK, T1), (None, T2)])
    assert messages == ["cal-mirror failure: token", "cal-mirror recovered: token"]


def test_token_final_state_cleared():
    states, _ = run([(TOK, T0), (TOK, T1), (None, T2)])
    assert states[2] == AlertState()


def test_since_stays_first_time():
    states, _ = run([(TOK, T0), (TOK, T1)])
    assert states[1].since == T0


def test_database_alerts_at_once():
    assert step_alert(AlertState(), Failure(FAIL_DB), T0)[1] == "cal-mirror failure: database"


def test_archive_alerts_at_once():
    assert step_alert(AlertState(), Failure(FAIL_ARCHIVE), T0)[1] == "cal-mirror failure: archive"


def test_token_after_api_count_resets_errors():
    state, message = step_alert(AlertState(api_errors=1), TOK, T0)
    assert (state.active, state.api_errors, message) == ("token", 0, "cal-mirror failure: token")


def test_other_kind_while_active_is_ignored():
    state, _ = step_alert(AlertState(), TOK, T0)
    assert step_alert(state, Failure(FAIL_DB), T1) == (state, None)


def test_recovery_after_ignored_kind():
    state, _ = step_alert(AlertState(), TOK, T0)
    state, _ = step_alert(state, Failure(FAIL_DB), T1)
    assert step_alert(state, None, T2)[1] == "cal-mirror recovered: token"


def test_none_when_idle():
    assert step_alert(AlertState(), None, T0) == (AlertState(), None)


def test_unknown_kind_alerts_at_once():
    assert step_alert(AlertState(), Failure("weird"), T0)[1] == "cal-mirror failure: weird"


def test_messages_hide_detail():
    _, messages = run([(TOK, T0), (None, T1), (API, T1), (API, T2), (None, T2)])
    assert not any("fixture-detail-1" in m or "HTTP 503" in m for m in messages)


class Recorder:
    def __init__(self, base):
        self.base = base
        self.requests = []
        self.status = 200


@pytest.fixture
def ntfy():
    rec = Recorder("")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            rec.requests.append((self.command, self.path, self.headers, body))
            self.send_response(rec.status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rec.base = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield rec
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_send_posts_to_topic(ntfy):
    NtfyNotifier(ntfy.base, "cm-topic").send("cal-mirror", "cal-mirror failure: token")
    assert (ntfy.requests[0][0], ntfy.requests[0][1]) == ("POST", "/cm-topic")


def test_send_headers_and_body(ntfy):
    NtfyNotifier(ntfy.base, "cm-topic").send(
        "cal-mirror", "cal-mirror failure: token", click="https://cal.example/status"
    )
    _, _, headers, body = ntfy.requests[0]
    assert (headers["Title"], headers["Click"], body) == (
        "cal-mirror",
        "https://cal.example/status",
        b"cal-mirror failure: token",
    )


def test_send_without_click_has_no_click_header(ntfy):
    NtfyNotifier(ntfy.base, "cm-topic").send("cal-mirror", "x")
    assert "Click" not in ntfy.requests[0][2]


def test_send_non_ascii_title_is_rfc2047(ntfy):
    NtfyNotifier(ntfy.base, "cm-topic").send("Café", "x")
    assert ntfy.requests[0][2]["Title"] == "=?UTF-8?B?Q2Fmw6k=?="


def test_send_body_is_utf8(ntfy):
    NtfyNotifier(ntfy.base, "cm-topic").send("cal-mirror", "Café ✓")
    assert ntfy.requests[0][3] == "Café ✓".encode()


def test_send_http_error_status(ntfy):
    ntfy.status = 500
    with pytest.raises(NotifyError) as info:
        NtfyNotifier(ntfy.base, "cm-topic").send("cal-mirror", "secret-body")
    text = str(info.value)
    assert "HTTP 500" in text
    assert "secret-body" not in text
    assert "cm-topic" not in text


def test_send_transport_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(NotifyError) as info:
        NtfyNotifier("http://127.0.0.1:1", "cm-topic", client=client).send("t", "m")
    assert str(info.value) == "ntfy send failed: ConnectError"
