# cal-mirror architecture

- **PRD:** `docs/prd.md` (version 1)
- **Repo:** `cal-mirror`: origin branch `main`
- **Owner:** the owner
- **Status:** built 2026-10-24 (conformance review PASS on 5c8ca56); accepted by the owner 2026-10-27 and merged to `main`

The names below are fixed: a component uses them by these exact names and redefines none.

## Shape

A functional core, wired last. `cal_mirror/model.py` holds frozen dataclasses and constants; `cal_mirror/ports.py` holds the `typing.Protocol` interfaces (store, calendar, notifier, clock, secret box) and their errors. Each feature is a **pure planner**: events plus settings in, a list of `PlannedWrite` (or notices) out, with no store, network or clock call. Adapters implement the ports: Postgres and in-memory stores, the Google adapter, a fake Google service, the ntfy notifier, the cipher. The worker (`cal_mirror/worker.py`) reads changes, runs the planners and applies their writes through the calendar port. The web app (`cal_mirror/web/`) is config only: each page is its own router module that reads and writes the store and never imports the Google adapter. The web app asks the worker for calendar work by writing a `Request` row; the worker answers in the same row.

```
web/page_*.py ──Store──► store_pg (Postgres) ◄──Store── worker.py ──Calendar──► google_api ──► Google
     (requests, settings, rules)                    │  planners: plan_blocked, plan_copy, rules,
                                                    │  checks, plan_move, archive, cleanup, alerts
                                                    └──Notifier──► alerts.NtfyNotifier ──► ntfy
```

## Contracts (wave 0, row A)

**`cal_mirror/model.py`.** All `@dataclass(frozen=True)`; sequences are tuples; `Mapping` fields use `field(default_factory=dict, compare=False)`. Datetimes are timezone-aware UTC; an all-day `start`/`end` is a `datetime.date`.
- `Attendee(email: str, response: str = "needsAction", is_self: bool = False, organizer: bool = False)`; `response` is Google's `responseStatus`.
- `Attachment(file_url: str, title: str = "", mime_type: str = "", file_id: str = "")`.
- `Event(calendar_id: str, id: str, status: str = "confirmed", summary: str = "", description: str = "", location: str = "", start: datetime | date | None = None, end: datetime | date | None = None, all_day: bool = False, recurrence: tuple[str, ...] = (), recurring_event_id: str = "", transparency: str = "opaque", visibility: str = "default", organizer_email: str = "", creator_email: str = "", attendees: tuple[Attendee, ...] = (), attachments: tuple[Attachment, ...] = (), meeting_link: str = "", dial_in: tuple[str, ...] = (), conference: Mapping = {}, private: Mapping[str, str] = {}, updated: datetime | None = None, raw: Mapping = {}, time_zone: str = "")`. `conference` is Google's `conferenceData`, `private` its `extendedProperties.private`, `raw` the Google item as read, `time_zone` its `start.timeZone` (`compare=False`; added by row G12, 2026-10-24: Google requires it on recurring timed writes; mirrors copy the source's zone, `"UTC"` when a recurring event has none).
- Marker constants: `MARKER_SOURCE = "cal_mirror_source"` (value `source_key(event)`), `MARKER_KIND = "cal_mirror_kind"`, `KIND_BLOCKED = "blocked"`, `KIND_COPY = "copy"`, `BLOCKED_TITLE = "BLOCKED"`.
- Functions: `source_key(event) -> str` = `f"{event.calendar_id}/{event.id}"`; `is_mirror(event) -> bool` (`MARKER_SOURCE in event.private`); `is_busy(event) -> bool` (`transparency != "transparent"` and `status != "cancelled"`); `declined_by_self(event) -> bool` (an attendee with `is_self` and `response == "declined"`); `is_meeting(event, owner_email: str) -> bool` (busy, not all-day, at least one attendee whose email is not `owner_email` and not `is_self`).
- `PlannedWrite(op: str, calendar_id: str, event_id: str = "", body: Event | None = None, source: str = "", destination: str = "", reason: str = "")`; `OP_CREATE = "create"`, `OP_UPDATE = "update"`, `OP_DELETE = "delete"`, `OP_MOVE = "move"` (`destination` = target calendar id).
- `Link(source: str, kind: str, calendar_id: str, event_id: str, start: datetime | date | None = None, rule_id: int | None = None)`: one row per mirror event the app wrote.
- `Rule(id: int, kind: str, value: str, calendar_id: str)`; order is list order; `RULE_GUEST_DOMAIN = "guest_domain"`, `RULE_ORGANIZER = "organizer"`, `RULE_TITLE_WORD = "title_word"`.
- `PlacementEntry(id: int, at: datetime, source: str, calendar_id: str, title: str = "", organizer_email: str = "", guest_domains: tuple[str, ...] = ())`: one default placement.
- `Settings(main_calendar: str = "", secondary_calendars: tuple[str, ...] = (), default_calendar: str = "", owner_email: str = "", time_zone: str = "America/Chicago", sync_interval_minutes: int = 5, ntfy_server: str = "https://ntfy.sh", ntfy_topic: str = "", archive_destinations: tuple[str, ...] = (), check_no_link: bool = True, check_hours: bool = True, allowed_hours: Mapping[int, tuple[time, time]] = {}, web_url: str = "", two_factor: bool = False)`; `allowed_hours` keys are weekdays, 0 = Monday; a missing weekday allows no hours.
- `CalendarInfo(id: str, summary: str = "", access_role: str = "", primary: bool = False)`.
- `Notice(source: str, check: str, start: datetime)`; `CHECK_NO_LINK = "no_call_link"`, `CHECK_HOURS = "outside_hours"`.
- `Failure(kind: str, detail: str = "")`; `FAIL_TOKEN = "token"`, `FAIL_API = "google_api"`, `FAIL_DB = "database"`, `FAIL_ARCHIVE = "archive"`. `AlertState(active: str = "", since: datetime | None = None, api_errors: int = 0)`.
- `Status(last_good_sync: datetime | None = None, failure: str = "", token_ok: bool = True, counts: Mapping[str, int] = {})`.
- `AuthRecord(password_hash: str = "", totp_enabled: bool = False, recovery_hashes: tuple[str, ...] = (), failures: int = 0, locked_until: datetime | None = None)`.
- `Request(id: int, kind: str, state: str = "pending", params: Mapping = {}, result: Mapping = {}, created: datetime | None = None)`; kinds `REQ_ARCHIVE = "archive"` (params `cutoff` ISO date, `destination`), `REQ_RULES = "rules"` (params `rules`: list of rule dicts); states `REQ_PENDING = "pending"`, `REQ_READY = "ready"` (dry run in `result`), `REQ_CONFIRMED = "confirmed"`, `REQ_DONE = "done"`, `REQ_FAILED = "failed"`, `REQ_CANCELLED = "cancelled"`.

**`cal_mirror/ports.py`.** Errors: `class CalendarError(Exception)`, `class CalendarAuthError(CalendarError)` (token rejected or expired), `class SyncTokenExpired(CalendarError)` (HTTP 410), `class NotifyError(Exception)`, `class StoreError(Exception)`.
- `Clock`: `now() -> datetime`. Implementations here: `SystemClock`, `FixedClock(at: datetime)` with `advance(seconds: float)`.
- `SecretBox`: `encrypt(plain: str) -> str`, `decrypt(token: str) -> str`.
- `Notifier`: `send(title: str, message: str, click: str = "") -> None`, raises `NotifyError`.
- `Calendar`: `list_calendars() -> list[CalendarInfo]`; `list_changes(calendar_id: str, sync_token: str | None, time_min: datetime | None = None) -> tuple[list[Event], str]` (all pages; returns the next sync token; raises `SyncTokenExpired`); `list_events(calendar_id: str, time_min: datetime | None = None, time_max: datetime | None = None) -> list[Event]`; `get_event(calendar_id: str, event_id: str) -> Event | None`; `apply(write: PlannedWrite) -> str` (the event id written; the only write path).
- `Store`: settings `get_settings() -> Settings`, `save_settings(s)`; rules `list_rules() -> list[Rule]`, `save_rules(rules)` (replaces the ordered list); calendars `list_calendars()`, `set_calendars(items)`; sync `get_sync_token(calendar_id) -> str | None`, `set_sync_token(calendar_id, token: str | None)`; links `get_link(source) -> Link | None`, `put_link(link)`, `delete_link(source)`, `list_links() -> list[Link]`; placements `add_placement(entry) -> int`, `list_placements(limit: int = 100) -> list[PlacementEntry]` (newest first), `get_placement(id) -> PlacementEntry | None`; notices `get_notice_state(source) -> dict[str, str]`, `set_notice_state(source, state: Mapping[str, str])`; alerts `get_alert_state() -> AlertState`, `set_alert_state(a)`; status `get_status() -> Status`, `set_status(s)`; requests `add_request(kind, params) -> int`, `get_request(id) -> Request | None`, `update_request(req)`, `list_requests(state: str | None = None) -> list[Request]`; secrets `get_secret(name) -> str | None`, `set_secret(name, value: str | None)` (names `google_token`, `totp_secret`; encrypted at rest); auth `get_auth() -> AuthRecord`, `save_auth(a)`; sessions `create_session(token_hash, expires: datetime)`, `session_valid(token_hash, now: datetime) -> bool`, `delete_session(token_hash)`; meta `get_meta(key) -> str | None`, `set_meta(key, value: str)`. Store errors raise `StoreError`.

**`cal_mirror/web/deps.py`** (with an empty `cal_mirror/web/__init__.py` and `cal_mirror/web/templates/base.html`): `SESSION_COOKIE = "cal_mirror_session"`; `session_hash(token: str) -> str` (SHA-256 hex); `get_store(request) -> Store` (`request.app.state.store`); `get_clock(request) -> Clock` (`request.app.state.clock`); `current_user(request) -> str` returns `request.state.user` or raises `HTTPException(303, headers={"Location": "/signin"})`; `templates` = `Jinja2Templates` on `cal_mirror/web/templates`. `base.html` has blocks `title` and `content`. Page tests build `FastAPI()`, set `app.state.store` and `app.state.clock`, `include_router(router)` and override `current_user` with `app.dependency_overrides`.

**Google service surface** (C4 calls it, C5 fakes it, no other method is used): `service.calendarList().list(pageToken=...)`; `service.events().list(calendarId, syncToken=None, timeMin=None, timeMax=None, pageToken=None, showDeleted=True, singleEvents=False, maxResults=2500)` → `{"items", "nextPageToken"}` or `{"items", "nextSyncToken"}`, an unknown sync token raises `googleapiclient.errors.HttpError` with status 410; `.insert(calendarId, body, sendUpdates, conferenceDataVersion=1, supportsAttachments=True)`; `.patch(calendarId, eventId, body, sendUpdates, conferenceDataVersion=1, supportsAttachments=True)`; `.delete(calendarId, eventId, sendUpdates)`; `.move(calendarId, eventId, destination, sendUpdates)`; `.get(calendarId, eventId)`; each returns an object with `.execute()`. A rejected token is `google.auth.exceptions.RefreshError` or `HttpError` 401.

## Components

Each writes its module, its tests in a **new** file `tests/test_<module>.py`, and `docs/<module>.md`. Each row's intent page is the full spec; where it is more exact than this table, the intent wins. Check `C` = `/usr/bin/uv run --offline --frozen pytest -q -p no:cacheprovider`.

| Row | Component | Files | Needs | Leaves | Check |
|---|---|---|---|---|---|
| A | contracts | `cal_mirror/model.py`, `cal_mirror/ports.py`, `cal_mirror/web/__init__.py`, `cal_mirror/web/deps.py`, `cal_mirror/web/templates/base.html`, `tests/test_contracts.py` | none | the names above | `C tests/test_contracts.py` |
| B1 | plan_blocked | `cal_mirror/plan_blocked.py` | A | `plan_blocked(changes, links, settings)`, `blocked_body(event, main_calendar)` | `C tests/test_plan_blocked.py` |
| B2 | plan_copy | `cal_mirror/plan_copy.py` | A | `plan_copies(changes, links, settings, place)`, `copy_body(event, calendar_id)`, `details_block(event)` | `C tests/test_plan_copy.py` |
| B3 | rules | `cal_mirror/rules.py` | A | `match_rule(event, rules)`, `choose_calendar(event, rules, default_calendar) -> tuple[str, Rule \| None]`, `rule_from_placement(entry, kind, calendar_id, rule_id, value="")`, `validate_rule(rule)`, `guest_domains(event)` | `C tests/test_rules.py` |
| B4 | checks | `cal_mirror/checks.py` | A | `meeting_failures(event, settings)`, `plan_notices(changes, states, settings, now) -> tuple[list[Notice], dict]`, `notice_text(notice, settings)`, `summary_text(notices, settings)`, `has_call_link(event)` | `C tests/test_checks.py` |
| B5 | plan_move | `cal_mirror/plan_move.py` | A | `plan_moves(copies, now, place)`, `count_moves(copies, now, place)`, `is_future(event, now)` | `C tests/test_plan_move.py` |
| B6 | archive | `cal_mirror/archive.py` | A | `write_archive(events, dest, cutoff)`, `archived_sources(paths)`, `archive_name(year)`, `ArchiveError` | `C tests/test_archive.py` |
| B7 | cleanup | `cal_mirror/cleanup.py` | A | `CleanupPlan`, `plan_cleanup(events, cutoff, owner_email, links)`, `verify_archive(plan, archived)` | `C tests/test_cleanup.py` |
| B8 | alerts | `cal_mirror/alerts.py` | A | `step_alert(state, failure, now) -> tuple[AlertState, str \| None]`, `NtfyNotifier(server, topic, client=None, timeout=10.0)`, `API_ERROR_THRESHOLD` | `C tests/test_alerts.py` |
| C1 | store_pg | `cal_mirror/store_pg.py`, `cal_mirror/migrations/0001_init.sql` | A | `PgStore(dsn, box)`, `migrate(conn)` | `/bin/sh scripts/gate.sh` |
| C2 | store_memory | `cal_mirror/store_memory.py` | A | `MemoryStore` | `C tests/test_store_memory.py` |
| C3 | crypto | `cal_mirror/crypto.py` | A | `Cipher(key)`, `cipher_from_env(environ)`, `KEY_ENV = "CAL_MIRROR_SECRET_KEY"`, `redact(text, secrets)`, `RedactingFilter(secrets)`, `REDACTED`, `generate_key()` | `C tests/test_crypto.py` |
| C4 | google_api | `cal_mirror/google_api.py` | A | `GoogleCalendar(service)`, `event_from_google(calendar_id, item)`, `event_to_google(event)`, `send_write(service, write)`, `build_service(token_json, client_config)`, `SCOPES` | `C tests/test_google_api.py` |
| C5 | fake_google | `cal_mirror/fake_google.py` | A | `FakeService(state=None)`, `FakeCalendar` (calendars, events, `writes` log; seeding `add_calendar`, `add_event`, `edit_event`, `cancel_event`, `add_exception`, `expire_sync_tokens`) | `C tests/test_fake_google.py` |
| D1 | page_signin | `cal_mirror/web/page_signin.py`, templates `signin.html`, `signin_totp.html`, `setup.html`, `account_2fa.html` | A | `router`, `hash_password`, `verify_password`, `MAX_FAILURES = 5`, `LOCK_SECONDS = 900`, `SESSION_SECONDS`, `PENDING_COOKIE` | `C tests/test_page_signin.py` |
| D2 | page_oauth | `cal_mirror/web/page_oauth.py`, `cal_mirror/web/templates/oauth*.html` | A | `router`, `get_flow` (dependency, overridden in tests), `CALENDAR_SCOPE` | `C tests/test_page_oauth.py` |
| D3 | page_calendars, page_settings | `cal_mirror/web/page_calendars.py`, `cal_mirror/web/page_settings.py`, their templates | A | `router` in each | `C tests/test_page_calendars.py tests/test_page_settings.py` |
| D4 | page_rules | `cal_mirror/web/page_rules.py`, `cal_mirror/web/templates/rules*.html` | A | `router` | `C tests/test_page_rules.py` |
| D5 | page_archive | `cal_mirror/web/page_archive.py`, `cal_mirror/web/templates/archive*.html` | A | `router` | `C tests/test_page_archive.py` |
| D6 | page_status | `cal_mirror/web/page_status.py`, `cal_mirror/web/templates/status*.html` | A | `router` | `C tests/test_page_status.py` |
| P | packaging | `Dockerfile.web`, `Dockerfile.worker`, `deploy/k8s/`, `.dockerignore`, `docs/install.md` | A | none | `C tests/test_packaging.py` |
| E1 | worker | `cal_mirror/worker.py` and new helper modules it names | B1–B8, C1–C5 | `run_once(store, calendar, notifier, clock)`, `process_requests(...)`, `main()` | `C tests/test_worker*.py` |
| E2 | web app | `cal_mirror/web/app.py` | D1–D6, C1, C3 | `create_app(store, clock=None)`, `main()` | `C tests/test_web_app.py` |
| F | acceptance | `tests/test_acceptance_*.py`, `docs/README.md` | E1, E2 | none | `/bin/sh scripts/gate.sh` |

Web routes, fixed so pages link to each other without importing each other: `/signin`, `/signin/totp`, `/signout`, `/setup` (first password), `/account/2fa` (D1); `/google`, `/google/connect`, `/google/callback` (D2); `/calendars` (D3), `/settings` (D3); `/rules`, `/rules?from_placement=<id>` (D4); `/archive`, `/archive/<id>` (D5); `/status` (D6). E2 redirects `/` to `/status` and leaves `/signin`, `/setup`, `/signin/totp` public.

Request flow: D4 writes a `REQ_RULES` request with the proposed rules and never saves rules itself; the worker counts the future copies that would move (`count_moves`), sets `result = {"moves": n}` and `REQ_READY`; D4 shows the warning and on confirm sets `REQ_CONFIRMED` (cancel: `REQ_CANCELLED`, nothing else); the worker then saves the rules and applies `plan_moves`. D5 does the same for `REQ_ARCHIVE`: the worker writes the archive, runs `plan_cleanup` and `verify_archive`, sets `result = {"counts": {"<calendar>/<year>": n}, "check": "ok" | "failed"}`; deletes happen only after `REQ_CONFIRMED` and a passed check.

**Environment** (read only in `main()` functions and `get_flow`, never at import): `CAL_MIRROR_DSN` (Postgres), `CAL_MIRROR_SECRET_KEY` (Fernet key, C3), `CAL_MIRROR_GOOGLE_CLIENT` (path to the OAuth client JSON), `CAL_MIRROR_OAUTH_REDIRECT` (HTTPS redirect URI ending `/google/callback`), `CAL_MIRROR_TLS_CERT`, `CAL_MIRROR_TLS_KEY`, `CAL_MIRROR_BIND` (default `0.0.0.0`), `CAL_MIRROR_PORT` (default `8443`). Entry points: `python -m cal_mirror.worker` and `python -m cal_mirror.web.app`.

**Recurring exceptions.** Google lists a changed or cancelled instance of a series as its own item with `recurring_event_id` set and id `<series id>_<suffix>`. A mirror of such an instance is the same instance of the mirror series: event id `<mirror series id>_<suffix>` (the text after the first `_` of the source id), written with `OP_UPDATE` or `OP_DELETE` on that id; it gets its own `Link` keyed by its own source key.

**Store meta keys:** `first_start_done` (`"1"` once the first run finished), `oauth_state` (D2), `recent_notices` (JSON list, newest first, at most 50, of `{"source", "check", "start", "title"}`, written by the worker; the notice link is `<web_url>/status?notice=<url-quoted source>` and D6 shows that notice with its title).

## Gates

- **Repo gate:** `/bin/sh scripts/gate.sh`: `uv sync` offline and frozen, `ruff check .`, `ruff format --check .`, then `pytest -q` (only when `tests/test_*.py` exist) with a throwaway Postgres cluster from `~/.local/opt/pg18` and `CAL_MIRROR_TEST_DSN` exported. Only C1's tests read that variable.
- **Ruff rules:** `E, F, W, I, B, UP, S, BLE`, line length 100. Bites: S101 `assert` outside `tests/`; S608 SQL built with an f-string or `+` (one fixed query string per statement); S105/S106 hard-coded password outside tests; S108 a `/tmp` literal; BLE001 blind `except Exception`; I001 import order; UP modern typing (`X | None`, `list[...]`). Format with `ruff format`.
- **Gate scripts:** `scripts/gate.sh`, protected.
- **Proven:** 2026-10-24 on s1, exit 0, by dev: 0.05 s with no tests; 0.70 s with one Postgres test (Postgres share 0.46 s, 8 runs).

## Protected paths

`pyproject.toml`, `uv.lock`, `.python-version`, `scripts/gate.sh`. No intent edits `conftest.py`, `cal_mirror/__init__.py` or a doc another row owns. Every library is already locked; no part adds one.

## Deviations from the PRD

- **Egress (PRD Constraints: "Outbound: Google APIs and the ntfy server only"):** the NetworkPolicies allow HTTPS (443) to every address, because a k8s NetworkPolicy cannot filter by host name and Google's address ranges change. Accepted as debt by the owner, 2026-10-24 ("11a", review finding 16). Revisit if the cluster gets an FQDN-aware policy engine (Cilium) or an egress proxy.
- **Recurring series moves (PRD "Rule changes move future copies": "Past copies stay where they are."):** a rule change moves a running recurring copy whole, past instances included. Single past events stay where they are, and series entirely in the future move as the PRD says. Splitting a running series would need `plan_copy` to keep the cut on every later sync and a second link for the truncated past copy, in sync, cleanup and the move step. Accepted as a deviation by the owner, 2026-10-24 ("Follow your recommendations on both of these", recommendation 10b; review finding 11; row G10 dropped).

Libraries added to the PRD's list with the owner's yes 2026-10-24 (decision 3): `cryptography`, `uvicorn`, `jinja2`, `python-multipart`, `tzdata`; `psycopg[binary]` is psycopg 3.

## Not built

- Criterion 13 (a week on the owner's account) and 14 (manifests applied on k3s): the owner's review after install.
- The PRD's install-time questions (TLS provider, registry, Postgres server, USB node): manifest settings, answered at install.

## Open items

None.
