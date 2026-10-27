# google_api

`cal_mirror/google_api.py` is the only module that talks to Google Calendar. It maps Google event
items to `Event` and back, and implements the `Calendar` port as `GoogleCalendar(service)`.
`build_service(token_json, client_config)` builds the real service from a stored OAuth token
(missing `client_id`/`client_secret` come from the `web` or `installed` client config); tests pass
a stub service instead. `SCOPES` is the single Calendar scope.

## Mapping

- `event_from_google(calendar_id, item)` never raises on missing keys. Timed values become aware
  UTC datetimes; `date` values become `date` with `all_day=True`; absent times are `None`.
  `meeting_link` is `hangoutLink`, else the first video entry point. `dial_in` has one string per
  phone entry point (`<number> PIN <pin>` when a pin exists).
- `event_to_google(event)` emits only content fields: summary, description, location, times,
  recurrence, transparency, visibility, attachments, `extendedProperties.private` and
  `conferenceData` (with `createRequest` removed, so no new meeting is created). It never emits
  `organizer`, `creator`, `id` or `status`, and `attendees` is always `[]`, so a patch also strips
  guests. `event_from_google` reads `time_zone` from `start.timeZone` (empty when missing), and `event_to_google` adds that `timeZone` on timed `start` and `end` only, using `"UTC"` when a recurring event has no zone.

## The one write path

`send_write(service, write)` is the only function that writes. Every call passes the literal
`sendUpdates="none"`; nothing configures it. A create with a non-empty event id inserts that id,
and on HTTP 409 (the id already exists, including a cancelled copy) patches the same id with the
update body plus `"status": "confirmed"`. An update whose conference is empty also sends
`conferenceData: null` so Google clears the old Join link; a create never sends that null.

| op | call | returns |
|---|---|---|
| create | `events().insert(calendarId, body, sendUpdates, conferenceDataVersion=1, supportsAttachments=True)` | response `id` |
| update | `events().patch(...)` as above plus `eventId` | response `id` |
| delete | `events().delete(calendarId, eventId, sendUpdates)` | `write.event_id` |
| move | `events().move(calendarId, eventId, destination, sendUpdates)` | response `id` |

Create or update without a body, or an unknown op, raises `ValueError` before any call.
`GoogleCalendar.apply` delegates to it.

## Reading

`list_calendars`, `list_changes`, `list_events` page until no `nextPageToken`.
`list_changes` sends `syncToken` when given, else `timeMin`, never both, and returns the last
page's `nextSyncToken` (or `""`). Event lists use `showDeleted=True`, `singleEvents=False`,
`maxResults=2500`. `get_event` returns `None` on HTTP 404.

## Errors

Only `HttpError` and `RefreshError` are caught, and re-raised `from None`. The message is
`Google API <method> failed: HTTP <status>`; the original text is never included, since it holds
the request URI and sync token.

| cause | raised |
|---|---|
| HTTP 410 in `list_changes` | `SyncTokenExpired` |
| HTTP 401 | `CalendarAuthError` |
| HTTP 403 unless the reason is `rateLimitExceeded`, `userRateLimitExceeded` or `quotaExceeded` | `CalendarAuthError` |
| `RefreshError` | `CalendarAuthError("Google token rejected")` |
| any other `HttpError` (including 410 elsewhere, and rate-limit 403) | `CalendarError` |
