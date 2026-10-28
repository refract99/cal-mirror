# Quickstart with Docker Compose

Runs the web app, the worker and Postgres on one machine. For Kubernetes see [install.md](install.md).

You need Docker with the Compose plugin, and a Google OAuth client ([google-oauth.md](google-oauth.md)).

```sh
git clone https://github.com/refract99/cal-mirror
cd cal-mirror
sh deploy/compose/init.sh          # writes .env with fresh secrets
docker compose up -d --build
```

Then open <https://localhost:8443>. The certificate is self-signed and made on first start, so your browser warns once; continue past it.

1. **Set a password** (first visit), then turn on 2FA (TOTP) under the account page. Save the recovery codes.
2. **Connect Google.** On the Google page, paste your OAuth client ID and secret once (they are stored encrypted), then press Connect Google. Sign in with the account whose calendars you want to mirror and allow access. You continue past the "unverified app" warning.
3. **Pick calendars:** the main calendar, the secondary calendars to mirror, and the default calendar for copies.
4. **Add rules** (optional) and set your **ntfy** server and topic in settings to receive alerts.
5. The worker runs every 5 minutes. The status page shows the last good sync.

## Settings

Edit `.env`, then `docker compose up -d`.

| Variable | Meaning |
| --- | --- |
| `CAL_MIRROR_SECRET_KEY` | Encrypts stored secrets. **Back it up.** If you lose it, reconnect Google and set up 2FA again. |
| `PG_PASSWORD` | Postgres password. Only applied when the database is first created. |
| `HOST_NAME` | Name you type in the browser. Must match the redirect URI in your OAuth client. Default `localhost`. |
| `WEB_PORT` | Port on the host. Default `8443`. |
| `WEB_BIND` | Address the web app listens on. Default `127.0.0.1` (this machine only). Use a LAN address to reach it from other machines. Do not expose it to the internet. |
| `ARCHIVE_DIR` | Folder for archive zips on the host. Default `./archive`. In the app, choose the destination `/archive`. |

If you change `HOST_NAME` or `WEB_PORT`, update the redirect URI in Google Cloud to match, and remove the old certificate so a new one is made: `docker compose down && docker volume rm cal-mirror_certs`.

## Operating

```sh
docker compose logs -f worker      # what the worker is doing
docker compose ps
docker compose pull && docker compose up -d --build   # update after git pull
```

Postgres data is in the `pgdata` volume. To back it up: `docker compose exec db pg_dump -U cal_mirror cal_mirror > backup.sql`.
