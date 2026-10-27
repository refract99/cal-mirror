import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from cal_mirror.model import (
    REQ_ARCHIVE,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    AlertState,
    AuthRecord,
    CalendarInfo,
    Link,
    PlacementEntry,
    Request,
    Rule,
    Settings,
    Status,
)
from cal_mirror.ports import Store, StoreError
from cal_mirror.store_memory import MemoryStore

T = datetime(2026, 11, 3, 13, 30, tzinfo=UTC)


def _entry(id: int = 0, at: datetime = T, source: str = "a/1") -> PlacementEntry:
    return PlacementEntry(id=id, at=at, source=source, calendar_id="c")


def _link(source: str, event_id: str = "e") -> Link:
    return Link(source=source, kind="copy", calendar_id="c", event_id=event_id)


def test_placement_ids_ignore_entry_id():
    s = MemoryStore()
    assert s.add_placement(_entry(id=99)) == 1


def test_placement_second_id():
    s = MemoryStore()
    s.add_placement(_entry(id=99))
    assert s.add_placement(_entry()) == 2


def test_placements_newest_first():
    s = MemoryStore()
    s.add_placement(_entry(at=T + timedelta(hours=1)))
    s.add_placement(_entry(at=T))
    assert [p.id for p in s.list_placements()] == [2, 1]


def test_placements_limit():
    s = MemoryStore()
    s.add_placement(_entry())
    s.add_placement(_entry())
    assert [p.id for p in s.list_placements(limit=1)] == [2]


def test_placements_limit_nonpositive():
    s = MemoryStore()
    s.add_placement(_entry())
    assert s.list_placements(limit=0) == [] and s.list_placements(limit=-3) == []


def test_get_placement_unknown_given_id():
    s = MemoryStore()
    s.add_placement(_entry(id=99))
    assert s.get_placement(99) is None


def test_get_placement_stores_new_id():
    s = MemoryStore()
    s.add_placement(_entry(id=99))
    assert s.get_placement(1) == _entry(id=1)


def test_request_json_round_trip():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {"rules": ({"kind": "organizer"},)})
    assert s.get_request(rid).params == {"rules": [{"kind": "organizer"}]}


def test_request_unencodable_params():
    s = MemoryStore()
    with pytest.raises(StoreError):
        s.add_request(REQ_ARCHIVE, {"x": object()})


def test_request_unencodable_leaves_no_request():
    s = MemoryStore()
    with pytest.raises(StoreError):
        s.add_request(REQ_ARCHIVE, {"x": object()})
    assert s.list_requests() == []


def test_request_defaults():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {})
    got = s.get_request(rid)
    assert (got.id, got.kind, got.state, got.result) == (1, REQ_RULES, REQ_PENDING, {})


def test_request_created_is_aware():
    s = MemoryStore()
    created = s.get_request(s.add_request(REQ_RULES, {})).created
    assert isinstance(created, datetime) and created.tzinfo is not None


def test_request_unknown_is_none():
    assert MemoryStore().get_request(5) is None


def test_update_unknown_request_raises():
    with pytest.raises(StoreError, match="unknown request 42"):
        MemoryStore().update_request(Request(id=42, kind=REQ_RULES))


def test_update_request_unencodable_result():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {})
    with pytest.raises(StoreError):
        s.update_request(dataclasses.replace(s.get_request(rid), result={"x": object()}))


def test_list_requests_by_state():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {})
    s.update_request(dataclasses.replace(s.get_request(rid), state=REQ_READY, result={"moves": 3}))
    assert [r.id for r in s.list_requests(REQ_READY)] == [rid]


def test_list_requests_other_state_empty():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {})
    s.update_request(dataclasses.replace(s.get_request(rid), state=REQ_READY))
    assert s.list_requests(REQ_PENDING) == []


def test_update_request_stores_result_and_keeps_created():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {})
    created = s.get_request(rid).created
    s.update_request(Request(id=rid, kind=REQ_ARCHIVE, state=REQ_READY, result={"moves": 3}))
    got = s.get_request(rid)
    assert (got.kind, got.state, got.result, got.created) == (
        REQ_ARCHIVE,
        REQ_READY,
        {"moves": 3},
        created,
    )


def test_list_requests_oldest_first():
    s = MemoryStore()
    ids = [s.add_request(REQ_RULES, {}) for _ in range(3)]
    assert [r.id for r in s.list_requests()] == ids


def test_request_reads_are_copies():
    s = MemoryStore()
    rid = s.add_request(REQ_RULES, {"a": [1]})
    s.get_request(rid).params["a"].append(2)
    s.list_requests()[0].params["b"] = 1
    assert s.get_request(rid).params == {"a": [1]}


def test_request_params_input_is_copied():
    s = MemoryStore()
    params = {"a": [1]}
    rid = s.add_request(REQ_RULES, params)
    params["a"].append(2)
    assert s.get_request(rid).params == {"a": [1]}


def test_notice_state_returns_copy():
    s = MemoryStore()
    got = s.get_notice_state("a/1")
    got["x"] = "y"
    assert s.get_notice_state("a/1") == {}


def test_notice_state_input_is_copied():
    s = MemoryStore()
    state = {"x": "y"}
    s.set_notice_state("a/1", state)
    state["z"] = "w"
    assert s.get_notice_state("a/1") == {"x": "y"}


def test_notice_state_replaces():
    s = MemoryStore()
    s.set_notice_state("a/1", {"x": "1"})
    s.set_notice_state("a/1", {"y": "2"})
    assert s.get_notice_state("a/1") == {"y": "2"}


def test_rules_input_is_copied():
    s = MemoryStore()
    r1, r2 = Rule(1, "organizer", "a@b", "c"), Rule(2, "title_word", "x", "c")
    rules = [r1]
    s.save_rules(rules)
    rules.append(r2)
    assert s.list_rules() == [r1]


def test_rules_returned_list_is_copy():
    s = MemoryStore()
    s.save_rules([Rule(1, "organizer", "a@b", "c")])
    s.list_rules().clear()
    assert len(s.list_rules()) == 1


def test_rules_keep_order_and_ids():
    s = MemoryStore()
    rules = [Rule(7, "organizer", "a", "c"), Rule(3, "title_word", "b", "c")]
    s.save_rules(rules)
    assert s.list_rules() == rules


def test_save_rules_empty_clears():
    s = MemoryStore()
    s.save_rules([Rule(1, "organizer", "a", "c")])
    s.save_rules([])
    assert s.list_rules() == []


def test_session_validity_boundary():
    s = MemoryStore()
    s.create_session("h", T)
    assert s.session_valid("h", T - timedelta(seconds=1))


def test_session_expired_at_expiry():
    s = MemoryStore()
    s.create_session("h", T)
    assert not s.session_valid("h", T)


def test_session_unknown_invalid():
    assert not MemoryStore().session_valid("h", T)


def test_session_valid_deletes_nothing():
    s = MemoryStore()
    s.create_session("h", T)
    s.session_valid("h", T + timedelta(days=1))
    assert s.session_valid("h", T - timedelta(seconds=1))


def test_session_deleted():
    s = MemoryStore()
    s.create_session("h", T)
    s.delete_session("h")
    assert not s.session_valid("h", T - timedelta(days=1))


def test_session_delete_unknown_ok():
    s = MemoryStore()
    s.delete_session("nope")
    assert not s.session_valid("nope", T)


def test_session_replaced():
    s = MemoryStore()
    s.create_session("h", T)
    s.create_session("h", T + timedelta(hours=1))
    assert s.session_valid("h", T)


def test_links_sorted_by_source():
    s = MemoryStore()
    s.put_link(_link("b/2"))
    s.put_link(_link("a/1"))
    assert [link.source for link in s.list_links()] == ["a/1", "b/2"]


def test_links_sorted_by_code_point():
    s = MemoryStore()
    for source in ("a/1", "B/1", "_/1"):
        s.put_link(_link(source))
    assert [link.source for link in s.list_links()] == ["B/1", "_/1", "a/1"]


def test_put_link_replaces():
    s = MemoryStore()
    s.put_link(_link("a/1", "e1"))
    s.put_link(_link("a/1", "e2"))
    assert s.list_links() == [_link("a/1", "e2")]


def test_get_link_round_trip():
    s = MemoryStore()
    link = _link("a/1")
    s.put_link(link)
    assert s.get_link("a/1") == link


def test_get_link_unknown_is_none():
    assert MemoryStore().get_link("a/1") is None


def test_delete_link():
    s = MemoryStore()
    s.put_link(_link("a/1"))
    s.delete_link("a/1")
    assert s.get_link("a/1") is None


def test_delete_link_unknown_ok():
    s = MemoryStore()
    s.delete_link("a/1")
    assert s.list_links() == []


def test_default_settings():
    assert MemoryStore().get_settings() == Settings()


def test_default_alert_state():
    assert MemoryStore().get_alert_state() == AlertState()


def test_default_status():
    assert MemoryStore().get_status() == Status()


def test_default_auth():
    assert MemoryStore().get_auth() == AuthRecord()


def test_default_rules():
    assert MemoryStore().list_rules() == []


def test_default_calendars():
    assert MemoryStore().list_calendars() == []


def test_default_links_and_placements():
    s = MemoryStore()
    assert s.list_links() == [] and s.list_placements() == [] and s.list_requests() == []


def test_default_none_lookups():
    s = MemoryStore()
    assert (s.get_sync_token("c"), s.get_secret("k"), s.get_meta("m")) == (None, None, None)


def test_default_notice_state():
    assert MemoryStore().get_notice_state("a/1") == {}


def test_settings_round_trip():
    s = MemoryStore()
    new = Settings(main_calendar="m", owner_email="o@x")
    s.save_settings(new)
    assert s.get_settings() == new


def test_alert_state_round_trip():
    s = MemoryStore()
    new = AlertState(active="token", since=T, api_errors=2)
    s.set_alert_state(new)
    assert s.get_alert_state() == new


def test_status_round_trip():
    s = MemoryStore()
    new = Status(last_good_sync=T, failure="api", token_ok=False)
    s.set_status(new)
    assert s.get_status() == new


def test_auth_round_trip():
    s = MemoryStore()
    new = AuthRecord(password_hash="h", totp_enabled=True, failures=1, locked_until=T)
    s.save_auth(new)
    assert s.get_auth() == new


def test_calendars_round_trip_in_order():
    s = MemoryStore()
    items = [CalendarInfo("z"), CalendarInfo("a", primary=True)]
    s.set_calendars(items)
    assert s.list_calendars() == items


def test_calendars_input_is_copied():
    s = MemoryStore()
    items = [CalendarInfo("a")]
    s.set_calendars(items)
    items.append(CalendarInfo("b"))
    assert s.list_calendars() == [CalendarInfo("a")]


def test_calendars_replaced():
    s = MemoryStore()
    s.set_calendars([CalendarInfo("a")])
    s.set_calendars([CalendarInfo("b")])
    assert s.list_calendars() == [CalendarInfo("b")]


def test_sync_token_round_trip():
    s = MemoryStore()
    s.set_sync_token("c", "t1")
    assert s.get_sync_token("c") == "t1"


def test_sync_token_cleared():
    s = MemoryStore()
    s.set_sync_token("c", "t1")
    s.set_sync_token("c", None)
    assert s.get_sync_token("c") is None


def test_sync_token_empty_string_kept():
    s = MemoryStore()
    s.set_sync_token("c", "")
    assert s.get_sync_token("c") == ""


def test_secret_round_trip():
    s = MemoryStore()
    s.set_secret("k", "v")
    assert s.get_secret("k") == "v"


def test_secret_cleared():
    s = MemoryStore()
    s.set_secret("k", "v")
    s.set_secret("k", None)
    assert s.get_secret("k") is None


def test_meta_last_value_wins():
    s = MemoryStore()
    s.set_meta("m", "1")
    s.set_meta("m", "2")
    assert s.get_meta("m") == "2"


def test_stores_do_not_share_state():
    a, b = MemoryStore(), MemoryStore()
    a.save_settings(Settings(main_calendar="m"))
    a.set_secret("k", "v")
    a.put_link(_link("a/1"))
    assert (b.get_settings(), b.get_secret("k"), b.list_links()) == (Settings(), None, [])


def test_stores_do_not_share_placement_counter():
    a, b = MemoryStore(), MemoryStore()
    a.add_placement(_entry())
    a.add_placement(_entry())
    assert b.add_placement(_entry()) == 1


def test_stores_do_not_share_request_counter():
    a, b = MemoryStore(), MemoryStore()
    a.add_request(REQ_RULES, {})
    a.add_request(REQ_RULES, {})
    assert b.add_request(REQ_RULES, {}) == 1


def test_every_store_method_exists_and_is_callable():
    names = [name for name in vars(Store) if not name.startswith("_")]
    s = MemoryStore()
    assert names and all(callable(getattr(s, name, None)) for name in names)
