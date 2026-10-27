# cal-mirror roadmap

## Next release: v1 (cal-mirror-core)

- Install answers: TLS provider (DNS-01 or self-signed), container registry, Postgres server, USB node and path; the manifests take each as a setting (added 2026-10-24)
- Install on k3s from the owner's laptop and confirm both pods start against Postgres: acceptance criterion 14 (added 2026-10-24)
- A week on the owner's account: the booking assistant sees the BLOCKED events and its bookings are filed correctly; acceptance criterion 13 (added 2026-10-24)

## Later

- Egress limited to Google and ntfy by host name: today the NetworkPolicy allows 443 to every address; accepted as debt by the owner 2026-10-24, revisit with an FQDN-aware policy engine (Cilium) or an egress proxy (added 2026-10-24)
- Split a running recurring copy on a rule change so its past instances stay put: today the whole series moves; accepted as a deviation by the owner 2026-10-24 (added 2026-10-24)
- More 2FA methods beyond TOTP and recovery codes: the PRD makes 2FA configurable and names TOTP for version 1 only (added 2026-10-24)

## Released

- v1 (cal-mirror-core): built and reviewed clean at 5c8ca56; accepted by the owner 2026-10-27 and merged to `main`. Criteria 13 and 14 (the k3s install and the week on the owner's account) are still open.
