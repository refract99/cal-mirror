# Archive page

`cal_mirror/web/page_archive.py` exports `router`. The page only writes `Request` rows of kind
`archive`; the worker writes the archive, runs the count check and does the deletes. Every route
needs a signed-in user (otherwise `303` to `/signin`).

## Routes

| Route | What it does |
|---|---|
| `GET /archive` | Start form and the list of archive requests. |
| `POST /archive` | Fields `cutoff`, `destination`. Adds a request and redirects `303` to `/archive/<id>`. |
| `GET /archive/<id>` | One request, by state. `404` when missing or not an archive request. |
| `POST /archive/<id>/confirm` | `ready` with check `ok` becomes `confirmed`; `303` to `/archive/<id>`. |
| `POST /archive/<id>/cancel` | Becomes `cancelled`; `303` to `/archive/<id>`. |

`GET /archive` with no archive destination in Settings shows "Add an archive destination in
Settings first." with a link to `/settings` and no form. Otherwise it shows a form with a date
input `cutoff` and a select `destination` (one option per destination, in settings order). Below
is a table of archive requests, highest id first, at most 20: id (linked), cutoff, destination,
state.

"In progress" means an archive request in state `pending`, `ready` or `confirmed`.

## States on `/archive/<id>`

Every state shows the cutoff and destination, then:

- `pending`: "Writing the archive and counting..."; response header `Refresh: 5`.
- `ready`: the dry-run table from `result["counts"]` (keys `<calendar>/<year>`): one row per key
  sorted by calendar then year, a `<calendar> total` row after each calendar, and a final
  `All calendars total` row. No counts shows "No events before the cutoff." After that line or
  the table, and before the forms, an int `kept_unbounded` above 0 shows
  "Kept, too large to check: N recurring series." (`id` `kept-unbounded`). `0`, a missing value,
  or a bool does not. When `result["check"] == "ok"` there are a `Confirm delete` form and a
  `Cancel` form. Otherwise it shows "Archive check failed: nothing will be deleted." and only
  `Cancel`.
- `confirmed`: "Deleting..."; response header `Refresh: 5`.
- `done`: "Archive finished." and each `result` key and value except `counts` and `check`, sorted
  by key.
- `failed`: "Archive failed." and `result["error"]` when present.
- `cancelled`: "Cancelled. Nothing was deleted."

## Refusals

| Request | Status | Message |
|---|---|---|
| `POST /archive`, cutoff not an ISO date or not before today | 400 | The cutoff must be a date before today. |
| `POST /archive`, destination not in settings | 400 | Pick one of the archive destinations. |
| `POST /archive`, an archive is in progress | 409 | An archive is already in progress. |
| `POST /archive/<id>/confirm`, not `ready` with check `ok` | 409 | This archive cannot be confirmed. |
| `POST /archive/<id>/cancel`, state `done` or `confirmed` | 409 | This archive can no longer be cancelled. |

The checks on `POST /archive` run in the order listed; a refusal adds no request. Nothing is
deleted by this page: the worker deletes only after the request is `confirmed` and its check
passed.
