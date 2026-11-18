#!/bin/sh
# Developer shortcut: the project's three checks, each a plain command run from the
# repository root in the locked environment. The PostgreSQL tests start their own
# throwaway cluster and need `initdb` and `pg_ctl` on PATH (see tests/conftest.py).
set -eu
cd "$(dirname "$0")/.."
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
