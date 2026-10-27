import re
from datetime import UTC, datetime

import pytest

from cal_mirror.model import Attendee, Event, PlacementEntry, Rule
from cal_mirror.rules import (
    choose_calendar,
    guest_domains,
    match_rule,
    rule_from_placement,
    validate_rule,
)


def ev(**kwargs) -> Event:
    return Event(calendar_id="main", id="e1", **kwargs)


def rule(kind: str, value: str, calendar_id: str, rule_id: int = 1) -> Rule:
    return Rule(id=rule_id, kind=kind, value=value, calendar_id=calendar_id)


def entry(**kwargs) -> PlacementEntry:
    base = {
        "id": 7,
        "at": datetime(2026, 10, 24, tzinfo=UTC),
        "source": "main/e1",
        "calendar_id": "dflt",
        "title": "Acme Sync",
        "organizer_email": "Pat@Acme.com",
        "guest_domains": ("acme.com", "x.org"),
    }
    base.update(kwargs)
    return PlacementEntry(**base)


def err(message: str) -> str:
    return re.escape(message)


def test_title_word_matches_whole_word():
    assert match_rule(ev(summary="Weekly sync"), [rule("title_word", "sync", "work")]) is not None


def test_title_word_not_inside_word():
    assert match_rule(ev(summary="Async review"), [rule("title_word", "sync", "work")]) is None


def test_title_word_case_insensitive_before_punctuation():
    assert match_rule(ev(summary="SYNC-up"), [rule("title_word", "sync", "work")]) is not None


def test_title_word_not_plural():
    assert match_rule(ev(summary="syncs"), [rule("title_word", "sync", "work")]) is None


def test_title_word_is_escaped():
    assert match_rule(ev(summary="c++ talk"), [rule("title_word", "c++", "work")]) is not None


def test_guest_domain_with_at_and_case():
    event = ev(attendees=(Attendee("Bob@Acme.COM"),))
    assert match_rule(event, [rule("guest_domain", "@acme.com", "work")]) is not None


def test_guest_domain_value_case():
    event = ev(attendees=(Attendee("Bob@Acme.COM"),))
    assert match_rule(event, [rule("guest_domain", "ACME.com", "work")]) is not None


def test_guest_domain_no_subdomain_match():
    event = ev(attendees=(Attendee("bob@eng.acme.com"),))
    assert match_rule(event, [rule("guest_domain", "acme.com", "work")]) is None


def test_guest_domain_no_suffix_match():
    event = ev(attendees=(Attendee("bob@notacme.com"),))
    assert match_rule(event, [rule("guest_domain", "acme.com", "work")]) is None


def test_organizer_email_is_not_a_guest():
    event = ev(organizer_email="pat@acme.com")
    assert match_rule(event, [rule("guest_domain", "acme.com", "work")]) is None


def test_organizer_attendee_is_not_a_guest():
    event = ev(
        attendees=(Attendee("pat@acme.com", organizer=True), Attendee("me@home.com", is_self=True))
    )
    assert match_rule(event, [rule("guest_domain", "acme.com", "work")]) is None


def test_guest_domains_of_organizer_and_self_is_empty():
    event = ev(
        attendees=(Attendee("pat@acme.com", organizer=True), Attendee("me@home.com", is_self=True))
    )
    assert guest_domains(event) == ()


def test_guest_domains_lowercase_unique_skips_no_at():
    event = ev(
        attendees=(
            Attendee("a@X.com"),
            Attendee("b@x.com"),
            Attendee("c@y.org"),
            Attendee("nodomain"),
        )
    )
    assert guest_domains(event) == ("x.com", "y.org")


def test_organizer_matches_trimmed_case_insensitive():
    event = ev(organizer_email="Pat@Acme.com")
    assert match_rule(event, [rule("organizer", " pat@acme.com ", "work")]) is not None


def test_organizer_different_address_does_not_match():
    event = ev(organizer_email="Pat@Acme.com")
    assert match_rule(event, [rule("organizer", "pat@acme.co", "work")]) is None


def test_first_match_wins():
    event = ev(summary="Weekly sync", organizer_email="pat@acme.com")
    r2 = rule("title_word", "sync", "a", 2)
    r3 = rule("organizer", "pat@acme.com", "b", 3)
    assert choose_calendar(event, [r2, r3], "default-cal") == ("a", r2)


def test_first_match_wins_reversed():
    event = ev(summary="Weekly sync", organizer_email="pat@acme.com")
    r2 = rule("title_word", "sync", "a", 2)
    r3 = rule("organizer", "pat@acme.com", "b", 3)
    assert choose_calendar(event, [r3, r2], "default-cal") == ("b", r3)


def test_no_match_uses_default():
    event = ev(summary="Lunch")
    assert choose_calendar(event, [rule("title_word", "sync", "a")], "default-cal") == (
        "default-cal",
        None,
    )


def test_empty_rules_use_default():
    assert choose_calendar(ev(summary="Lunch"), [], "default-cal") == ("default-cal", None)


def test_empty_default_calendar():
    assert choose_calendar(ev(summary="Lunch"), [], "") == ("", None)


def test_invalid_rules_are_skipped():
    rules = [
        Rule(1, "colour", "x", "a"),
        Rule(2, "title_word", "", "b"),
        Rule(3, "title_word", "sync", "c"),
    ]
    assert match_rule(ev(summary="Weekly sync"), rules) == rules[2]


def test_validate_unknown_kind():
    with pytest.raises(ValueError, match=err("unknown rule kind: colour")):
        validate_rule(Rule(1, "colour", "x", "a"))


def test_validate_blank_value():
    with pytest.raises(ValueError, match=err("rule value is empty")):
        validate_rule(Rule(1, "organizer", "  ", "a"))


def test_validate_guest_domain_only_at():
    with pytest.raises(ValueError, match=err("rule value is empty")):
        validate_rule(Rule(1, "guest_domain", "@", "a"))


def test_validate_title_word_with_space():
    with pytest.raises(ValueError, match=err("title word must be one word")):
        validate_rule(Rule(1, "title_word", "weekly sync", "a"))


def test_validate_blank_calendar():
    with pytest.raises(ValueError, match=err("rule calendar is empty")):
        validate_rule(Rule(1, "title_word", "sync", ""))


def test_validate_good_rule_returns_none():
    assert validate_rule(Rule(1, "title_word", "sync", "a")) is None


def test_placement_guest_domain_default_first():
    assert rule_from_placement(entry(), "guest_domain", "work", 4) == Rule(
        4, "guest_domain", "acme.com", "work"
    )


def test_placement_guest_domain_value_normalized():
    assert rule_from_placement(entry(), "guest_domain", "work", 4, "@X.org").value == "x.org"


def test_placement_organizer():
    assert rule_from_placement(entry(), "organizer", "work", 5).value == "pat@acme.com"


def test_placement_title_word():
    assert rule_from_placement(entry(), "title_word", "work", 6, "sync").value == "sync"


def test_placement_title_word_not_in_title():
    with pytest.raises(ValueError, match=err("word not in title: async")):
        rule_from_placement(entry(), "title_word", "work", 6, "async")


def test_placement_title_word_required():
    with pytest.raises(ValueError, match=err("title word is required")):
        rule_from_placement(entry(), "title_word", "work", 6)


def test_placement_unknown_kind():
    with pytest.raises(ValueError, match=err("unknown rule kind: colour")):
        rule_from_placement(entry(), "colour", "work", 6)


def test_placement_no_guest_domain():
    with pytest.raises(ValueError, match=err("placement has no guest domain")):
        rule_from_placement(entry(guest_domains=()), "guest_domain", "work", 4)


def test_placement_no_organizer():
    with pytest.raises(ValueError, match=err("placement has no organizer")):
        rule_from_placement(entry(organizer_email=""), "organizer", "work", 5)
