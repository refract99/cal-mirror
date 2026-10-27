from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import REQ_PENDING, REQ_RULES, Request, Rule, Settings
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


class FakeStore:
    def __init__(self, requests=()):
        self.requests = list(requests)

    def list_rules(self):
        return list(RULES)

    def get_settings(self):
        return Settings(secondary_calendars=CALENDARS)

    def add_request(self, kind, params):
        new_id = len(self.requests) + 1
        self.requests.append(Request(id=new_id, kind=kind, params=params, created=NOW))
        return new_id

    def get_request(self, request_id):
        return next((r for r in self.requests if r.id == request_id), None)

    def update_request(self, req):
        self.requests = [req if r.id == req.id else r for r in self.requests]

    def list_requests(self, state=None):
        return [r for r in self.requests if state is None or r.state == state]


def make_client(store):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(NOW)
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app, follow_redirects=False)


def form(kind, value, calendar, row1_kind=None):
    data = {"count": "4"}
    for i, rule in enumerate(RULES):
        data[f"kind_{i}"] = row1_kind if i == 0 and row1_kind else rule.kind
        data[f"value_{i}"] = rule.value
        data[f"calendar_{i}"] = rule.calendar_id
        data[f"position_{i}"] = str(i + 1)
    data.update(kind_3=kind, value_3=value, calendar_3=calendar, position_3="4")
    return data


def test_multi_word_title_word_refused():
    pending = Request(id=1, kind=REQ_RULES, params={"rules": []}, created=NOW)
    store = FakeStore([pending])
    resp = make_client(store).post("/rules", data=form("title_word", "two words", "work@x"))
    assert resp.status_code == 400
    assert "Rule 4: title word must be one word" in resp.text
    assert len(store.requests) == 1
    assert store.requests[0].state == REQ_PENDING


def test_bare_at_guest_domain_refused():
    store = FakeStore()
    resp = make_client(store).post("/rules", data=form("guest_domain", "@", "club@x"))
    assert resp.status_code == 400
    assert "Rule 4: rule value is empty" in resp.text
    assert store.requests == []


def test_guest_domain_with_at_accepted_unchanged():
    store = FakeStore()
    resp = make_client(store).post("/rules", data=form("guest_domain", "@club.org", "club@x"))
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rules"
    assert store.requests[0].params["rules"][3]["value"] == "@club.org"


def test_single_word_title_word_accepted():
    store = FakeStore()
    resp = make_client(store).post("/rules", data=form("title_word", "board", "work@x"))
    assert resp.status_code == 303
    assert len(store.requests) == 1


def test_page_unknown_kind_check_runs_first():
    store = FakeStore()
    resp = make_client(store).post(
        "/rules", data=form("title_word", "board", "work@x", row1_kind="colour")
    )
    assert resp.status_code == 400
    assert "Rule 1: unknown kind." in resp.text
    assert store.requests == []
