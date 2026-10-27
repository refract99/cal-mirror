from datetime import UTC, datetime

import pytest

import cal_mirror.worker as worker
from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import FAIL_DB, AlertState, CalendarInfo, Settings
from cal_mirror.ports import FixedClock, StoreError
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import RunResult
from cal_mirror.worker import serve

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
FAILED = "cal-mirror failure: database"
RECOVERED = "cal-mirror recovered: database"


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append(message)


class PassStore(MemoryStore):
    """Memory store whose named methods fail on the listed pass numbers only."""

    def __init__(self) -> None:
        super().__init__()
        self.pass_n = 1
        self.down: dict[str, set[int]] = {}

    def _blocked(self, name: str) -> None:
        if self.pass_n in self.down.get(name, set()):
            raise StoreError("fixture-down-1")

    def get_settings(self) -> Settings:
        self._blocked("get_settings")
        return super().get_settings()

    def set_calendars(self, items: list[CalendarInfo]) -> None:
        self._blocked("set_calendars")
        super().set_calendars(items)

    def set_alert_state(self, state: AlertState) -> None:
        self._blocked("set_alert_state")
        super().set_alert_state(state)


def _calendar():
    fake = FakeCalendar()
    fake.add_calendar("main@x")
    return lambda: GoogleCalendar(FakeService(fake))


def _run(
    monkeypatch: pytest.MonkeyPatch,
    store: PassStore,
    passes: int,
    make_calendar,
    results: list[RunResult] | None = None,
) -> list[str]:
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    queue = list(results) if results is not None else []

    def sleep(seconds: float) -> None:
        clock.advance(seconds)
        store.pass_n += 1

    def stop() -> bool:
        return store.pass_n > passes

    def run_script(*_args: object) -> RunResult:
        return queue.pop(0)

    monkeypatch.setattr(worker, "process_requests", lambda *_args: None)
    if results is not None:
        monkeypatch.setattr(worker, "run_once", run_script)
    store.save_settings(Settings(sync_interval_minutes=1, main_calendar="main@x"))
    store.set_meta("first_start_done", "1")
    serve(store, make_calendar, lambda _settings: notifier, clock, sleep, stop)
    return notifier.sent


def test_memory_announces_once_when_the_first_store_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    store.down["set_calendars"] = {1, 5}
    store.down["set_alert_state"] = {1}
    sent = _run(monkeypatch, store, 10, _calendar())
    assert (sent, store.get_alert_state()) == ([FAILED, RECOVERED], AlertState())


def test_store_announces_once_across_a_later_settings_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    store.down["set_calendars"] = {1}
    store.down["get_settings"] = {2}
    sent = _run(monkeypatch, store, 6, _calendar())
    assert sent == [FAILED, RECOVERED]


def test_settings_outage_then_sync_outage_is_one_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    store.down["get_settings"] = {2}
    store.down["set_calendars"] = {5}
    sent = _run(monkeypatch, store, 10, _calendar())
    assert sent == [FAILED, RECOVERED]


def test_handoff_records_the_failure_without_a_second_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    sent = _run(monkeypatch, store, 2, lambda: object(), [RunResult(failure=FAIL_DB)])
    assert (sent, store.get_alert_state()) == (
        [FAILED],
        AlertState(active=FAIL_DB, since=NOW, api_errors=0),
    )


def test_persisted_token_alert_remains_after_the_database_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    store.set_alert_state(AlertState(active="token", since=NOW))
    sent = _run(
        monkeypatch,
        store,
        6,
        lambda: object(),
        [RunResult(failure=FAIL_DB), RunResult()],
    )
    assert (sent, store.get_alert_state().active) == ([FAILED, RECOVERED], "token")


def test_failed_handoff_write_still_sends_one_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PassStore()
    store.down["set_alert_state"] = set(range(1, 11))
    sent = _run(
        monkeypatch,
        store,
        10,
        lambda: object(),
        [RunResult(failure=FAIL_DB), RunResult(failure=FAIL_DB), RunResult()],
    )
    assert sent == [FAILED, RECOVERED]


def test_missing_token_does_not_recover_on_a_settings_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PassStore()
    store.down["get_settings"] = {2}
    sent = _run(monkeypatch, store, 4, lambda: None)
    assert sent == [FAILED]
