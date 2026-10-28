# Install on k3s

Everything below runs from your laptop with `kubectl` pointed at the k3s cluster.
The build does not apply anything; you do.

## 1. Build and push the images

```sh
docker build -f Dockerfile.web -t <registry>/cal-mirror-web:<tag> .
docker push <registry>/cal-mirror-web:<tag>
docker build -f Dockerfile.worker -t <registry>/cal-mirror-worker:<tag> .
docker push <registry>/cal-mirror-worker:<tag>
```

Set the same registry and tag in `deploy/k8s/kustomization.yaml` (`images:`).

## 2. TLS

Install cert-manager if it is absent. For DNS-01, create your own ClusterIssuer
with the DNS provider token (the token lives in the cluster, never in this repo)
and set `TLS_ISSUER_KIND=ClusterIssuer` and `TLS_ISSUER_NAME=<your issuer>`.

For self-signed, set `TLS_ISSUER_KIND=Issuer` and
`TLS_ISSUER_NAME=cal-mirror-selfsigned` (shipped in the base).

## 3. Postgres

On the Postgres server (existing or separate):

```sql
CREATE ROLE cal_mirror LOGIN PASSWORD '<password>';
CREATE DATABASE cal_mirror OWNER cal_mirror;
```

## 4. Namespace and secret

```sh
kubectl create namespace cal-mirror
```

Generate the Fernet key:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```sh
kubectl -n cal-mirror create secret generic cal-mirror-secrets \
  --from-literal=secret-key=<fernet key> \
  --from-literal=pg-password=<password>
```

## 5. Archive folder

On the node named by `USB_NODE`, prepare the folder at `ARCHIVE_HOST_PATH`
owned by uid 10001:

```sh
sudo mkdir -p /var/lib/cal-mirror/archive
sudo chown 10001 /var/lib/cal-mirror/archive
```

Archive destinations in the web app are paths under `/archive`.

## 6. Settings, preview, apply

Edit `deploy/k8s/settings.properties` (and `namespace`, registry and tag in
`deploy/k8s/kustomization.yaml`), then:

```sh
kubectl kustomize deploy/k8s
kubectl apply -k deploy/k8s
kubectl -n cal-mirror get pods
kubectl -n cal-mirror logs deploy/cal-mirror-worker
```

## Settings

| Setting | Meaning |
| --- | --- |
| `HOST_NAME` | DNS name of the web app; goes into the certificate. |
| `OAUTH_REDIRECT` | Google OAuth redirect URI, ending `/google/callback`. |
| `LAN_CIDR` | Network allowed to reach the web app. |
| `VPN_CIDR` | VPN network allowed to reach the web app. |
| `TLS_ISSUER_KIND` | `ClusterIssuer` (DNS-01) or `Issuer` (self-signed). |
| `TLS_ISSUER_NAME` | Name of the issuer, or `cal-mirror-selfsigned`. |
| `PG_NAMESPACE` | Namespace of the Postgres server; the network policies allow it. |
| `PG_HOST` | Postgres host name. |
| `PG_DB` | Postgres database name. |
| `PG_USER` | Postgres user name. |
| `USB_NODE` | Node that holds the USB drive; the worker is pinned to it. |
| `ARCHIVE_HOST_PATH` | Folder on that node, mounted at `/archive` in the worker. |
| `namespace` | Namespace for everything (`kustomization.yaml`). |
| registry | `newName` of both images (`kustomization.yaml`). |
| tag | `newTag` of both images (`kustomization.yaml`). |

## Certificate renewal

The web pod restarts itself within a minute of the renewed certificate reaching the pod, with no command to run.

## Accepted gaps

egress HTTPS is open to all addresses because a NetworkPolicy cannot filter by host name.
