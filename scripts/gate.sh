#!/bin/sh
# Repo gate for cal-mirror. Slots have no network: uv runs offline from its cache.
# Tests get a throwaway Postgres cluster (userspace binaries in ~/.local/opt/pg18)
# through CAL_MIRROR_TEST_DSN; only the Postgres store's tests use it.
set -eu
cd "$(dirname "$0")/.."
export UV_OFFLINE=1 UV_FROZEN=1 UV_PYTHON_DOWNLOADS=never
/usr/bin/uv sync -q
/usr/bin/uv run --no-sync ruff check .
/usr/bin/uv run --no-sync ruff format --check .
if ls tests/test_*.py >/dev/null 2>&1; then
  PG=$HOME/.local/opt/pg18/usr
  d=$(mktemp -d /tmp/calmirror-pg.XXXXXX)
  trap 'LD_LIBRARY_PATH=$PG/lib $PG/bin/pg_ctl -D "$d/data" -m immediate stop >/dev/null 2>&1; rm -rf "$d"' EXIT
  LD_LIBRARY_PATH=$PG/lib $PG/bin/initdb -D "$d/data" -U test --auth=trust --no-sync -E UTF8 --locale=C >"$d/initdb.log"
  LD_LIBRARY_PATH=$PG/lib $PG/bin/pg_ctl -D "$d/data" -l "$d/pg.log" -w \
    -o "-k $d -c listen_addresses= -c fsync=off -c synchronous_commit=off -c full_page_writes=off" start >/dev/null
  export CAL_MIRROR_TEST_DSN="host=$d user=test dbname=postgres"
  /usr/bin/uv run --no-sync pytest -q -p no:cacheprovider
fi
