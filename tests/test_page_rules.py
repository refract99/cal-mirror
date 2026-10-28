from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import (
    REQ_ARCHIVE,
    REQ_CANCELLED,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    PlacementEntry,
    Request,
    Rule,
    Settings,
)
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_rules import router

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CALENDARS = ("work@x", "club@x")
RULES = [
    Rule(1, "organizer", "a@b.com", "work@x"),
    Rule(2, "guest_domain", "club.org", "club@x"),
    Rule(3, "title_word", "golf", "club@x"),
]
MOVES = "future copies will move to another calendar."


class FakeStore:
    def __init__(self, requests=(), placements=()):
        self.requests = list(requests)
        self.placements = {p.id: p for p in placements}
        self.saved = []

    def list_rules(self):
        return list(RULES)

    def get_settings(self):
        return Settings(secondary_calendars=CALENDARS)

    def get_placement(self, placement_id):
        return self.placements.get(placement_id)

    def add_request(self, kind, params):
        new_id = len(self.requests) + 1
        self.requests.append(Request(id=new_id, kind=kind, params=params, created=NOW))
        return new_id

    def get_request(self, request_id):
        for r in self.requests:
            if r.id == request_id:
                return r
        return None

    def update_request(self, req):
        self.requests = [req if r.id == req.id else r for r in self.requests]

    def list_requests(self, state=None):
        return [r for r in self.requests if state is None or r.state == state]

    def save_rules(self, rules):
        self.saved.append(rules)


def make_client(store, signed_in=True):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(NOW)
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app)


def req(id=1, state=REQ_PENDING, result=None, kind=REQ_RULES):
    return Request(
        id=id,
        kind=kind,
        state=state,
        params={"rules": [{"kind": "title_word", "value": "chess", "calendar_id": "club@x"}]},
        result=result or {},
        created=NOW,
    )


def form(positions=("1", "2", "3", "4"), **extra):
    data = {"count": str(len(positions))}
    for i, pos in enumerate(positions):
        data[f"position_{i}"] = pos
        if i < len(RULES):
            rule = RULES[i]
            data[f"kind_{i}"] = rule.kind
            data[f"value_{i}"] = rule.value
            data[f"calendar_{i}"] = rule.calendar_id
        else:
            data[f"kind_{i}"] = ""
            data[f"value_{i}"] = ""
            data[f"calendar_{i}"] = ""
    data.update(extra)
    return data


def post(client, data):
    return client.post("/rules", data=data, follow_redirects=False)


def proposed(store):
    return [(r["kind"], r["value"], r["calendar_id"]) for r in store.requests[-1].params["rules"]]


def test_reorder_by_position():
    store = FakeStore()
    r = post(make_client(store), form(("3", "2", "1", "4")))
    assert r.status_code == 303
    assert r.headers["location"] == "/rules"
    assert len(store.requests) == 1
    assert store.requests[0].kind == REQ_RULES
    assert store.requests[0].params["rules"][0] == {
        "kind": "title_word",
        "value": "golf",
        "calendar_id": "club@x",
    }
    assert proposed(store) == [
        ("title_word", "golf", "club@x"),
        ("guest_domain", "club.org", "club@x"),
        ("organizer", "a@b.com", "work@x"),
    ]
    assert store.saved == []


def test_equal_positions_keep_row_order():
    store = FakeStore()
    post(make_client(store), form(("2", "2", "1", "4")))
    assert [p[1] for p in proposed(store)] == ["golf", "a@b.com", "club.org"]


def test_delete_drops_row():
    store = FakeStore()
    post(make_client(store), form(delete_1="1"))
    assert [p[1] for p in proposed(store)] == ["a@b.com", "golf"]


def test_delete_everything_is_valid():
    store = FakeStore()
    r = post(make_client(store), form(delete_0="1", delete_1="1", delete_2="1"))
    assert r.status_code == 303
    assert store.requests[0].params == {"rules": []}


def test_add_row_at_position_zero_goes_first():
    store = FakeStore()
    data = form(("1", "2", "3", "0"), kind_3="title_word", value_3="board", calendar_3="work@x")
    post(make_client(store), data)
    assert [p[1] for p in proposed(store)] == ["board", "a@b.com", "club.org", "golf"]


def test_unknown_kind_refused():
    store = FakeStore()
    r = post(make_client(store), form(kind_1="colour"))
    assert r.status_code == 400
    assert "Rule 2: unknown kind." in r.text
    assert store.requests == []


def test_empty_value_refused():
    store = FakeStore()
    r = post(make_client(store), form(value_0="  "))
    assert r.status_code == 400
    assert "Rule 1: the value is empty." in r.text
    assert store.requests == []


def test_unknown_calendar_refused():
    store = FakeStore()
    r = post(make_client(store), form(calendar_0="other@x"))
    assert r.status_code == 400
    assert "Rule 1: pick one of the secondary calendars." in r.text
    assert store.requests == []


def test_bad_position_refused():
    store = FakeStore()
    r = post(make_client(store), form(position_0="x"))
    assert r.status_code == 400
    assert "Rule 1: the position must be a whole number." in r.text
    assert store.requests == []


def test_bad_count_refused():
    store = FakeStore()
    for count in ("abc", "-1", "201", ""):
        r = post(make_client(store), {"count": count})
        assert r.status_code == 400
        assert "Bad form." in r.text
    assert store.requests == []


def test_good_post_cancels_open_requests():
    store = FakeStore([req(1, REQ_PENDING), req(2, REQ_READY), req(3, REQ_DONE)])
    post(make_client(store), form())
    states = {r.id: r.state for r in store.requests}
    assert states == {1: REQ_CANCELLED, 2: REQ_CANCELLED, 3: REQ_DONE, 4: REQ_PENDING}


def test_bad_post_cancels_nothing():
    store = FakeStore([req(1, REQ_PENDING)])
    post(make_client(store), form(kind_0="colour"))
    assert store.requests[0].state == REQ_PENDING
    assert len(store.requests) == 1


def test_ready_shows_warning_and_buttons():
    store = FakeStore([req(1, REQ_READY, {"moves": 3})])
    text = make_client(store).get("/rules").text
    assert f"3 {MOVES}" in text
    assert 'action="/rules/requests/1/confirm"' in text
    assert 'action="/rules/requests/1/cancel"' in text
    assert "Confirm" in text
    assert "Cancel" in text
    assert "chess" in text


def test_ready_singular():
    store = FakeStore([req(1, REQ_READY, {"moves": 1})])
    text = make_client(store).get("/rules").text
    assert "1 future copy will move to another calendar." in text


def test_pending_state_refreshes():
    store = FakeStore([req(1, REQ_PENDING)])
    text = make_client(store).get("/rules").text
    assert "Counting the copies that would move..." in text
    assert '<meta http-equiv="refresh" content="5">' in text
    assert "Confirm" not in text


def test_confirmed_state_refreshes():
    store = FakeStore([req(1, REQ_CONFIRMED)])
    text = make_client(store).get("/rules").text
    assert "Moving copies..." in text
    assert '<meta http-equiv="refresh" content="5">' in text


def test_failed_state_shows_error():
    store = FakeStore([req(1, REQ_FAILED, {"error": "calendar <gone>"})])
    text = make_client(store).get("/rules").text
    assert "The rule change failed: calendar &lt;gone&gt;" in text


def test_cancelled_and_done_show_no_request():
    for state in (REQ_CANCELLED, REQ_DONE):
        store = FakeStore([req(1, state, {"moves": 3})])
        text = make_client(store).get("/rules").text
        assert MOVES not in text
        assert "Counting" not in text
        assert "failed" not in text
        assert "refresh" not in text


def test_current_request_is_highest_id():
    store = FakeStore([req(1, REQ_READY, {"moves": 3}), req(2, REQ_CANCELLED)])
    assert MOVES not in make_client(store).get("/rules").text


def test_form_lists_rules():
    text = make_client(FakeStore()).get("/rules").text
    assert 'name="kind_0"' in text
    assert 'value="a@b.com"' in text
    assert 'name="value_2"' in text
    assert 'name="position_3" value="4"' in text
    assert '<input type="hidden" name="count" value="4">' in text
    assert '<form method="post" action="/rules">' in text


def test_confirm_ready():
    store = FakeStore([req(1, REQ_READY, {"moves": 3})])
    r = make_client(store).post("/rules/requests/1/confirm", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/rules"
    assert store.requests[0].state == REQ_CONFIRMED


def test_confirm_pending_refused():
    store = FakeStore([req(1, REQ_PENDING)])
    r = make_client(store).post("/rules/requests/1/confirm", follow_redirects=False)
    assert r.status_code == 409
    assert "This change cannot be confirmed." in r.text
    assert store.requests[0].state == REQ_PENDING


def test_cancel_ready():
    store = FakeStore([req(1, REQ_READY, {"moves": 3})])
    r = make_client(store).post("/rules/requests/1/cancel", follow_redirects=False)
    assert r.status_code == 303
    assert store.requests[0].state == REQ_CANCELLED


def test_cancel_done_and_confirmed_refused():
    for state in (REQ_DONE, REQ_CONFIRMED):
        store = FakeStore([req(1, state)])
        r = make_client(store).post("/rules/requests/1/cancel", follow_redirects=False)
        assert r.status_code == 409
        assert "This change can no longer be cancelled." in r.text
        assert store.requests[0].state == state


def test_unknown_or_other_kind_is_404():
    store = FakeStore([req(1, REQ_READY, kind=REQ_ARCHIVE)])
    client = make_client(store)
    for action in ("confirm", "cancel"):
        for request_id in (1, 99):
            r = client.post(f"/rules/requests/{request_id}/{action}", follow_redirects=False)
            assert r.status_code == 404
    assert store.requests[0].state == REQ_READY


def placement(id=7, organizer="o@corp.com", domains=("g.com",)):
    return PlacementEntry(
        id=id,
        at=NOW,
        source="a@x/e1",
        calendar_id="work@x",
        title="Team <sync>",
        organizer_email=organizer,
        guest_domains=domains,
    )


def add_row(text):
    return text[text.index('name="kind_3"') :]


def test_from_placement_organizer():
    store = FakeStore(placements=[placement()])
    text = make_client(store).get("/rules?from_placement=7").text
    row = add_row(text)
    assert '<option value="organizer" selected>' in row
    assert 'name="value_3" value="o@corp.com"' in row
    assert "Team &lt;sync&gt;" in text


def test_from_placement_guest_domain():
    store = FakeStore(placements=[placement(organizer="")])
    row = add_row(make_client(store).get("/rules?from_placement=7").text)
    assert '<option value="guest_domain" selected>' in row
    assert 'name="value_3" value="g.com"' in row
    assert "calendar_3" in row
    assert 'work@x" selected' not in row


def test_from_placement_title_word():
    store = FakeStore(placements=[placement(organizer="", domains=())])
    row = add_row(make_client(store).get("/rules?from_placement=7").text)
    assert '<option value="title_word" selected>' in row
    assert 'name="value_3" value=""' in row


def test_from_placement_unknown_is_404():
    store = FakeStore(placements=[placement()])
    assert make_client(store).get("/rules?from_placement=8").status_code == 404


def test_signed_out_redirects():
    r = make_client(FakeStore(), signed_in=False).get("/rules", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"


def test_rules_page_shows_calendar_names_not_ids() -> None:
    from cal_mirror.model import CalendarInfo

    store = FakeStore()
    store.list_calendars = lambda: [
        CalendarInfo(id=CALENDARS[0], summary="Blocksentience", access_role="owner")
    ]
    html = make_client(store).get("/rules").text
    assert f'value="{CALENDARS[0]}"' in html and ">Blocksentience</option>" in html
