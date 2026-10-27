# cal-mirror docs

## Project

- [prd.md](prd.md): what cal-mirror is for and how it is judged.
- [architecture.md](architecture.md): the design record.
- [roadmap.md](roadmap.md): things to do later and the next release; not a task list.

## Docs

- [alerts.md](alerts.md): the failure alert state machine and the ntfy client.
- [archive.md](archive.md): writing and reading back the per-year archive zips.
- [checks.md](checks.md): pure meeting checks (no call link, outside hours).
- [cleanup.md](cleanup.md): the cleanup planner and what it keeps.
- [contracts.md](contracts.md): shared names every component imports.
- [crypto.md](crypto.md): encrypting stored secrets and redacting them.
- [fake_google.md](fake_google.md): the in-memory Calendar API used by tests.
- [google_api.md](google_api.md): the only module that talks to Google Calendar.
- [google-oauth.md](google-oauth.md): creating the Google OAuth client the app needs.
- [install.md](install.md): installing on k3s, and the environment the commands need.
- [quickstart.md](quickstart.md): running the whole app on one machine with Docker Compose.
- [page_archive.md](page_archive.md): the archive page.
- [page_calendars.md](page_calendars.md): the calendars page.
- [page_oauth.md](page_oauth.md): the Google connect and callback routes.
- [page_rules.md](page_rules.md): the rules page and rule-change requests.
- [page_settings.md](page_settings.md): the settings page.
- [page_signin.md](page_signin.md): setup, sign-in, 2FA and lockout.
- [page_status.md](page_status.md): the status page.
- [plan_blocked.md](plan_blocked.md): planning BLOCKED events on the main calendar.
- [plan_copy.md](plan_copy.md): planning main-to-secondary copies.
- [plan_move.md](plan_move.md): planning moves of future copies on a rule change.
- [requests_runner.md](requests_runner.md): the worker half of rule changes and archives.
- [rules.md](rules.md): rules that choose the secondary calendar for a copy.
- [store_memory.md](store_memory.md): the in-memory store for tests.
- [store_pg.md](store_pg.md): the Postgres store adapter.
- [sync.md](sync.md): one pass of the mirror, `run_once`.
- [web_app.md](web_app.md): the FastAPI app built from the page routers.
- [worker.md](worker.md): the worker loop.

## Run commands

- `/bin/sh scripts/gate.sh`: the repo gate (ruff and format check, then the tests).
- `python -m cal_mirror.worker`: start the worker.
- `python -m cal_mirror.web.app`: start the web app.

The environment both processes need is in [install.md](install.md).

## Acceptance tests

- Criteria 1-5: `tests/test_acceptance_mirror.py`.
- Criteria 6, 7, 10, 11, 12: `tests/test_acceptance_ops.py`.
- Criteria 8, 9: `tests/test_acceptance_web.py`.
- Criteria 13 and 14 are the owner's review after install.
