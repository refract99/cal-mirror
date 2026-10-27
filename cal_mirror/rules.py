import re
from collections.abc import Sequence

from cal_mirror.model import (
    RULE_GUEST_DOMAIN,
    RULE_ORGANIZER,
    RULE_TITLE_WORD,
    Event,
    PlacementEntry,
    Rule,
)

RULE_KINDS = (RULE_GUEST_DOMAIN, RULE_ORGANIZER, RULE_TITLE_WORD)


def guest_domains(event: Event) -> tuple[str, ...]:
    domains: list[str] = []
    for attendee in event.attendees:
        if attendee.is_self or attendee.organizer or "@" not in attendee.email:
            continue
        domain = attendee.email.rpartition("@")[2].strip().lower()
        if domain not in domains:
            domains.append(domain)
    return tuple(domains)


def validate_rule(rule: Rule) -> None:
    if rule.kind not in RULE_KINDS:
        raise ValueError(f"unknown rule kind: {rule.kind}")
    value = rule.value.strip()
    if not value or (rule.kind == RULE_GUEST_DOMAIN and value == "@"):
        raise ValueError("rule value is empty")
    if rule.kind == RULE_TITLE_WORD and len(value.split()) > 1:
        raise ValueError("title word must be one word")
    if not rule.calendar_id.strip():
        raise ValueError("rule calendar is empty")


def _normal_domain(value: str) -> str:
    domain = value.strip()
    if domain.startswith("@"):
        domain = domain[1:]
    return domain.lower()


def _has_word(text: str, word: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _matches(event: Event, rule: Rule) -> bool:
    if rule.kind == RULE_GUEST_DOMAIN:
        return _normal_domain(rule.value) in guest_domains(event)
    if rule.kind == RULE_ORGANIZER:
        return rule.value.strip().casefold() == event.organizer_email.strip().casefold()
    return _has_word(event.summary, rule.value.strip())


def match_rule(event: Event, rules: Sequence[Rule]) -> Rule | None:
    for rule in rules:
        try:
            validate_rule(rule)
        except ValueError:
            continue
        if _matches(event, rule):
            return rule
    return None


def choose_calendar(
    event: Event, rules: Sequence[Rule], default_calendar: str
) -> tuple[str, Rule | None]:
    rule = match_rule(event, rules)
    if rule is None:
        return default_calendar, None
    return rule.calendar_id, rule


def rule_from_placement(
    entry: PlacementEntry, kind: str, calendar_id: str, rule_id: int, value: str = ""
) -> Rule:
    if kind == RULE_GUEST_DOMAIN:
        if value.strip():
            normal = _normal_domain(value)
        elif entry.guest_domains:
            normal = _normal_domain(entry.guest_domains[0])
        else:
            raise ValueError("placement has no guest domain")
    elif kind == RULE_ORGANIZER:
        normal = entry.organizer_email.strip().lower()
        if not normal:
            raise ValueError("placement has no organizer")
    elif kind == RULE_TITLE_WORD:
        normal = value.strip()
        if not normal:
            raise ValueError("title word is required")
        if not _has_word(entry.title, normal):
            raise ValueError(f"word not in title: {normal}")
    else:
        raise ValueError(f"unknown rule kind: {kind}")
    rule = Rule(id=rule_id, kind=kind, value=normal, calendar_id=calendar_id)
    validate_rule(rule)
    return rule
