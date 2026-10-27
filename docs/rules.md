# Rules

Rules choose the secondary calendar for a new event on the main calendar. They live in
`cal_mirror/rules.py`, which is pure: data in, data out.

## Rule kinds

- `guest_domain`: matches when the value (trimmed, one leading `@` removed, lower-cased) equals the
  domain of a guest exactly. `eng.acme.com` does not match `acme.com`, and `notacme.com` does not
  match either.
- `organizer`: matches when the value equals the event's organizer email, ignoring case and
  surrounding spaces.
- `title_word`: matches when the value appears in the event title as a whole word, ignoring case.
  `sync` matches `Weekly sync` and `SYNC-up`, not `Async review` or `syncs`. Special characters
  are matched literally.

## Guests

A guest is an attendee who is not you and not flagged as organizer. The organizer is not a guest,
whether it is `organizer_email` or an attendee flagged `organizer`; organizer rules cover them.
`guest_domains(event)` lists the lower-cased guest domains in attendee order without repeats; an
address with no `@` is skipped.

## Choosing the calendar

Rules are tried in list order and the first match wins. When no rule matches, or the list is
empty, the event goes to the default calendar. A rule that fails validation never matches.

## Rule from a placement

`rule_from_placement(entry, kind, calendar_id, rule_id, value="")` builds a rule from a logged
default placement. For `guest_domain` it uses `value`, else the first guest domain of the entry.
For `organizer` it uses the entry's organizer (any `value` is ignored). For `title_word` it
requires a `value` that is a whole word of the entry's title; case is kept.

## Errors

All are `ValueError`.

- `unknown rule kind: <kind>`
- `rule value is empty`
- `title word must be one word`
- `rule calendar is empty`
- `placement has no guest domain`
- `placement has no organizer`
- `title word is required`
- `word not in title: <word>`
