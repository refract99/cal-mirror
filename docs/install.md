# Install on Kubernetes

The repository ships two images, not manifests. A release tag `v<version>` makes CI publish
`ghcr.io/refract99/cal-mirror-web:<version>` and `ghcr.io/refract99/cal-mirror-worker:<version>`.
The cluster's own GitOps repository holds the deployment, the same way as the cluster's other
apps; the owner's is `refract99/homelab`, folder `apps/cal-mirror`. The build applies nothing.

## What the deployment holds

- **Web**: the web image, one replica, port `8443`, serving TLS itself (`CAL_MIRROR_TLS_CERT`,
  `CAL_MIRROR_TLS_KEY`; a self-signed certificate is enough behind the ingress). Behind a Service on
  port 443 and the cluster's ingress, which terminates the public TLS certificate and re-encrypts
  to the pod without verifying it.
- **Worker**: the worker image, one replica, `Recreate`, with the archive volume at `/archive`.
  Archive destinations in the web app are paths under `/archive`.
- **Postgres**, reachable by both through `CAL_MIRROR_DSN`, with `PGPASSWORD` from the secret.
- **Secret** `cal-mirror-secrets`: `pg-password`, `secret-key` (Fernet key), `google-client.json`
  (the OAuth client, see [google-oauth.md](google-oauth.md)), and optionally `setup-code`.

Both containers run as uid 10001 with a read-only root file system and an `emptyDir` at `/tmp`.

## Environment

| Variable | Pod | Value |
| --- | --- | --- |
| `CAL_MIRROR_DSN` | both | `host=<postgres> port=5432 dbname=cal_mirror user=cal_mirror` |
| `PGPASSWORD` | both | secret key `pg-password` |
| `CAL_MIRROR_SECRET_KEY` | both | secret key `secret-key` |
| `CAL_MIRROR_GOOGLE_CLIENT` | both | path of the mounted `google-client.json` |
| `CAL_MIRROR_OAUTH_REDIRECT` | web | `https://<host>/google/callback` |
| `CAL_MIRROR_SIGNIN_REDIRECT` | web | `https://<host>/signin/google/callback` |
| `CAL_MIRROR_SETUP_CODE` | web | secret key `setup-code`, optional |
| `CAL_MIRROR_PORT`, `CAL_MIRROR_TLS_CERT`, `CAL_MIRROR_TLS_KEY` | web | `8443`, the pod's certificate and key |
| `FORWARDED_ALLOW_IPS` | web | the ingress's address range, for k3s the pod network `10.42.0.0/16` |

The Google OAuth client needs both redirect URIs: `https://<host>/google/callback` and
`https://<host>/signin/google/callback`.

`FORWARDED_ALLOW_IPS` is read by uvicorn: from those addresses it takes the client address from
`X-Forwarded-For`. Without it every visitor reaches the app from the ingress's address, so the
sign-in lock is one lock for everyone and a stranger's failures lock the owner out. The ingress
must set `X-Forwarded-For` itself and not pass on a client's own; Traefik does this by default.

## Setup code

On a fresh install the first-run page `/setup` asks for `CAL_MIRROR_SETUP_CODE`. Generate one with

```sh
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

and put it in the secret as `setup-code` (for a plain secret,
`kubectl -n cal-mirror create secret generic cal-mirror-secrets --from-literal=setup-code=<code> ...`).
Read it back with:

```sh
kubectl -n cal-mirror get secret cal-mirror-secrets -o jsonpath='{.data.setup-code}' | base64 -d
```

Without it `/setup` answers 403, which is what an install that already has a password wants.

## Upgrade

Set both images to the new release in the GitOps repository and let it apply. The database
migrates itself when the web app starts.

From 0.1.x to 0.2.0:

- Add `CAL_MIRROR_SIGNIN_REDIRECT` and `FORWARDED_ALLOW_IPS` to the web pod, and add the sign-in
  redirect URI to the Google OAuth client.
- A second factor is now required: if you have no TOTP or passkey, the next sign-in takes you to
  `/account/2fa` to enrol one.

## Certificate renewal

The web pod restarts itself within a minute of a renewed pod certificate reaching it, with no
command to run.
