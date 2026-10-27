# cal-mirror

Self-hosted mirror for one Google account with several calendars.

- **Busy blocks:** each busy event on a secondary calendar appears as a private "BLOCKED" event on your main calendar, so anyone or anything that reads only the main calendar (a booking assistant, a scheduling link) sees all your commitments.
- **Rule-filed copies:** each new event on the main calendar is copied, with its details, into the secondary calendar chosen by your rules (guest domain, organizer, or a word in the title), with a default when no rule matches.
- **Meeting checks:** a notice when a meeting has no call link or falls outside your hours.
- **Archive and cleanup:** archive old events to one zip per year (`.ics` and raw `.json`), check the count, and delete only after you confirm.

It never sends email or invitations (every write uses `sendUpdates=none`), never adds guests, calls no AI service, and keeps your events on your own machine. Alerts go to an [ntfy](https://ntfy.sh) topic.

A worker does all calendar work. A small config-only web app (password and TOTP sign-in) sets calendars, rules and settings. Both use Postgres.

## Quickstart

Docker Compose on one machine, about ten minutes: [docs/quickstart.md](docs/quickstart.md).

You need a Google Cloud OAuth client first: [docs/google-oauth.md](docs/google-oauth.md).

## Kubernetes

Two images and kustomize manifests (cert-manager TLS, network policies, an archive volume on a pinned node): [docs/install.md](docs/install.md). Images are published to `ghcr.io/refract99/cal-mirror-web` and `ghcr.io/refract99/cal-mirror-worker`.

## Scope and security

- **One user and one Google account.** Only calendars you own are touched.
- **LAN or VPN only.** The web app has password and TOTP sign-in with lockout, but it is not built to face the internet. Do not expose it.
- Tokens, passwords and 2FA secrets are encrypted at rest with a key you provide (`CAL_MIRROR_SECRET_KEY`). Back that key up: without it the stored Google token cannot be read.
- Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## More

- What it does, in detail: [docs/prd.md](docs/prd.md). How it is built: [docs/architecture.md](docs/architecture.md). Known gaps and plans: [docs/roadmap.md](docs/roadmap.md).
- Development: `scripts/gate.sh` runs ruff and the tests (it starts a throwaway Postgres; see the script). The tests use a fake Google Calendar API, never the live one.

## License

MIT. See [LICENSE](LICENSE).
