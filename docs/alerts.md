# Alerts

`cal_mirror/alerts.py` holds the failure alert state machine and the ntfy client.
One message is sent when a failure starts and one when it clears. There is no
alert for each failed run.

## `step_alert(state, failure, now)`

Pure: no store, network, clock, env or logging. Returns `(new_state, message | None)`.
Rules, in order:

1. Alert active and a failure arrives: state unchanged, no message. A different
   kind while active keeps the first kind's `active` and `since`.
2. Alert active and no failure: state resets to `AlertState()`, message
   `cal-mirror recovered: <kind>`.
3. Not active, `FAIL_API`: `api_errors` goes up by one. At `API_ERROR_THRESHOLD`
   (2) the alert starts with `cal-mirror failure: google_api`; below it, no message.
4. Not active, any other kind (including an unknown kind): the alert starts at
   once with `cal-mirror failure: <kind>` and `api_errors` is 0.
5. Not active, no failure: `AlertState()`, no message (this resets `api_errors`).

## Messages

Only two forms exist: `cal-mirror failure: <kind>` and `cal-mirror recovered: <kind>`.
They never hold `failure.detail`, a secret, or any event title or detail.

## `NtfyNotifier(server, topic, client=None, timeout=10.0)`

Satisfies `ports.Notifier`. `send(title, message, click="")` makes one POST to
`<server>/<topic>` with the UTF-8 message as body, a `Title` header, and a `Click`
header only when `click` is not empty.

A title that is not ASCII is sent in RFC 2047 form,
`=?UTF-8?B?<base64 of UTF-8 title>?=`, because httpx encodes headers as ASCII and
ntfy decodes RFC 2047.

## Errors

- Status 400 or above: `NotifyError("ntfy send failed: HTTP <status>")`.
- `httpx.HTTPError`: `NotifyError("ntfy send failed: <ExceptionName>")`, raised
  `from None`.

The text never holds the body, the topic or the URL (the topic is the only access
control on ntfy.sh).

## Worker use

The worker (row E1) keeps the state with `get_alert_state` / `set_alert_state`,
calls `step_alert`, and sends the returned message with `NtfyNotifier.send`.
