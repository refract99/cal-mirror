# store_pg

`cal_mirror/store_pg.py` is the Postgres adapter of the `Store` port. `PgStore(dsn, box)` and
`migrate(conn)` are the names other components call. Dataclasses and `StoreError` come from
`cal_mirror.model` and `cal_mirror.ports`; this module does not redefine them.

## Connections

`PgStore` keeps no connection on the object and uses no pool. `__init__` opens one connection,
calls `migrate`, and closes it. Every later method opens its own connection:

```python
with psycopg.connect(self.dsn) as conn:
    ...
```

That is one transaction per call: Postgres commits when the block finishes and rolls back when it
raises. A fresh connection per call is safe across the web app's threads. Any `psycopg.Error`
(connection refused, missing database, constraint) is re-raised as `StoreError(str(err))` from the
original error. Other exceptions are not caught.

## Migrations

`migrate` runs in the caller's transaction and commits only when that transaction commits.

1. `CREATE TABLE IF NOT EXISTS schema_version (name text PRIMARY KEY, applied timestamptz NOT NULL DEFAULT now())`.
2. `SELECT pg_advisory_xact_lock(4242)`, so a web pod and a worker pod starting together apply the
   same files once.
3. For each `sorted((Path(__file__).parent / "migrations").glob("*.sql"))` whose file name is not
   already in `schema_version`, execute that file's text with no parameters, then insert the file
   name.

A second call sees `0001_init.sql` and applies nothing. `migrations/` has no `__init__.py`.
`0001_init.sql` creates the tables below. `schema_version` itself is created by `migrate`, not by
that file.

## Tables

| Table | Key | Role |
|---|---|---|
| `state` | `name` | One jsonb row each for `settings`, `alert_state`, `status`, `auth` |
| `rules` | `position` | Ordered rules. `save_rules` deletes every row, then inserts the list in order |
| `calendars` | `id` | `position`, `summary`, `access_role`, `is_primary` (`primary` is reserved) |
| `sync_tokens` | `calendar_id` | Sync token text. `set_sync_token(id, None)` deletes the row |
| `links` | `source` | Mirror link. `put_link` upserts `ON CONFLICT (source) DO UPDATE` |
| `placements` | `id` identity | `add_placement` ignores `entry.id` and returns the new id |
| `notice_state` | `source` | jsonb object. A missing source reads `{}` |
| `requests` | `id` identity | `created timestamptz DEFAULT now()` |
| `secrets` | `name` | Ciphertext only (`token text NOT NULL`) |
| `sessions` | `token_hash` | `expires timestamptz NOT NULL` |
| `meta` | `key` | `value text NOT NULL`. A missing key reads `None` |

The closest existing construct to `meta` is the `state` table, also a keyed jsonb row store. `meta`
is a separate key/value table because `state` rows are typed dataclass rows keyed by name
(`settings`, `alert_state`, `status`, `auth`, each read back into its dataclass with field
defaults), while `meta` holds free-form strings such as `oauth_state` and `recent_notices` (a JSON
list kept as text) that have no dataclass and are read back as the exact string written.

`list_rules` and `list_calendars` order by `position`. `set_calendars` replaces every calendar row.
`list_links` orders by `source COLLATE "C"`. `list_placements(limit)` orders by `id DESC` (newest is
the highest id) with `LIMIT %s`. A limit of 0 or less returns `[]` and does not query.
`list_requests` orders by `id` ascending (oldest first) and, when a state is given, keeps only that
state.

`add_request` stores state `pending` and result `{}`. `update_request` writes `kind`, `state`,
`params` and `result` for `req.id` and keeps the stored `created`, as `MemoryStore` does. When no
row matches it raises `StoreError(f"unknown request {id}")`.

`session_valid(token_hash, now)` is true only when the row exists and `expires > now`.
`create_session` inserts or replaces: a second call with the same hash replaces its expiry
(`ON CONFLICT (token_hash) DO UPDATE`), as `MemoryStore` does.

## jsonb encoding

`state.data`, `notice_state.state`, `requests.params` and `requests.result` are bound with
`psycopg.types.json.Jsonb`. A missing `state` row reads as the default: `Settings()`,
`AlertState()`, `Status()`, or `AuthRecord()`. The object has one key per dataclass field. A key
that is absent on read takes that field's default. `Mapping` fields come back as `dict`.

Tuples are JSON arrays and are read back as tuples. `AuthRecord.password_hash` and
`recovery_hashes` are already argon2 hashes and are stored as those strings, not encrypted again.

Datetimes inside jsonb are `isoformat()` strings and are read with `datetime.fromisoformat`:

- `AlertState.since`, `Status.last_good_sync` and `AuthRecord.locked_until` are
  `"2026-11-02T15:00:00+00:00"` or JSON `null`.

`allowed_hours` is not a bare array. Monday 09:00 through 17:00 is stored as:

```json
{"0": ["09:00:00", "17:00:00"]}
```

The key is `str(weekday)` and each bound is `time.isoformat()`. On read, `int(key)` and
`time.fromisoformat` rebuild `{0: (time(9), time(17))}`.

## Other column encodings

`links.start` is text, not timestamptz:

- `None` is SQL NULL
- a `date` is `"2026-11-02"`
- a `datetime` is `"2026-11-02T15:00:00+00:00"`

On read, text that contains `T` becomes a `datetime` (`datetime.fromisoformat`); any other text
becomes a `date`. NULL stays `None`.

`placements.at`, `requests.created` and `sessions.expires` are `timestamptz`. Every one of those
values is converted with `.astimezone(UTC)` before it is returned. `placements.guest_domains` is
`text[]`, read back as a tuple.

## Secrets

`set_secret(name, value)` stores `box.encrypt(value)` in `secrets.token`. `get_secret` returns
`box.decrypt(token)`. `set_secret(name, None)` deletes the row. An unknown name returns `None`.
The plain value is never written to a table, a log line or an error message.
