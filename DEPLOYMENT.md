# Deployment — Zero-Touch from GitHub

> This supersedes the older `SERVER_HANDOFF.md`, which described a separate
> `smt.flendergroup.com` subdomain. The decision was made to keep
> everything on the existing `ordersheet.flendergroup.com` domain instead —
> simpler, same SSL cert, no server admin involvement.

The Social Media Tracker deploys with **no server admin involvement**. The
existing GitHub Actions workflow handles everything: copying the code,
writing the new env vars into the server's `.env`, building the new Docker
images, starting the new containers, and reloading nginx.

The server admin **does not need to touch anything** on the VPS.

## Architecture

```
Same VPS, same domain — no DNS or SSL changes.

ordersheet.flendergroup.com  (host nginx + existing SSL cert)
├── /              → existing app container (Order Sheet + AI Tools hub)
├── /order-sheet/* → existing app container (Order Sheet tool)
└── /smt/*         → new smt container (Social Media Tracker, Next.js)

Behind the scenes (docker compose):
  app, db                    (existing Order Sheet stack — unchanged)
  smt, smt-db, minio         (new — fully isolated)
```

SMT runs in its own containers with its own Postgres database and its own
MinIO object storage. It cannot read from or write to the Order Sheet's
data. Nothing about the existing deployment changes.

## What you do — three steps, all from GitHub's web UI

Total time: ~3 minutes of your hands, then ~5 minutes of waiting.

### 1. Add three GitHub Actions secrets

Go to:
**Settings → Secrets and variables → Actions → New repository secret**
at https://github.com/kareemelsenosy/flender-platform/settings/secrets/actions

Add these three secrets:

| Name | Value |
|---|---|
| `SMT_DB_PASSWORD` | a strong random password (see below) |
| `MINIO_ROOT_USER` | `smtadmin` (or any username you choose) |
| `MINIO_ROOT_PASSWORD` | another strong random password |

Generate strong passwords with, for example:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(36))"
```

These are stored encrypted in GitHub. The deploy workflow reads them and
writes them into the server's `.env` automatically — they never appear in
logs or in the repo.

### 2. Merge the PR

Merge `feature/ai-tools-hub` into `main` — one click in the PR UI.

### 3. Watch the deploy

GitHub Actions runs automatically on push to `main`. Open the **Actions**
tab to watch the live progress:

```
Run smoke tests        ~1 min
SCP repo to VPS        ~30 sec
docker compose build   ~3 min  (first time only — fresh Next.js image)
docker compose up -d   ~10 sec
nginx reload           ~5 sec
```

Total ~5 minutes.

## What happens on the server during the deploy

The workflow script does this on its own — no admin needed:

1. Receives the new code via SCP
2. Writes the three new secrets into the server's `.env`
3. Runs `docker compose pull && docker compose build --no-cache && docker compose up -d`
   - Builds the new `smt` Docker image (Next.js production)
   - Pulls `postgres:16-alpine` and `minio/minio` images
   - Starts the new `smt`, `smt-db`, and `minio` containers
4. Syncs the updated `nginx.conf` (now containing the `location /smt/` block)
   to `/etc/nginx/sites-available/ordersheet.flendergroup.com`
5. Reloads nginx

The existing `app` and `db` containers keep running with no interruption —
docker compose only restarts services whose config changed.

## Verification — after the deploy completes

| Check | Where | Expected |
|---|---|---|
| Order Sheet still works | `https://ordersheet.flendergroup.com/order-sheet` | Loads normally |
| AI Tools hub renders | `https://ordersheet.flendergroup.com/` (logged in) | Two tiles |
| Click **Social Media Tracker** tile | New tab | Loads `/smt`, shows the dashboard |
| Upload a screenshot in SMT | SMT UI | Succeeds, file appears in record |
| Close the session, click Export | SMT UI | ZIP downloads with the file inside |

## SSL — already covered

`/smt/*` is served at `https://ordersheet.flendergroup.com/smt/*` — the
existing SSL cert already covers it. No new certificate, no `certbot` run,
no DNS records.

## Rollback

If anything looks wrong:

1. In GitHub, click **Revert** on the merge commit. This creates a revert PR.
2. Merge the revert PR.
3. GitHub Actions re-deploys the previous code automatically.

The new SMT containers will keep running but the hub tile and `/smt` route
will disappear (because the older nginx config doesn't route to them).

## Troubleshooting

**Deploy fails with `Set SMT_DB_PASSWORD in .env`**

The GitHub secrets weren't added before the workflow ran. Add them
(step 1 above) and re-run the workflow from the Actions tab
(**Actions → Deploy → Re-run all jobs**).

**The `smt` container restarts repeatedly**

In the GitHub Actions deploy log, the next-to-last step shows the docker
compose output. If unclear, ask the server admin (or anyone with SSH) to
run:

```bash
docker compose logs --tail=50 smt
```

Most common cause: typo in a secret value. Re-set the secret in GitHub
and re-run the workflow.

**`/smt` returns 404 from nginx**

The nginx reload step in the workflow may need `sudo` permissions that
aren't configured for the deploy user. The Actions log will say so.

One-time fix (server admin): run `sudo visudo` and add:

```
deployuser ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /bin/systemctl, /bin/cp
```

(replace `deployuser` with the actual deploy username).

**Existing Order Sheet broke after deploy**

Shouldn't be possible — SMT runs in fully isolated containers with its own
database and storage. But to confirm everything is up, the server admin
can run `docker compose ps`. All five services should be `Up`:

```
NAME       IMAGE                      STATUS
app        flender-platform-app       Up
db         postgres:16-alpine         Up (healthy)
smt        flender-platform-smt       Up
smt-db     postgres:16-alpine         Up (healthy)
minio      minio/minio:latest         Up
```

---

The whole flow is: **add 3 secrets → merge PR → wait 5 minutes → it's
live**. No SSH, no DNS, no certbot.
