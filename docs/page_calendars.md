# page_calendars

`cal_mirror/web/page_calendars.py` leaves `router`. Both routes need `current_user`
(no user: 303 to `/signin`) and use the store from `get_store`. The page never reads or
writes calendar events.

## Routes

### `GET /calendars`

Lists the calendars the owner owns (`access_role == "owner"`, store order). Each row shows
the `summary` (the `id` when the summary is blank) and three inputs, all with
`value=<calendar id>`, checked from the current settings:

- `main`: radio
- `secondary`: checkbox
- `default`: radio

With no owned calendar the page shows: "The worker has not read your calendars yet.
Connect Google and wait one sync."

### `POST /calendars`

Reads `main` (one), `secondary` (many; form order kept, a repeated id kept once at its
first place) and `default` (one). A blank value counts as not given.

On success it saves the settings with `main_calendar`, `secondary_calendars` and
`default_calendar` replaced (no other field changes) and answers 303 to `/calendars`.

On an error it answers 400, re-renders the page with the message and the submitted
choices, and does not save. Only the first failing rule is shown, in this order:

1. No main: "Pick a main calendar."
2. No secondary: "Pick at least one secondary calendar."
3. Any given id not owned: "Pick only calendars you own."
4. Main among the secondaries: "The main calendar cannot also be a secondary calendar."
5. Default blank or not among the secondaries: "The default calendar must be one of the secondary calendars."
