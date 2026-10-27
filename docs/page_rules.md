# Rules page

`cal_mirror/web/page_rules.py` exports `router`. The page never saves rules. A rule change is a
`Request` row of kind `rules`; the worker counts the future copies that would move, and after
The owner confirms it saves the rules and moves the copies. Past copies stay where they are. Every
route needs a signed-in user (otherwise `303` to `/signin`).

## Routes

| Route | What it does |
|---|---|
| `GET /rules` | The current request (by state) and the editor form. Optional `from_placement=<id>`; `404` when no such placement. |
| `POST /rules` | Reads the form, adds a `rules` request, redirects `303` to `/rules`. |
| `POST /rules/requests/<id>/confirm` | `ready` becomes `confirmed`; `303` to `/rules`. |
| `POST /rules/requests/<id>/cancel` | Becomes `cancelled`; `303` to `/rules`. |

`404` for confirm and cancel when the request is missing or is not a `rules` request.

## Form encoding

One `<form method="post" action="/rules">`. Hidden `count` is the number of rows: the current
rules plus one. Row `i` (0-based), one per current rule, in list order:

- `kind_{i}`: select of `guest_domain`, `organizer`, `title_word` (the rule's kind selected)
- `value_{i}`: text input
- `calendar_{i}`: select of the secondary calendars (the rule's calendar selected)
- `position_{i}`: number input, default `i + 1`
- `delete_{i}`: checkbox, value `1`

The last row `i = len(rules)` is a blank add row: its selects start with an empty option, the
value is empty and the position is `len(rules) + 1`. With `from_placement` the add row is
prefilled and the placement title is shown: kind `organizer` with the organizer email when there
is one; else kind `guest_domain` with the first guest domain; else kind `title_word` with an
empty value. The calendar is never preselected.

## Saving

`count` must be an integer from 0 to 200. For each row: `delete_{i}` present drops it; kind,
value (stripped) and calendar all empty skips it (an unused add row). Otherwise, in this order:
kind, value, calendar, position are checked. Kept rows are sorted by `(position, i)`. Then every
`rules` request in state `pending` or `ready` becomes `cancelled`, and a new `pending` request is
added with `params = {"rules": [{"kind", "value", "calendar_id"}, ...]}`. Deleting every rule is
valid (`{"rules": []}`).

## Request states on `/rules`

The current request is the `rules` request with the highest id.

- `pending`: "Counting the copies that would move..." and a 5 second refresh.
- `ready`: "N future copies will move to another calendar." (N = `result["moves"]`; "1 future
  copy will move to another calendar." for one), the proposed rules in order, and `Confirm` and
  `Cancel` buttons.
- `confirmed`: "Moving copies..." and a 5 second refresh.
- `failed`: "The rule change failed: " and `result["error"]`.
- `done`, `cancelled` or no request: no request section.

## Refusals

| Case | Status | Message |
|---|---|---|
| `count` missing, not an integer, or outside 0..200 | 400 | `Bad form.` |
| Kind not one of the three | 400 | `Rule N: unknown kind.` |
| Value empty after stripping | 400 | `Rule N: the value is empty.` |
| Calendar not a secondary calendar | 400 | `Rule N: pick one of the secondary calendars.` |
| Position not an integer | 400 | `Rule N: the position must be a whole number.` |
| `validate_rule` refuses the rule (a title word of more than one word, a guest domain of only `@`) | 400 | `Rule N: <its message>` |
| Confirm when not `ready` | 409 | `This change cannot be confirmed.` |
| Cancel when `done` or `confirmed` | 409 | `This change can no longer be cancelled.` |

`N` is the 1-based row number. A 400 re-renders the page with the message and adds, cancels and
saves nothing. A 409 changes nothing.
