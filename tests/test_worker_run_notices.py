import pytest

import cal_mirror.worker as worker
from cal_mirror.model import FAIL_API, FAIL_DB, AlertState
from cal_mirror.ports import Notifier, StoreError
from cal_mirror.sync import RunResult
from tests.test_worker_alert_once import FAILED, NOW, RECOVERED, PassStore, _calendar, _run


class SinceNowAlertDown(PassStore):
    """Alert writes stamped at the first pass's clock fail; later stamps land."""

    def set_alert_state(self, state: AlertState) -> None:
        if state.since == NOW:
            raise StoreError("fixture-down-1")
        super().set_alert_state(state)


class ConfirmReadDown(PassStore):
    """The confirming read on pass 1 fails once the store holds the database alert."""

    def get_alert_state(self) -> AlertState:
        state = super().get_alert_state()
        if self.pass_n == 1 and state.active == "database":
            raise StoreError("fixture-down-1")
        return state


def test_handoff_write_fails_and_later_run_write_is_one_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SinceNowAlertDown()
    store.down["set_calendars"] = {1, 5}
    sent = _run(monkeypatch, store, 10, _calendar())
    assert (sent, store.get_alert_state()) == ([FAILED, RECOVERED], AlertState())


def test_confirming_read_fails_after_run_once_announced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ConfirmReadDown()
    store.down["set_calendars"] = {1}
    sent = _run(monkeypatch, store, 6, _calendar())
    assert (sent, store.get_alert_state()) == ([FAILED, RECOVERED], AlertState())


def test_recovery_while_memory_owns_is_not_sent_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def run_script(_store: object, _cal: object, notifier: Notifier, _clock: object) -> RunResult:
        calls.append(1)
        if len(calls) == 1:
            return RunResult(failure=FAIL_DB)
        notifier.send("cal-mirror", RECOVERED)
        return RunResult()

    monkeypatch.setattr(worker, "run_once", run_script)
    store = PassStore()
    store.down["set_alert_state"] = set(range(1, 6))
    sent = _run(monkeypatch, store, 6, lambda: object())
    assert sent == [FAILED, RECOVERED]


def test_other_failure_messages_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_script(_store: object, _cal: object, notifier: Notifier, _clock: object) -> RunResult:
        notifier.send("cal-mirror", "cal-mirror failure: google_api")
        return RunResult(failure=FAIL_API)

    monkeypatch.setattr(worker, "run_once", run_script)
    sent = _run(monkeypatch, PassStore(), 2, lambda: object())
    assert sent == ["cal-mirror failure: google_api"]
