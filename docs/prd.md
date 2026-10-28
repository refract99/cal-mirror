# cal-mirror PRD

- **Owner:** the project owner (decides and accepts)
- **Status:** complete, 2026-10-24. Approved for build 2026-10-24. v1 accepted 2026-10-27.
- **Version:** 1
- **Architecture:** `docs/architecture.md`
- **Roadmap:** `docs/roadmap.md`

## Outcome

A booking assistant sees every commitment I have, because each event on my secondary calendars shows as a "BLOCKED" event on my main calendar. Each new event on my main calendar is copied, with its details, into the right secondary calendar by rules I set, with a default when no rule matches. It runs on a schedule, with no agent, no token spend, and no email to anyone. It tells me when a meeting has no call link or falls outside my hours. I can also archive and delete old events whenever I choose.

## Users

- **The owner:** the only user. He signs in to the web app over the LAN or VPN to connect Google, pick calendars, write rules, change settings and start archive and cleanup. He gets ntfy messages only for things he must resolve: an app failure or a failed meeting check.
- **Booking assistant:** reads and books on the main calendar only. It never talks to cal-mirror.
- **Event organizers and guests:** must not see any effect of cal-mirror. They get no email from it.

## In scope

### 1. Mirror (worker)

- Runs every 5 minutes. The owner can change the interval in the web app.
- Reads changes, not a time window: each run gets every event that was created, edited or cancelled on each selected calendar since the last run.
- **Secondary to main:** each busy event on a secondary calendar gets one "BLOCKED" event on main. It has the same start, end, all-day flag and recurrence. It is busy and private, and has no description, location, guests or meeting link. When the source changes, the app updates the BLOCKED event. When the source is cancelled, the app deletes it.
- **Main to secondary:** each busy event on main that the app did not create is copied to one secondary calendar. The copy has the same title, time, recurrence, location and attachments. It is busy. When the original changes, the app updates the copy. When it is cancelled, the app deletes the copy.
- **Details at the top of the copy's body:** the body starts with a details block: the meeting link, dial-in numbers, the organizer and every participant with their response. Below it comes the original description. The copy also carries the original's meeting link as its own meeting link, so the "Join" button works. It never creates a new meeting. The copy has no guests, so a copy or an update never sends a notice to anyone, also for meetings that were booked earlier. When the original's details change, the app rewrites the block.
- **Choosing the calendar:** an ordered list of rules, where the first match wins. A rule matches on guest email domain, organizer email or a word in the title. If no rule matches, the event goes to the default calendar. The app logs each default placement.
- **Skipped:** events the app created, events marked free (for example, an all-day notice), and events the owner declined. This applies in both directions. If an event changes from busy to free, the app deletes its mirror. If it changes from free to busy, the app creates one.
- **First start:** existing future events are mirrored once. Past events are not mirrored.
- Each event the app writes carries a private marker that links it to its source. The app reads this marker, so it never mirrors its own events and never loops.

### 1a. Meeting checks (worker, set in the web app)

- When a meeting on main is created or changed, the worker checks it and sends an ntfy notice if a check fails. A meeting is a busy, timed event with at least one guest other than the owner.
- **No call link:** the event has no meeting link, and no Meet, Zoom or Teams link in its location or description.
- **Outside hours:** the event starts or ends outside the allowed hours for its weekday, in the owner's time zone. The owner sets the hours per weekday in the web app.
- The owner can turn each check on or off.
- **Message text:** the full date and time, the failed check and a link to the web app, which shows the meeting. Example: "Tue 2026-11-03 7:30 AM CST meeting: no call link". The message never contains the title or other event details.
- One notice per event and failed check. A new notice is sent only if a later change makes a check fail again, for example when the meeting moves to a new time.
- Only future meetings are checked. At first start, the worker sends one summary notice, not one notice per meeting.

### 2. Archive and cleanup (worker, started from the web app)

- The owner enters a cutoff date and picks a destination from the file locations set in the web app, for example a mounted USB drive. Nothing is fixed in the code, and the job can run again at any time.
- **Archive:** every event before the cutoff on every calendar the owner owns, including events he was invited to. One file per year: a zip with one `.ics` file and one raw `.json` file per calendar.
- **Check:** the app reads the archive back and compares its event count with the events it will delete. If the counts differ, it deletes nothing.
- **Dry run:** the web app shows the counts per calendar and per year. The app deletes only after the owner confirms.
- **Delete:** single events before the cutoff, recurring series that ended before the cutoff, and the occurrences before the cutoff of series that run on (the series itself stays), that the owner organized or created. Mirror events the app wrote are deleted with their source.
- **Secondary calendars are swept:** on every secondary calendar, everything before the cutoff is deleted whoever organized it, including copies the app wrote. The database records of every deleted event (links, placements, notice state) are removed with it.
- **Keep:** on the main calendar, events the owner was invited to, every series that runs past the cutoff (only its ended occurrences go), and a series with more than 2000 ended occurrences, which the dry run reports as kept.

### 3. Web app (config only)

- Single user. Password sign-in with configurable 2FA: TOTP with recovery codes in version 1.
- Connects the one Google account with OAuth, and shows "reconnect" when the token stops working.
- Shows the owner's own calendars as a list. The owner picks the main calendar, the secondary calendars and the default calendar.
- A rules editor: add, edit, delete and reorder rules. From a logged default placement, the owner can make a rule from that event.
- **Rule changes move future copies.** Before a rule change is saved, the web app shows a warning with the number of future copies that will move to another calendar, and asks the owner to confirm. If he confirms, the worker moves those copies. Past copies stay where they are. If he cancels, nothing changes.
- Settings: sync interval, ntfy server and topic, archive destinations.
- Starts archive and cleanup, shows the dry run, and takes the confirmation.
- A status page: last good sync, the current failure, counts, and the default-placement log.
- The web app writes config and requests. It never reads or writes calendar events. The worker does all calendar work.

### 4. Alerts

- One ntfy message when a failure starts, and one when it clears. There is no alert for each failed run.
- Failures include a rejected or expired token, Google API errors on consecutive runs, a database error, and an archive or cleanup that fails its check.

### 5. Packaging

- Two container images (web, worker) and k3s manifests that the owner applies with `kubectl` from his laptop.
- Postgres: a database and user on an existing Postgres server in k3s, or a separate one for this app.
- TLS on the web app with a certificate that renews by itself. Self-signed is the fallback.

## Out of scope

- Do not call an LLM, a judge model or any AI service.
- Do not send email or invitations. Every Google write uses `sendUpdates=none`. Never add guests to an event the app writes.
- Do not edit or delete an event the app did not create, except in a cleanup that the owner confirmed.
- Do not delete a main-calendar event the owner was invited to, or the series record of one that runs past the cutoff.
- Do not delete anything before the archive for that run is written and its count check passes.
- Do not touch a calendar the owner does not own. Do not support a second Google account or a second user.
- Do not let the web app read or write calendar events.
- Do not write the OAuth token, password, TOTP secret or recovery codes in plain text, in logs or in alerts. Do not put event titles or details in alerts.
- Do not expose the web app to the public internet.
- Do not apply manifests or create cluster resources from the build. The owner applies them.
- Do not call the live Google API in tests. Tests use a fake Calendar API.

## Acceptance criteria

1. A busy event created on a secondary calendar appears as a private BLOCKED event on main within one interval. An edit updates it and a cancel deletes it. `[check]`
2. An event created on main is copied to the calendar chosen by the first matching rule, or to the default when no rule matches, with the title, location, attachments and meeting link, and no guests. Its body starts with the meeting link and the participant list. An edit updates the copy and a cancel deletes it. `[check]`
3. Events the app wrote are never mirrored again: one source event gives exactly one mirror event after any number of runs. `[check]`
4. Free events, declined events and events on calendars that are not selected are not mirrored. An event that changes from busy to free loses its mirror. `[check]`
5. Every Google write sends `sendUpdates=none`, and no event the app writes has guests. `[check]`
6. Archive and cleanup writes one zip per year that holds an `.ics` and a `.json` file per calendar. It deletes nothing when the count check fails or before the owner confirms. `[check]`
7. Cleanup keeps invited events and the records of series that run past the cutoff, deletes the ended occurrences of the owner's series, and deletes only what the dry run listed. `[check]`
8. The web app needs a password and, when 2FA is on, a valid TOTP or recovery code. Five failed attempts lock sign-in for a time. `[check]`
9. No secret appears in plain text in the database, the logs or an alert. `[check]`
10. A failure sends one ntfy alert, and recovery sends one more. `[check]`
11. A rule change moves only future copies, and only after the owner confirms the warning. A cancelled change moves nothing. `[check]`
12. A future meeting with no call link, or outside the allowed hours, sends one notice when that check is on, and none when it is off. A change that does not alter the result sends nothing. `[check]`
13. On the owner's account, the booking assistant sees the BLOCKED events and its own bookings are filed correctly for one week. `[review]`
14. The manifests apply on k3s from the owner's laptop, and both pods start against Postgres. `[review]`

## Constraints

- Python 3.13, managed with `uv`. Repo gate: `ruff check`, `ruff format --check`, `pytest`.
- Libraries: `google-api-python-client` with `google-auth-oauthlib` (fallback: direct REST with `httpx`); FastAPI with server-rendered templates (fallback: Flask); `psycopg` 3; `pyotp`; `argon2-cffi`; `icalendar`.
- Secrets in the database are encrypted with a key from a k8s Secret.
- Google OAuth: the Cloud app is set to "In production" without verification. In "Testing" the refresh token expires after 7 days. The redirect URI must be HTTPS on a host whose domain is on the public suffix list. Google does not need to reach the host, so a LAN-only name under a public domain works.
- The worker's pod must reach the archive destination. A USB drive pins the worker to the node that has the drive.
- Outbound: Google APIs and the ntfy server only, plus Let's Encrypt and the DNS provider's API if cert-manager is used. Inbound: the web app, from hosts on the owner's network and VPN only.

## Open questions

None. The PRD is complete.
