# store_memory

`cal_mirror.store_memory.MemoryStore` is an in-memory `Store` for tests of the worker and the
web app. Build it with `MemoryStore()`. It does the same observable things as the PostgreSQL
store, with these exact semantics. It uses no threads, locks, files, network or logging.

## Copies

A method that returns a `list` or `dict` returns a new one on every call. A method that takes a
sequence or mapping keeps its own copy. Changing what you passed in or got back never changes the
store. Frozen dataclasses are stored and returned as they are.

## Values

- `get_settings`, `get_alert_state`, `get_status`, `get_auth` give `Settings()`, `AlertState()`,
  `Status()`, `AuthRecord()` until saved. Each save replaces the whole value.
- `list_rules` and `list_calendars` give `[]` at first. `save_rules` and `set_calendars` replace
  the whole list, keeping the given order. Rules keep their given `id` (no renumbering).
  `save_rules([])` empties the list.
- `get_sync_token` gives `None` for an unknown calendar. `set_sync_token(cid, None)` clears it.
  `""` is stored as `""`.
- `get_meta` gives `None` for an unknown key, else the last `set_meta` value.
- `get_secret` gives `None` for an unknown name. `set_secret(name, None)` deletes it. Values stay in
  memory only; there is no `SecretBox`, since encryption at rest belongs to the PostgreSQL store.

## Links

Keyed by `link.source`. `put_link` inserts or replaces. `get_link` gives the link or `None`.
`delete_link` of an unknown source does nothing. `list_links` is sorted by `source` in code-point
order (`sorted` on `str`; PostgreSQL uses `COLLATE "C"`).

## Placements

`add_placement` ignores `entry.id`, assigns the next id (1, 2, 3, ... per store), stores
`dataclasses.replace(entry, id=new_id)` and returns the new id. `list_placements(limit=100)` gives
the newest first, meaning the highest id (not `at`), at most `limit` entries; `limit <= 0` gives
`[]`. `get_placement(id)` gives the entry or `None`.

## Notice state

`get_notice_state(source)` gives `{}` for an unknown source, else a new `dict` copy of what
`set_notice_state` stored. `set_notice_state` replaces; it does not merge.

## Requests

`add_request(kind, params)` assigns the next id (1, 2, 3, ... per store) and stores a `Request`
with `state=REQ_PENDING`, `result={}` and `created=datetime.now(UTC)`.

`params` and `result` are stored as a JSON round trip (`json.loads(json.dumps(x))`), as `jsonb`
does: a tuple reads back as a list. A value `json` cannot encode (`TypeError` or `ValueError`)
raises `StoreError`, and nothing is stored. Every read returns fresh copies of `params` and
`result`.

- `get_request(id)` gives `None` for an unknown id.
- `update_request(req)` raises `StoreError("unknown request <id>")` for an unknown id. Otherwise it
  stores `kind`, `state`, `params` and `result`, and keeps the stored `created`.
- `list_requests()` gives all requests oldest first (ascending id). `list_requests(state)` gives
  only those in that state, in the same order.

## Sessions

`create_session(token_hash, expires)` inserts or replaces. `session_valid(token_hash, now)` is True
only when the session exists and `expires > now`; `expires == now` is False and an unknown hash is
False. It deletes nothing. `delete_session` of an unknown hash does nothing.

## Errors

Only `update_request` (unknown id) and an unencodable request `params` or `result` raise
`StoreError`. No other method raises for an unknown key.

## Writing tests

Use fixed aware datetimes such as `datetime(2026, 11, 3, 13, 30, tzinfo=UTC)`. Only `created` on a
request comes from the clock; assert its type and that `tzinfo` is set, not its value.
