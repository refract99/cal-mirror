from datetime import UTC, datetime
from pathlib import Path

import pytest

import cal_mirror.worker as worker
from cal_mirror.crypto import generate_key
from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import FAIL_API, FAIL_DB, FAIL_TOKEN, Settings
from cal_mirror.ports import FixedClock, StoreError
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import RunResult
from cal_mirror.worker import REQUEST_SECONDS, main, serve

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
UNREADABLE = "CAL_MIRROR_GOOGLE_CLIENT is unreadable: "


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append(message)


class FlakySettingsStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get_settings(self) -> Settings:
        self.calls += 1
        if self.calls == 2:
            raise StoreError("fixture-down-1")
        return super().get_settings()


class DownStore(MemoryStore):
    """Alert-state methods work; every other read of the sync fails."""

    def set_calendars(self, calendars: object) -> None:
        raise StoreError("fixture-down-1")


def _store(cls: type[MemoryStore] = MemoryStore) -> MemoryStore:
    store = cls()
    store.save_settings(Settings(sync_interval_minutes=1, main_calendar="main@x"))
    return store


def _run(
    monkeypatch: pytest.MonkeyPatch,
    store: MemoryStore,
    results: list[RunResult],
    passes: int,
) -> list[str]:
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    queue = list(results)
    done = {"n": 0}

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        done["n"] += 1

    monkeypatch.setattr(worker, "process_requests", lambda *args: None)
    monkeypatch.setattr(worker, "run_once", lambda *args: queue.pop(0))
    serve(store, lambda: object(), lambda _s: notifier, clock, sleep, lambda: done["n"] >= passes)
    assert REQUEST_SECONDS == 15
    return notifier.sent


FAILED = "cal-mirror failure: database"
RECOVERED = "cal-mirror recovered: database"
DB_THEN_OK = [RunResult(failure=FAIL_DB), RunResult(failure=FAIL_DB), RunResult()]


def test_database_result_alerts_once_and_holds_until_a_clean_run(monkeypatch) -> None:
    assert _run(monkeypatch, _store(), list(DB_THEN_OK), 8) == [FAILED]


def test_clean_run_clears_the_database_alert(monkeypatch) -> None:
    assert _run(monkeypatch, _store(), list(DB_THEN_OK), 10) == [FAILED, RECOVERED]


def test_token_and_api_results_send_nothing(monkeypatch) -> None:
    results = [RunResult(failure=FAIL_API), RunResult(failure=FAIL_TOKEN), RunResult()]
    assert _run(monkeypatch, _store(), results, 10) == []


def test_settings_error_does_not_end_a_sync_database_alert(monkeypatch) -> None:
    results = [RunResult(failure=FAIL_DB), RunResult()]
    sent = _run(monkeypatch, _store(FlakySettingsStore), results, 6)
    assert sent == [FAILED, RECOVERED]


def test_store_alert_and_worker_alert_send_one_message(monkeypatch) -> None:
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    store = _store(DownStore)
    done = {"n": 0}

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        done["n"] += 1

    monkeypatch.setattr(worker, "process_requests", lambda *args: None)

    def make_calendar() -> GoogleCalendar:
        return GoogleCalendar(FakeService(FakeCalendar()))

    serve(store, make_calendar, lambda _s: notifier, clock, sleep, lambda: done["n"] >= 6)
    assert notifier.sent == [FAILED]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CAL_MIRROR_DSN", "postgresql://localhost/cal_mirror")
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    calls: list[object] = []
    monkeypatch.setattr(worker, "PgStore", lambda *args: calls.append(args))
    monkeypatch.setattr(worker, "serve", lambda *args: calls.append(args))
    return calls


def _exit(monkeypatch: pytest.MonkeyPatch, path: Path) -> pytest.ExceptionInfo[SystemExit]:
    monkeypatch.setenv("CAL_MIRROR_GOOGLE_CLIENT", str(path))
    with pytest.raises(SystemExit) as raised:
        main()
    return raised


def test_missing_client_file(monkeypatch, tmp_path, env) -> None:
    raised = _exit(monkeypatch, tmp_path / "absent.json")
    assert str(raised.value) == UNREADABLE + "FileNotFoundError"


def test_missing_client_file_calls_nothing_else(monkeypatch, tmp_path, env) -> None:
    raised = _exit(monkeypatch, tmp_path / "absent.json")
    assert env == []
    assert raised.value.__suppress_context__ is True


def test_directory_client_path(monkeypatch, tmp_path, env) -> None:
    raised = _exit(monkeypatch, tmp_path)
    assert str(raised.value) == UNREADABLE + "IsADirectoryError"
    assert env == []


def test_empty_client_file(monkeypatch, tmp_path, env) -> None:
    path = tmp_path / "client.json"
    path.write_text("")
    raised = _exit(monkeypatch, path)
    assert str(raised.value) == UNREADABLE + "JSONDecodeError"
    assert env == []
    assert raised.value.__suppress_context__ is True


def test_partial_client_file_hides_content(monkeypatch, tmp_path, env) -> None:
    path = tmp_path / "client.json"
    path.write_text('{"web": {"client_secret": "fixture-client-secret-1"')
    raised = _exit(monkeypatch, path)
    assert str(raised.value) == UNREADABLE + "JSONDecodeError"
    assert "fixture-client-secret-1" not in str(raised.value)
    assert env == []
    assert raised.value.__suppress_context__ is True


def test_non_utf8_client_file(monkeypatch, tmp_path, env) -> None:
    path = tmp_path / "client.json"
    path.write_bytes(b"\xff\xfe\x00")
    raised = _exit(monkeypatch, path)
    assert str(raised.value) == UNREADABLE + "UnicodeDecodeError"
    assert env == []
    assert raised.value.__suppress_context__ is True


def test_array_client_file(monkeypatch, tmp_path, env) -> None:
    path = tmp_path / "client.json"
    path.write_text("[]")
    raised = _exit(monkeypatch, path)
    assert str(raised.value) == UNREADABLE + "not an object"
    assert env == []


def test_string_client_file(monkeypatch, tmp_path, env) -> None:
    path = tmp_path / "client.json"
    path.write_text('"fixture-text-1"')
    raised = _exit(monkeypatch, path)
    assert str(raised.value) == UNREADABLE + "not an object"
    assert env == []
