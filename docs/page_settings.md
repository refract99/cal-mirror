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
| `allowed_hours_0` .. `allowed_hours_6` | Monday .. Sunday, `HH:MM-HH:MM`; blank means no allowed hours that day |

The page changes only these ten settings. `main_calendar`, `secondary_calendars`,
`default_calendar` and `two_factor` stay as they were.

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

A URL `urlsplit` cannot parse (such as `https://[`) gets the field's URL message.
