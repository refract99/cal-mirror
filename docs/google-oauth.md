# Google OAuth client

cal-mirror reads and writes your calendars through the Google Calendar API, so you need your own OAuth client. It stays inside your Google account; Google never reaches your machine.

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project (or pick one).
2. Enable the **Google Calendar API** for it.
3. **OAuth consent screen**: user type External, add your own Google account as a user, and add the scope `https://www.googleapis.com/auth/calendar`.
4. **Publish the app ("In production").** In "Testing" mode Google expires the refresh token after 7 days and cal-mirror would stop working. Publishing without verification is fine for your own use; Google shows an "unverified app" warning once when you connect, and you continue past it.
5. **Credentials → Create credentials → OAuth client ID → Web application.** Under **Authorized redirect URIs** add the address of your cal-mirror web app followed by `/google/callback`:
   - Compose quickstart on the same machine: `https://localhost:8443/google/callback`
   - Otherwise `https://<HOST_NAME>/google/callback`. Google requires HTTPS (except for `localhost`), and the host must be a name under a public domain, not a bare IP. It does not have to be reachable from the internet; a name that only resolves on your LAN works.
6. Copy the **client ID** and **client secret**. You paste them once on the Google page of the web app; they are stored encrypted in the database. No file or Kubernetes secret is needed. (Setting `CAL_MIRROR_GOOGLE_CLIENT` to a client JSON path still works as a fallback.)

The Google page shows the redirect URI to register. By default it is the address you browse the app at plus `/google/callback`; the redirect URI must match exactly what the app sends. To override it, set `CAL_MIRROR_OAUTH_REDIRECT` (set from `HOST_NAME` and `WEB_PORT` in the Compose setup, and from `OAUTH_REDIRECT` in the Kubernetes settings).
