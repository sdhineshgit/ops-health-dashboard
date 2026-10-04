# Daily Health

A small internal site for daily operational health checks. Windows, VMware, Storage, Backup, Database, and Linux each file a RAG status. Management sees one colour per track for any day, and can open the history behind it.

A track is **red** when any check is red, and **amber** when any remaining check is amber. Otherwise it is **green**. Red and amber cannot be saved without a comment that describes the issue.

Daily filing, the management board, and history are open. Adding or removing tracks and checks requires the admin login. Run the site on the internal network only, because the comments describe live operational issues.

Change the admin password before anyone else can reach the site. The defaults in `docker-compose.yml` are username `admin` and password `change-me`. Override them when you start the container:

```powershell
$env:ADMIN_PASSWORD="a-long-password"
$env:SESSION_SECRET="a-long-random-string"
docker compose up -d --build
```

## Run in Docker

```powershell
docker compose up -d --build
```

Open http://localhost:8080.

The database is the Docker volume `health-data`, mounted at `/data/health.db`. Submissions survive container rebuilds. The calendar day follows `TZ` in `docker-compose.yml` (default `Asia/Kolkata`). Change that value if the operations day is judged in another timezone, then recreate the container.

To stop it:

```powershell
docker compose down
```

`docker compose down` keeps the volume. `docker compose down -v` deletes the saved checks.

## Deploy to k3s

Pushes to `main` run `.github/workflows/deploy.yml`. The workflow runs the tests, publishes the image to GitHub Container Registry, then applies `deploy/k3s` with kubectl. The database is a 1Gi volume on the k3s `local-path` storage class, mounted at `/data`. One pod is used, because SQLite has a single writer.

The cluster must reach the registry, and the runner must reach the k3s API. Copy `/etc/rancher/k3s/k3s.yaml` from the server, change `server: https://127.0.0.1:6443` to the address the runner can use, then store the file as a base64 GitHub secret:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("k3s.yaml"))
```

GitHub Actions secrets:

| Secret | Purpose |
| --- | --- |
| `KUBE_CONFIG` | Base64 kubeconfig whose `server` address the runner can reach |
| `ADMIN_PASSWORD` | Admin login password |
| `SESSION_SECRET` | Cookie signing secret |
| `GHCR_PULL_TOKEN` | Personal access token with `read:packages`, so k3s can pull a private image |
| `ADMIN_USERNAME` | Optional. Defaults to `admin` |

Optional repository variable `HEALTH_HOST` replaces the ingress host `health.internal`. k3s Traefik serves that host on port 80. Point DNS, or an internal hosts entry, at a k3s node.

To apply the same manifests by hand after `docker build -t ops-health-dashboard:local .` and `k3s ctr images import`:

```powershell
kubectl apply -f deploy/k3s/namespace.yaml
kubectl -n ops-health create secret generic ops-health-config --from-literal=ADMIN_USERNAME=admin --from-literal=ADMIN_PASSWORD=a-long-password --from-literal=SESSION_SECRET=a-long-random-string
kubectl apply -k deploy/k3s
```

Remove `imagePullSecrets` from `deploy/k3s/deployment.yaml` when the image is already on the node and no registry pull is required. `deploy/k3s/secret.example.yaml` shows the secret shape and is not applied by Kustomize.

## Run without Docker

```powershell
py -m pip install -r requirements.txt
$env:ADMIN_PASSWORD="a-long-password"
$env:SESSION_SECRET="a-long-random-string"
py -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

The database file is `data/health.db`.

## How teams use it

- **My track** — pick the team, mark every check green, amber, or red, and save. Today can be updated. Earlier days can be opened, but they cannot be changed.
- **Management** — one card per track for the selected day. Open a card to read the checks and the red or amber comments.
- **History** — a colour grid of recent days. Choose a cell to open that day.
- **Admin** — sign in, then add, rename, or remove tracks and checks. The same page sets the company logo and banner colour. If a removed track or check was already filed, those past days stay visible. Restore brings it back onto the daily form.

## Tests

```powershell
py -m pip install -r requirements-dev.txt
py -m pytest
```
