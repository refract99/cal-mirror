import io
import json
import logging
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import cal_mirror.worker as worker
from cal_mirror.alerts import NtfyNotifier
from cal_mirror.crypto import generate_key
from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import KIND_BLOCKED, MARKER_KIND, Settings, Status
from cal_mirror.ports import FixedClock, StoreError, SystemClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.worker import (
    REQUEST_SECONDS,
    NullNotifier,
    configure_logging,
    main,
    notifier_for,
    serve,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
START = "2026-11-03T15:00:00Z"
END = "2026-11-03T16:00:00Z"


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append(message)


def _clock() -> FixedClock:
    return FixedClock(NOW)


def _loop(passes: dict[str, int], clock: FixedClock, limit: int):
    def sleep(seconds: float) -> None:
        assert seconds == REQUEST_SECONDS
        clock.advance(seconds)
        passes["n"] += 1

    def stop() -> bool:
        return passes["n"] >= limit

    return sleep, stop


def _patch_steps(monkeypatch: pytest.MonkeyPatch, syncs: list, requests: list) -> None:
    def run_sync(store, cal, notifier, clock) -> None:
        syncs.append(clock.now())

    def run_requests(store, cal, clock, notifier) -> None:
        requests.append(clock.now())

    monkeypatch.setattr(worker, "run_once", run_sync)
    monkeypatch.setattr(worker, "process_requests", run_requests)


def _detach(handler: logging.Handler) -> None:
    logger = logging.getLogger("cal_mirror")
    logger.removeHandler(handler)
    handler.close()
    if not logger.handlers:
        logger.setLevel(logging.NOTSET)


def test_interval_five_runs_twice_across_21_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    store = MemoryStore()
    store.save_settings(Settings(sync_interval_minutes=5))
    syncs: list[datetime] = []
    requests: list[datetime] = []
    _patch_steps(monkeypatch, syncs, requests)
    passes = {"n": 0}
    sleep, stop = _loop(passes, clock, 21)
    serve(store, lambda: object(), lambda _settings: RecordingNotifier(), clock, sleep, stop)
    assert requests == [NOW + timedelta(seconds=15 * i) for i in range(21)]
    assert syncs == [NOW, NOW + timedelta(seconds=300)]


def test_interval_change_applies_on_the_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    store = MemoryStore()
    store.save_settings(Settings(sync_interval_minutes=5))
    syncs: list[datetime] = []
    requests: list[datetime] = []
    _patch_steps(monkeypatch, syncs, requests)
    passes = {"n": 0}

    def sleep(seconds: float) -> None:
        assert seconds == REQUEST_SECONDS
        if passes["n"] == 0:
            store.save_settings(Settings(sync_interval_minutes=1))
        clock.advance(seconds)
        passes["n"] += 1

    def stop() -> bool:
        return passes["n"] >= 5

    serve(store, lambda: object(), lambda _settings: RecordingNotifier(), clock, sleep, stop)
    assert syncs == [NOW, NOW + timedelta(seconds=60)]
    assert len(requests) == 5


def test_missing_calendar_skips_both_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    store = MemoryStore()
    original = Status(
        last_good_sync=NOW,
        failure="google_api",
        token_ok=True,
        counts={"blocked": 2, "copies": 1},
    )
    store.set_status(original)
    syncs: list[datetime] = []
    requests: list[datetime] = []
    _patch_steps(monkeypatch, syncs, requests)
    passes = {"n": 0}
    sleep, stop = _loop(passes, clock, 1)
    serve(store, lambda: None, lambda _settings: RecordingNotifier(), clock, sleep, stop)
    status = store.get_status()
    assert syncs == []
    assert requests == []
    assert status.token_ok is False
    assert status.last_good_sync == original.last_good_sync
    assert status.failure == original.failure
    assert status.counts == {"blocked": 2, "copies": 1}


def test_sync_store_error_is_logged_without_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    store = MemoryStore()
    store.save_settings(Settings(sync_interval_minutes=5))
    requests: list[datetime] = []

    def run_sync(store, cal, notifier, clock) -> None:
        raise StoreError("fixture")

    def run_requests(store, cal, clock, notifier) -> None:
        requests.append(clock.now())

    monkeypatch.setattr(worker, "run_once", run_sync)
    monkeypatch.setattr(worker, "process_requests", run_requests)
    passes = {"n": 0}
    sleep, stop = _loop(passes, clock, 2)
    buf = io.StringIO()
    handler = configure_logging(lambda: [], stream=buf)
    try:
        serve(store, lambda: object(), lambda _settings: RecordingNotifier(), clock, sleep, stop)
        text = buf.getvalue()
        assert "sync failed: StoreError" in text
        assert "fixture" not in text
        assert len(requests) == 2
    finally:
        _detach(handler)


def test_database_down_then_up_sends_two_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    inner = MemoryStore()
    inner.save_settings(Settings(sync_interval_minutes=5, main_calendar="main@x"))
    seen = {"passes": 0}

    class DownStore:
        def get_settings(self):
            seen["passes"] += 1
            if seen["passes"] <= 2:
                raise StoreError("down")
            return inner.get_settings()

        def __getattr__(self, name: str):
            def wrapped(*args, **kwargs):
                if seen["passes"] <= 2:
                    raise StoreError("down")
                return getattr(inner, name)(*args, **kwargs)

            return wrapped

    sent: list[str] = []

    class RecordingNull:
        def send(self, title: str, message: str, click: str = "") -> None:
            sent.append(message)

    monkeypatch.setattr(worker, "NullNotifier", RecordingNull)
    monkeypatch.setattr(worker, "run_once", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "process_requests", lambda *args, **kwargs: None)
    passes = {"n": 0}
    sleep, stop = _loop(passes, clock, 3)
    buf = io.StringIO()
    handler = configure_logging(lambda: [], stream=buf)
    try:
        serve(DownStore(), lambda: object(), notifier_for, clock, sleep, stop)
        assert sent == [
            "cal-mirror failure: database",
            "cal-mirror recovered: database",
        ]
        assert "down" not in buf.getvalue()
    finally:
        _detach(handler)


def test_notifier_for_empty_topic_is_null() -> None:
    found = notifier_for(Settings())
    assert isinstance(found, NullNotifier)


def test_notifier_for_topic_is_ntfy() -> None:
    found = notifier_for(Settings(ntfy_topic="cm-topic", ntfy_server="https://ntfy.example"))
    assert isinstance(found, NtfyNotifier)
    assert found._topic == "cm-topic"
    assert found._server == "https://ntfy.example"
    found._client.close()


def test_null_notifier_logs_and_drops() -> None:
    buf = io.StringIO()
    handler = configure_logging(lambda: [], stream=buf)
    try:
        NullNotifier().send("title", "body", click="https://cal.example")
        text = buf.getvalue()
        assert "ntfy topic not set: message dropped" in text
        assert "body" not in text
    finally:
        _detach(handler)


def test_configure_logging_redacts_secret() -> None:
    buf = io.StringIO()
    v = "fixture-token-1"
    handler = configure_logging(lambda: [v], stream=buf)
    try:
        log = logging.getLogger("cal_mirror.sync")
        log.info("value %s here", v)
        log.info("plain " + v)
        text = buf.getvalue()
        assert "value [redacted] here" in text
        assert "plain [redacted]" in text
        assert v not in text
    finally:
        _detach(handler)


def test_main_exits_when_dsn_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAL_MIRROR_DSN", raising=False)
    with pytest.raises(SystemExit) as raised:
        main()
    assert str(raised.value) == "CAL_MIRROR_DSN is not set"


def test_main_exits_with_cipher_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = tmp_path / "client.json"
    client.write_text("{}\n")
    monkeypatch.setenv("CAL_MIRROR_DSN", "postgresql://localhost/cal_mirror")
    monkeypatch.setenv("CAL_MIRROR_GOOGLE_CLIENT", str(client))
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", "fixture-not-a-key")
    with pytest.raises(SystemExit) as raised:
        main()
    assert str(raised.value) == "invalid secret key"


def _restore_signals(previous: dict[int, signal.Handlers]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _drop_new_handlers(before: list[logging.Handler]) -> None:
    logger = logging.getLogger("cal_mirror")
    for handler in list(logger.handlers):
        if handler not in before:
            logger.removeHandler(handler)
            handler.close()
    if not logger.handlers:
        logger.setLevel(logging.NOTSET)


def test_main_wires_store_serve_and_redacts_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps(
            {"web": {"client_id": "fixture-client-1", "client_secret": "fixture-password-1"}}
        )
    )
    monkeypatch.setenv("CAL_MIRROR_DSN", "postgresql://localhost/cal_mirror")
    monkeypatch.setenv("CAL_MIRROR_GOOGLE_CLIENT", str(client))
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    monkeypatch.delenv("PGPASSWORD", raising=False)
    token_json = json.dumps({"token": "fixture-access-1", "refresh_token": "fixture-refresh-1"})
    seen: dict[str, object] = {}

    class FakeStore:
        def __init__(self) -> None:
            self.value: str | None = None

        def get_secret(self, name: str) -> str | None:
            assert name in ("google_token", "google_client")
            return self.value if name == "google_token" else None

    def fake_pg(dsn: str, box: object) -> FakeStore:
        seen["dsn"] = dsn
        seen["box"] = box
        store = FakeStore()
        seen["store"] = store
        return store

    def fake_serve(store, make_calendar, notifier_factory, clock, sleep, stop) -> None:
        seen["serve"] = (store, make_calendar, notifier_factory, clock, sleep, stop)

    monkeypatch.setattr(worker, "PgStore", fake_pg)
    monkeypatch.setattr(worker, "serve", fake_serve)
    monkeypatch.setattr(worker, "build_service", lambda token, config: {"built": token})
    monkeypatch.setattr(worker, "GoogleCalendar", lambda service: service)
    previous = {
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
        signal.SIGINT: signal.getsignal(signal.SIGINT),
    }
    before = list(logging.getLogger("cal_mirror").handlers)
    try:
        main()
        box = seen["box"]
        assert seen["dsn"] == "postgresql://localhost/cal_mirror"
        assert callable(box.encrypt)
        assert callable(box.decrypt)
        store, make_calendar, factory, clock, sleep, stop = seen["serve"]
        assert store is seen["store"]
        assert factory is notifier_for
        assert isinstance(clock, SystemClock)
        assert stop() is False
        assert make_calendar() is None
        store.value = ""
        assert make_calendar() is None
        store.value = token_json
        built = make_calendar()
        assert built == {"built": token_json}
        handler = logging.getLogger("cal_mirror").handlers[-1]
        buf = io.StringIO()
        handler.setStream(buf)
        log = logging.getLogger("cal_mirror.worker")
        access = "fixture-access-1"
        refresh = "fixture-refresh-1"
        client_secret = "fixture-password-1"
        log.info("value %s here", access)
        log.info("plain " + refresh)
        log.info("value %s here", client_secret)
        text = buf.getvalue()
        assert "value [redacted] here" in text
        assert "plain [redacted]" in text
        assert access not in text
        assert refresh not in text
        assert client_secret not in text
    finally:
        _restore_signals(previous)
        _drop_new_handlers(before)


def test_one_pass_mirrors_a_busy_secondary_event() -> None:
    store = MemoryStore()
    store.save_settings(
        Settings(
            main_calendar="main@x",
            secondary_calendars=("work@x",),
            default_calendar="work@x",
            owner_email="steve@x",
            sync_interval_minutes=5,
        )
    )
    store.set_meta("first_start_done", "1")
    state = FakeCalendar()
    state.add_calendar("main@x", primary=True)
    state.add_calendar("work@x")
    state.add_event(
        "work@x",
        {"summary": "ZebraBusyTitle", "start": {"dateTime": START}, "end": {"dateTime": END}},
    )
    clock = _clock()
    passes = {"n": 0}
    sleep, stop = _loop(passes, clock, 1)
    serve(
        store,
        lambda: GoogleCalendar(FakeService(state)),
        lambda _settings: RecordingNotifier(),
        clock,
        sleep,
        stop,
    )
    inserts = [item for item in state.writes if item["method"] == "insert"]
    assert len(inserts) == 1
    assert inserts[0]["calendarId"] == "main@x"
    assert inserts[0]["body"]["summary"] == "BLOCKED"
    blocked = []
    for item in state.events["main@x"].values():
        private = (item.get("extendedProperties") or {}).get("private") or {}
        if private.get(MARKER_KIND) == KIND_BLOCKED and item.get("status") != "cancelled":
            blocked.append(item)
    assert len(blocked) == 1


def test_sync_now_flag_forces_one_extra_sync_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock()
    store = MemoryStore()
    store.save_settings(Settings(sync_interval_minutes=60))
    syncs: list[datetime] = []
    _patch_steps(monkeypatch, syncs, [])
    passes = {"n": 0}
    base_sleep, stop = _loop(passes, clock, 4)

    def sleep(seconds: float) -> None:
        if passes["n"] == 1:
            store.set_meta("sync_now", "1")
        base_sleep(seconds)

    serve(store, lambda: object(), lambda _settings: RecordingNotifier(), clock, sleep, stop)
    assert syncs == [NOW, NOW + timedelta(seconds=30)]
    assert store.get_meta("sync_now") == ""
