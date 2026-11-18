# Settings page

Module `cal_mirror/web/page_settings.py`, template `settings.html`. It leaves `router`.
Every route needs a signed-in user (otherwise `303` to `/signin`). The page reads and
writes only `Settings`; it never reads or writes calendar events.

## Routes

- `GET /settings` shows the form filled from the stored settings.
- `POST /settings` validates the form. On success it saves and answers `303` with
  `Location: /settings`. On error it answers `400`, shows the message and the submitted
  values, and saves nothing.

## Form fields

Every text value is stripped before it is checked.

| Field | Meaning |
|---|---|
| `sync_interval_minutes` | whole minutes, 1 to 1440 |
| `ntfy_server` | http or https URL |
| `ntfy_topic` | 1 to 64 letters, digits, `-` or `_` |
| `archive_destinations` | textarea, one absolute path per line; blank lines dropped; may be empty |
| `owner_email` | one `@`, text on both sides, no whitespace |
| `time_zone` | a zone name known to `zoneinfo` |
| `web_url` | http or https URL |
| `check_no_link`, `check_hours` | checkboxes; present is on, absent is off |
| `signin_failure_limit` | whole number, 1 or more: wrong sign-in attempts from one address before it is locked |
| `signin_lock_seconds` | whole number, 1 or more: how long that address stays locked, and how long a quiet address is remembered |
| `allowed_hours_0` .. `allowed_hours_6` | Monday .. Sunday, `HH:MM-HH:MM`; blank means no allowed hours that day |

The page changes only these twelve settings. `main_calendar`, `secondary_calendars` and
`default_calendar` stay as they were. Both sign-in fields are required: a missing field,
a value below 1 or one that is not an integer is refused like any other bad value.

## Messages

Only the first failing rule is shown, in this order:

1. Sync interval must be a whole number of minutes from 1 to 1440.
2. ntfy server must be an http or https URL.
3. ntfy topic must be 1 to 64 letters, digits, - or _.
4. Each archive destination must be an absolute path.
5. Owner email must be an email address.
6. Time zone must be a zone name such as America/Chicago.
7. Web URL must be an http or https URL.
8. Allowed hours for `<Day>` must be HH:MM-HH:MM with start before end. (`<Day>` is
   Monday .. Sunday; the first failing weekday from Monday is named.)

9. Sign-in failure limit must be a whole number of 1 or more.
10. Sign-in lock seconds must be a whole number of 1 or more.

A URL `urlsplit` cannot parse (such as `https://[`) gets the field's URL message.
