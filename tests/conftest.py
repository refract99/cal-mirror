"""Shared fixtures: a throwaway PostgreSQL for the tests that need one."""

import os
import shutil
import subprocess
from collections.abc import Iterator

import pytest

DSN_ENV = "CAL_MIRROR_TEST_DSN"
PG_TOOLS = ("initdb", "pg_ctl")


@pytest.fixture(scope="session")
def pg_dsn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A database the test may wipe.

    `CAL_MIRROR_TEST_DSN` names a running server (CI's service container). Without it,
    `initdb` and `pg_ctl` from `PATH` run a cluster in pytest's temporary directory,
    reached through a socket there and never over the network.
    """
    given = os.environ.get(DSN_ENV)
    if given:
        yield given
        return
    tools = {tool: shutil.which(tool) for tool in PG_TOOLS}
    missing = [tool for tool, found in tools.items() if found is None]
    if missing:
        pytest.fail(
            f"PostgreSQL tests need {' and '.join(missing)} on PATH, or {DSN_ENV} set",
            pytrace=False,
        )
    home = tmp_path_factory.mktemp("pg")
    data = str(home / "data")
    initdb = [tools["initdb"], "-D", data, "-U", "test", "--auth=trust", "--no-sync"]
    subprocess.run([*initdb, "-E", "UTF8", "--locale=C"], check=True, capture_output=True)  # noqa: S603
    options = f"-k {home} -c listen_addresses= -c fsync=off -c synchronous_commit=off"
    pg_ctl = [tools["pg_ctl"], "-D", data]
    start = [*pg_ctl, "-l", str(home / "pg.log"), "-w", "-o", options, "start"]
    subprocess.run(start, check=True, capture_output=True)  # noqa: S603
    try:
        yield f"host={home} user=test dbname=postgres"
    finally:
        subprocess.run([*pg_ctl, "-m", "immediate", "stop"], check=False, capture_output=True)  # noqa: S603
