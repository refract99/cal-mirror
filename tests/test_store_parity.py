import os
from datetime import UTC, datetime, timedelta

import psycopg

from cal_mirror.model import REQ_ARCHIVE, REQ_READY, REQ_RULES, Request
from cal_mirror.store_memory import MemoryStore
from cal_mirror.store_pg import PgStore

DSN = os.environ["CAL_MIRROR_TEST_DSN"]
T = datetime(2026, 11, 2, 18, tzinfo=UTC)


class PrefixBox:
    def encrypt(self, p: str) -> str:
        return "box:" + p

    def decrypt(self, token: str) -> str:
        return token[len("box:") :]


def make_pg() -> PgStore:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return PgStore(DSN, PrefixBox())


def request_sequence(store) -> tuple:
    rid = store.add_request(REQ_ARCHIVE, {"cutoff": "2026-01-01"})
    before = store.get_request(rid)
    store.update_request(
        Request(
            id=rid,
            kind=REQ_RULES,
            state=REQ_READY,
            params={"rules": []},
            result={"moves": 1},
        )
    )
    got = store.get_request(rid)
    return (
        got.kind,
        got.state,
        dict(got.params),
        dict(got.result),
        got.created == before.created,
    )


def session_sequence(store) -> tuple:
    store.create_session("h", T)
    store.create_session("h", T + timedelta(hours=1))
    first = store.session_valid("h", T + timedelta(minutes=30))
    second = store.session_valid("h", T + timedelta(hours=1))
    store.create_session("h", T - timedelta(hours=1))
    third = store.session_valid("h", T - timedelta(minutes=30))
    return (first, second, third)


def test_update_request_parity() -> None:
    expected = ("rules", "ready", {"rules": []}, {"moves": 1}, True)
    assert request_sequence(MemoryStore()) == request_sequence(make_pg()) == expected


def test_create_session_parity() -> None:
    expected = (True, False, False)
    assert session_sequence(MemoryStore()) == session_sequence(make_pg()) == expected
