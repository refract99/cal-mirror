# Contracts

Shared names for cal-mirror. A component imports these and does not redefine them.

## `cal_mirror.model`

Frozen dataclasses:

- `Attendee`
- `Attachment`
- `Event` (`time_zone` is the IANA zone of a timed start, `""` when unknown, and is not compared)
- `PlannedWrite`
- `Link`
- `Rule`
- `PlacementEntry`
- `Settings`
- `CalendarInfo`
- `Notice`
- `Failure`
- `AlertState`
- `Status`
- `AuthRecord`
- `Request`

Helpers: `source_key`, `is_mirror`, `is_busy`, `declined_by_self`, `is_meeting`.

Constants: `MARKER_SOURCE`, `MARKER_KIND`, `KIND_BLOCKED`, `KIND_COPY`, `BLOCKED_TITLE`, `OP_CREATE`, `OP_UPDATE`, `OP_DELETE`, `OP_MOVE`, `RULE_GUEST_DOMAIN`, `RULE_ORGANIZER`, `RULE_TITLE_WORD`, `CHECK_NO_LINK`, `CHECK_HOURS`, `FAIL_TOKEN`, `FAIL_API`, `FAIL_DB`, `FAIL_ARCHIVE`, `REQ_ARCHIVE`, `REQ_RULES`, `REQ_PENDING`, `REQ_READY`, `REQ_CONFIRMED`, `REQ_DONE`, `REQ_FAILED`, `REQ_CANCELLED`.

## `cal_mirror.ports`

Protocols: `Clock`, `SecretBox`, `Notifier`, `Calendar`, `Store`.

Clocks: `SystemClock`, `FixedClock`.

Errors: `CalendarError`, `CalendarAuthError`, `SyncTokenExpired`, `NotifyError`, `StoreError`.

## `cal_mirror.web.deps`

`SESSION_COOKIE`, `session_hash`, `get_store`, `get_clock`, `current_user`, `templates`.

## `cal_mirror/web/templates/base.html`

Pages extend this template. It defines the `title` and `content` blocks. The nav links to `/status`, `/calendars`, `/rules`, `/settings`, `/archive`, `/google`, and `/signout`.
