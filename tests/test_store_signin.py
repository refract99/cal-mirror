"""PRD-01 R7, R10, R13, R16: sign-in data in both stores, and old settings rows still load."""

from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from cal_mirror.model import GoogleLink, Passkey, Settings
from cal_mirror.store_memory import MemoryStore
from cal_mirror.store_pg import PgStore

T = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class PrefixBox:
    def encrypt(self, p: str) -> str:
        return "box:" + p

    def decrypt(self, token: str) -> str:
        return token[len("box:") :]


def make_pg(dsn: str) -> PgStore:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return PgStore(dsn, PrefixBox())


def passkey_sequence(store) -> tuple:
    first = Passkey("cred-a", "Laptop", b"\xa5\x01\x02public-a", 0, T)
    second = Passkey("cred-b", "Phone", b"\xa5\x01\x02public-b", 7, T + timedelta(days=1))
    empty = store.list_passkeys()
    store.add_passkey(first)
    store.add_passkey(second)
    both = store.list_passkeys()
    store.set_passkey_sign_count("cred-b", 8)
    counted = store.list_passkeys()
    store.delete_passkey("cred-a")
    store.delete_passkey("cred-unknown")
    left = store.list_passkeys()
    return (empty, both, counted, left)


def google_sequence(store) -> tuple:
    empty = store.get_google_link()
    store.set_google_link(GoogleLink(sub="sub-1", email="a@example.com"))
    one = store.get_google_link()
    store.set_google_link(GoogleLink(sub="sub-2", email="b@example.com"))
    replaced = store.get_google_link()
    store.set_google_link(None)
    return (empty, one, replaced, store.get_google_link())


def test_passkey_parity(pg_dsn: str) -> None:
    a = Passkey("cred-a", "Laptop", b"\xa5\x01\x02public-a", 0, T)
    b = Passkey("cred-b", "Phone", b"\xa5\x01\x02public-b", 7, T + timedelta(days=1))
    b8 = Passkey("cred-b", "Phone", b"\xa5\x01\x02public-b", 8, T + timedelta(days=1))
    expected = ([], [a, b], [a, b8], [b8])
    assert passkey_sequence(MemoryStore()) == expected
    assert passkey_sequence(make_pg(pg_dsn)) == expected


def test_google_link_parity(pg_dsn: str) -> None:
    expected = (
        None,
        GoogleLink(sub="sub-1", email="a@example.com"),
        GoogleLink(sub="sub-2", email="b@example.com"),
        None,
    )
    assert google_sequence(MemoryStore()) == expected
    assert google_sequence(make_pg(pg_dsn)) == expected


def test_lock_settings_round_trip(pg_dsn: str) -> None:
    store = make_pg(pg_dsn)
    store.save_settings(Settings(signin_failure_limit=3, signin_lock_seconds=120))
    got = store.get_settings()
    assert (got.signin_failure_limit, got.signin_lock_seconds) == (3, 120)


def test_settings_row_with_two_factor_still_loads(pg_dsn: str) -> None:
    store = make_pg(pg_dsn)
    store.save_settings(Settings(owner_email="me@example.org"))
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        row = conn.execute("SELECT data FROM state WHERE name = 'settings'").fetchone()
        assert row is not None
        data = dict(row[0])
        data.pop("signin_failure_limit", None)
        data.pop("signin_lock_seconds", None)
        data["two_factor"] = True
        conn.execute("UPDATE state SET data = %s WHERE name = 'settings'", (Jsonb(data),))
    got = store.get_settings()
    assert got.owner_email == "me@example.org"
    assert (got.signin_failure_limit, got.signin_lock_seconds) == (5, 900)
