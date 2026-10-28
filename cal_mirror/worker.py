"""Schedule the mirror and request runner. Environment is read only in main."""

import json
import logging
import os
import signal
import threading
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO

from cal_mirror.alerts import NtfyNotifier, step_alert
from cal_mirror.crypto import RedactingFilter, cipher_from_env
from cal_mirror.google_api import GoogleCalendar, build_service
from cal_mirror.model import FAIL_DB, SYNC_NOW, AlertState, Failure, Settings
from cal_mirror.ports import (
    Calendar,
    CalendarError,
    Clock,
    Notifier,
    NotifyError,
    Store,
    StoreError,
    SystemClock,
)
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_pg import PgStore
from cal_mirror.sync import RunResult, run_once

log = logging.getLogger("cal_mirror.worker")

REQUEST_SECONDS = 15
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DB_STEPS = frozenset({"settings", "requests", "sync"})


class NullNotifier:
    """Notifier used when no ntfy topic is configured. Sends nothing."""

    def send(self, title: str, message: str, click: str = "") -> None:
        log.info("ntfy topic not set: message dropped")


def notifier_for(settings: Settings) -> Notifier:
    if settings.ntfy_topic == "":
        return NullNotifier()
    return NtfyNotifier(settings.ntfy_server, settings.ntfy_topic)


def configure_logging(
    secrets: Callable[[], Iterable[str]],
    stream: TextIO | None = None,
) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(RedactingFilter(secrets))
    logger = logging.getLogger("cal_mirror")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def _log_failure(step: str, err: BaseException) -> None:
    log.error("%s failed: %s", step, type(err).__name__)


def _db_alert(
    state: AlertState,
    notifier: Notifier,
    failure: Failure | None,
    now: datetime,
    step: str,
) -> AlertState:
    state, message = step_alert(state, failure, now)
    if message is None:
        return state
    try:
        notifier.send("cal-mirror", message)
    except NotifyError as err:
        _log_failure(step, err)
    return state


def _handoff_db(store: Store, mem_state: AlertState) -> tuple[AlertState, str]:
    """Give a memory-owned database alert to the store when the store can hold it."""
    try:
        active = store.get_alert_state().active
    except StoreError as err:
        _log_failure("settings", err)
        return mem_state, "memory"
    if active not in ("", FAIL_DB):
        return mem_state, "memory"
    if active == "":
        try:
            store.set_alert_state(AlertState(active=FAIL_DB, since=mem_state.since))
        except StoreError as err:
            _log_failure("settings", err)
            return mem_state, "memory"
    return AlertState(), "store"


def _open_db(
    store: Store,
    notifier: Notifier,
    mem_state: AlertState,
    now: datetime,
    step: str,
) -> tuple[AlertState, str]:
    """Own a new database failure: the store if it already announced it, else memory."""
    try:
        held = store.get_alert_state().active == FAIL_DB
    except StoreError as err:
        _log_failure(step, err)
        held = False
    if held:
        return mem_state, "store"
    state = _db_alert(mem_state, notifier, Failure(FAIL_DB), now, step)
    return state, "memory"


def _adopt_store_db(store: Store, mem_state: AlertState) -> tuple[AlertState, str]:
    """Drop the memory copy once a later failed run has recorded the same outage."""
    try:
        held = store.get_alert_state().active == FAIL_DB
    except StoreError as err:
        _log_failure("sync", err)
        return mem_state, "memory"
    if held:
        return AlertState(), "store"
    return mem_state, "memory"


def _recover_db(
    store: Store,
    notifier: Notifier,
    mem_state: AlertState,
    db_owner: str,
    now: datetime,
) -> tuple[AlertState, str]:
    """Send one recovery on a clean run, from whichever owner still holds the alert."""
    if db_owner == "memory":
        state = _db_alert(mem_state, notifier, None, now, "sync")
        return state, ""
    if db_owner != "store":
        return mem_state, db_owner
    try:
        active = store.get_alert_state().active
    except StoreError as err:
        _log_failure("sync", err)
        return mem_state, "store"
    if active == "":
        return AlertState(), ""
    if active != FAIL_DB:
        return mem_state, "store"
    try:
        store.set_alert_state(AlertState())
    except StoreError as err:
        _log_failure("sync", err)
        return mem_state, "store"
    state = _db_alert(AlertState(active=FAIL_DB), notifier, None, now, "sync")
    return state, ""


class _RunNotifier:
    """Note run_once's database notices; hold a failure while an alert is open."""

    def __init__(self, inner: Notifier, hold_failure: bool) -> None:
        self._inner = inner
        self._hold_failure = hold_failure
        self.failure_sent = False
        self.recovery_sent = False

    def send(self, title: str, message: str, click: str = "") -> None:
        if message == f"cal-mirror failure: {FAIL_DB}":
            self.failure_sent = True
            if self._hold_failure:
                return
            self._inner.send(title, message, click)
            return
        if message == f"cal-mirror recovered: {FAIL_DB}":
            self.recovery_sent = True
        self._inner.send(title, message, click)


def serve(
    store: Store,
    make_calendar: Callable[[], Calendar | None],
    notifier_factory: Callable[[Settings], Notifier],
    clock: Clock,
    sleep: Callable[[float], object],
    stop: Callable[[], bool],
) -> None:
    """Loop until stop(). Interval is read each pass; the minimum is one minute."""
    last_sync: datetime | None = None
    mem_state = AlertState()
    db_owner = ""
    notifier: Notifier = NullNotifier()
    while not stop():
        step = "settings"
        try:
            settings = store.get_settings()
            if db_owner == "memory":
                mem_state, db_owner = _handoff_db(store, mem_state)
            notifier = notifier_factory(settings)
            step = "calendar"
            cal = make_calendar()
            if cal is None:
                step = "settings"
                store.set_status(replace(store.get_status(), token_ok=False))
            else:
                step = "requests"
                process_requests(store, cal, clock, notifier)
                step = "sync"
                interval = max(1, settings.sync_interval_minutes)
                forced = store.get_meta(SYNC_NOW) == "1"
                if (
                    forced
                    or last_sync is None
                    or clock.now() >= last_sync + timedelta(minutes=interval)
                ):
                    if forced:
                        store.set_meta(SYNC_NOW, "")
                    last_sync = clock.now()
                    run_notifier = _RunNotifier(notifier, hold_failure=db_owner != "")
                    result = run_once(store, cal, run_notifier, clock)
                    failed = isinstance(result, RunResult) and result.failure == FAIL_DB
                    if failed and db_owner == "":
                        if run_notifier.failure_sent:
                            db_owner = "store"
                        else:
                            mem_state, db_owner = _open_db(
                                store, notifier, mem_state, clock.now(), step
                            )
                    elif failed and db_owner == "memory":
                        if run_notifier.failure_sent:
                            mem_state = AlertState()
                            db_owner = "store"
                        else:
                            mem_state, db_owner = _adopt_store_db(store, mem_state)
                    elif not failed:
                        if run_notifier.recovery_sent:
                            mem_state = AlertState()
                            db_owner = ""
                        else:
                            mem_state, db_owner = _recover_db(
                                store, notifier, mem_state, db_owner, clock.now()
                            )
        except StoreError as err:
            _log_failure(step, err)
            if step in _DB_STEPS and db_owner == "":
                mem_state = _db_alert(mem_state, notifier, Failure(FAIL_DB), clock.now(), step)
                db_owner = "memory"
        except CalendarError as err:
            _log_failure(step, err)
        except NotifyError as err:
            _log_failure(step, err)
        except ValueError as err:
            _log_failure(step, err)
        sleep(REQUEST_SECONDS)


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if value == "":
        raise SystemExit(f"{name} is not set")
    return value


def _read_client_file(path: str) -> dict:
    """The optional client file; the client normally comes from the web app's Google page."""
    if path == "":
        return {}
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SystemExit(f"CAL_MIRROR_GOOGLE_CLIENT is unreadable: {type(err).__name__}") from None
    if not isinstance(config, dict):
        raise SystemExit("CAL_MIRROR_GOOGLE_CLIENT is unreadable: not an object")
    return config


def _client_secret(config: object) -> str:
    if not isinstance(config, dict):
        return ""
    client = config.get("web") or config.get("installed") or {}
    if not isinstance(client, dict):
        return ""
    secret = client.get("client_secret", "")
    if not isinstance(secret, str):
        return ""
    return secret


def _remember_google(secrets: list[str], payload: object) -> None:
    del secrets[3:]
    if not isinstance(payload, dict):
        return
    for name in ("token", "refresh_token"):
        value = payload.get(name, "")
        if isinstance(value, str) and value != "":
            secrets.append(value)


def main() -> None:
    dsn = _required("CAL_MIRROR_DSN")
    client_path = os.environ.get("CAL_MIRROR_GOOGLE_CLIENT", "")
    try:
        box = cipher_from_env(os.environ)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    client_config = _read_client_file(client_path)
    store = PgStore(dsn, box)
    secrets = [
        os.environ["CAL_MIRROR_SECRET_KEY"],
        os.environ.get("PGPASSWORD", ""),
        _client_secret(client_config),
    ]
    configure_logging(lambda: list(secrets))

    def make_calendar() -> Calendar | None:
        token = store.get_secret("google_token")
        if token is None or token == "":
            return None
        config = client_config
        stored = store.get_secret("google_client")
        if stored:
            config = json.loads(stored)
            secrets[2:3] = [_client_secret(config)]
        if not config:
            return None
        _remember_google(secrets, json.loads(token))
        return GoogleCalendar(build_service(token, config))

    stop_event = threading.Event()

    def _on_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)
    serve(
        store,
        make_calendar,
        notifier_for,
        SystemClock(),
        stop_event.wait,
        stop_event.is_set,
    )


if __name__ == "__main__":
    main()
