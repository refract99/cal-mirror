# Fake Google Calendar

`cal_mirror.fake_google` is an in-memory Calendar API for tests. It does not open a network connection and it does not import any other `cal_mirror` module. `FakeService` speaks only the service surface in the architecture: `calendarList().list` and `events().list`, `insert`, `patch`, `delete`, `move`, and `get`.

## Seeding

`FakeCalendar` holds `calendars`, `events`, and `writes`. Build one, pass it to `FakeService`, and seed before any call. Seeding is not appended to `writes`, but it does advance the sync change counter.

- `add_calendar(id, summary="", access_role="owner", primary=False)` stores `id`, `summary`, `accessRole`, and `primary`.
- `add_event(calendar_id, item)` deep-copies `item`, assigns `evt1`, `evt2`, ... when `id` is missing, and sets `status` to `confirmed` when missing.
- `edit_event(calendar_id, event_id, **fields)` replaces top-level fields.
- `cancel_event` sets `status` to `cancelled` and keeps the item.
- `add_exception(calendar_id, series_id, original_start, **fields)` stores `<series_id>_<suffix>` with `recurringEventId` and `originalStartTime`. The suffix is `original_start` without `-` or `:`. A date-time becomes `dateTime`; a value with no `T` becomes `date`.
- `expire_sync_tokens()` drops every issued token. The next sync with one of them returns HTTP 410.

A helper aimed at an unknown calendar or event raises `KeyError`. Every helper return value is a deep copy.

`updated` is `2026-01-01T00:00:00Z` plus the change number in seconds, formatted with a `.000Z` suffix.

## Calling the service

Resource methods take keyword arguments only and return a request. Nothing is stored and nothing is logged until `execute()`.

```python
state = FakeCalendar()
state.add_calendar("a", summary="Main", primary=True)
state.add_event("a", {"summary": "Meet"})
service = FakeService(state)
page = service.events().list(calendarId="a").execute()
service.events().patch(
    calendarId="a",
    eventId="evt1",
    body={"summary": "Later"},
    sendUpdates="none",
).execute()
```

`writes` then holds one dict: `method` plus every parameter, including defaults (`sendUpdates` defaults to `None`, `conferenceDataVersion` to `1`, `supportsAttachments` to `True`). `body` is a deep copy of the body that was sent.

`list` without a sync token returns items in insertion order. `showDeleted=False` omits cancelled items. `timeMin` and `timeMax` keep an item whose end is after `timeMin` and whose start is before `timeMax` (`dateTime`, otherwise `date` at midnight UTC, otherwise `originalStartTime`). An item with no start is kept. A series master is kept when its first start is before `timeMax` and is not expanded. `singleEvents` is accepted and ignored.

With a sync token, the response is items changed after that token, cancelled ones included, ordered by change number. A token from another calendar, an unknown token, or a token used after `expire_sync_tokens()` raises `HttpError` with `resp.status == 410`. `timeMin` or `timeMax` together with a sync token, or an unknown `pageToken`, raises 400.

Pages hold `maxResults` items (default 2500). Every page but the last has `nextPageToken` (`page-<offset>`). The last page has `nextSyncToken` and not `nextPageToken`.

`insert` stores an event the way `add_event` does. An `id` that already exists raises 409. `insert` and `patch` raise `HttpError` 400 `Missing time zone definition for start time.` and store nothing when recurrence is non-empty (the body's, or the stored item's when a patch omits it) and `start` or `end` has `dateTime` without a non-empty `timeZone`; `add_event` is not checked. `patch` merges top-level keys. Patching a missing `<series>_<suffix>` id creates that exception first when the series is on the calendar (`20261027T150000Z` becomes `2026-10-27T15:00:00Z`, `20261027` becomes `2026-10-27`). `delete` cancels the item and returns `""`; deleting it again raises 410. `move` leaves a cancelled copy on the source and the same id, not cancelled, on the destination. `get` returns the item, including cancelled ones. An unknown calendar, event, or move destination raises 404, and a failed move records no write.
