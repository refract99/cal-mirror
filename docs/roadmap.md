# cal-mirror roadmap

Rows are in order. A row starts when the one above it is done, unless it says otherwise.

## Next: close out v1

Only the owner can do these; no code is needed unless the install or the week finds a fault.

- Install answers: TLS provider (DNS-01 or self-signed), container registry, Postgres server, USB node and path; the manifests take each as a setting (added 2026-10-24)
- Install on k3s from the owner's laptop and confirm both pods start against Postgres: acceptance criterion 14 (added 2026-10-24)
- A week on the owner's account: the booking assistant sees the BLOCKED events and its bookings are filed correctly; acceptance criterion 13 (added 2026-10-24)
- Record the result of 13 and 14 in `docs/prd.md` and `docs/architecture.md` ("Not built"), and file each fault found as a fix under 0.1.x (added 2026-11-16)

## Then: 0.1.14 (docs and release hygiene)

Independent of the close-out, so it can run beside it. No behaviour change.

- Bring the docs level with 0.1.13. Three changes made after v1 are not in the docs (added 2026-11-16):
  - No default calendar is allowed (c084171): `docs/prd.md` (outcome, "Choosing the calendar", web app, criterion 2), `docs/page_calendars.md` rule 5, `docs/quickstart.md` step 3 and `README.md` still say a default is required.
  - BLOCKED events carry no reminders (0e56d94) and are never created for a past event (8a824d1): `docs/prd.md` section 1 and `docs/plan_blocked.md`.
  - The Sync now button, and that Sync now and an applied rule change re-read every future event (7e8842e, b9c40f0): `docs/prd.md` section 3, `docs/page_settings.md`, `docs/sync.md`.
- A GitHub Release per tag: today the release workflow pushes the two images and creates no Release, so the repo shows none for v0.1.0 to v0.1.13. Add a step to `.github/workflows/release.yml` that creates the Release with generated notes (needs `contents: write`) (added 2026-11-16)
- Done 2026-11-16: the checks no longer assume one machine. `scripts/gate.sh` runs three plain commands, the tests start their own PostgreSQL from `PATH`, and `uv.lock` is level with version 0.1.13. This prepares the repository for Slip's per-project environment (Slip PRD-27).

## v2: public and push (owner's priorities, 2026-11-16)

Implementation is on hold by the owner's word, 2026-11-16, except row 1, which the owner released as PRD-01 on 2026-11-17; rows 2 and 3 stay on hold. Three PRDs (owner's decision 2026-11-17: row 1 alone is PRD-01; rows 2 and 3 follow as their own PRDs), built in this order because each row needs the one above: a public sign-in page must be hardened before it is reachable, and Google delivers change notices only to a public HTTPS address with a trusted certificate.

1. **Sign-in fit for the internet.** Written as PRD-01 in `docs/roadmap.json` (2026-11-17), submitted to Slip on 2026-11-17. Done 2026-11-19 on `main` (c2f72b0, not yet released): Slip landed signin-core, deploy-setup-code and google-signin; the owner cancelled the Slip PRD after its third plan deadlocked, and passkeys and the end-to-end contract `tests/test_acceptance_signin.py` were finished by hand. The app's own sign-in stays separate from its Google grant (it already is: password and TOTP are local; the Google token is only the app's permission to work on the calendars). Needed before exposure: 2FA required, not optional; throttling by client address in place of the single global lock, which lets any stranger lock the owner out; a one-time setup secret so the first visitor cannot claim `/setup`; an Origin check on every form post; passkeys as a second factor (this takes in "More 2FA methods" below). (added 2026-11-16)
   - **Sign in with Google, as an option beside the password** (owner's decision 2026-11-16). A second OAuth request on the same client, asking for `openid` and `email` only and kept apart from the calendar grant, so it needs no Google verification (confirm against Google's current rules in the PRD). The owner links his Google account once while signed in with the password; the app stores that account's stable ID and accepts no other. A Google sign-in is complete by itself: the app trusts the Google account's own 2-Step Verification and asks for no TOTP or passkey after it (owner's decision 2026-11-16). Password with 2FA stays as the way in on a fresh install and when Google sign-in fails.
2. **Public URL with a Let's Encrypt certificate.** Reverses the PRD's "Do not expose the web app to the public internet" and the README's "LAN or VPN only". The manifests already take the certificate issuer as a setting, and the web app already reloads a renewed certificate; the work is a Let's Encrypt issuer, the web NetworkPolicy (today LAN and VPN ranges only), the real client address behind whatever forwards the traffic, and the docs. Open: how public traffic reaches the node. (added 2026-11-16)
3. **Sync on a calendar change, not a timer.** Google push notifications: the worker opens a watch channel per selected calendar and renews it before it expires; the web app gets one public, sign-in-free path that accepts Google's notice, checks the channel's secret and marks that calendar for sync; the worker picks the mark up on its request poll and runs the same incremental sync as today. The web app still never calls Google. The timer stays as a slow fallback, because a notice can be lost. (added 2026-11-16)

## v3: front end (owner's priority, 2026-11-16)

- Front-end updates: the list of changes is not yet given. Independent of v2 except the sign-in pages, which v2 row 1 changes. (added 2026-11-16)

## Later

Kept in mind, not prioritised by the owner (2026-11-16).

1. Split a running recurring copy on a rule change so its past instances stay put: today the whole series moves; accepted as a deviation by the owner 2026-10-24 (added 2026-10-24)
2. Egress limited to Google and ntfy by host name: today the NetworkPolicy allows 443 to every address; accepted as debt by the owner 2026-10-24, revisit with an FQDN-aware policy engine (Cilium) or an egress proxy (added 2026-10-24)
3. More 2FA methods beyond TOTP, recovery codes and passkeys (passkeys done in v2 row 1, 2026-11-19) (added 2026-10-24)

## Released

- v1 (cal-mirror-core): built and reviewed clean at 5c8ca56; accepted by the owner 2026-10-27 and merged to `main`. Criteria 13 and 14 (the k3s install and the week on the owner's account) are still open.
- 0.1.1 to 0.1.13 (2026-10-27): fixes from first use on the owner's account. OAuth client entered on the web page; readable Google errors; version in the header; Sync now; calendar names on the Rules page; no default calendar allowed; no BLOCKED event for a past event; BLOCKED events carry no reminders; the Status page shows what happened to each upcoming main-calendar event; archive cleanup deletes ended occurrences of running series and sweeps the main and secondary calendars, purging their records.
