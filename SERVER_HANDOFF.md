# Server Admin Handoff — SMT Deployment

**Audience:** the person with SSH access to the VPS hosting
`ordersheet.flendergroup.com`.

This is a one-time deployment of the new **Social Media Tracker** at
`smt.flendergroup.com`. SMT runs as additional Docker Compose services
alongside the existing Order Sheet app, on the same VPS, with its **own
isolated** Postgres + MinIO containers. Nothing about the existing Order
Sheet deployment changes.

Total time: ~10 minutes of your hands, plus a few minutes of waiting.

---

## What you'll be doing (5 steps)

### 1. Add a DNS record — 2 min

At the DNS provider for `flendergroup.com`, add an **A** record:

```
Name      smt
Type      A
Value     <same IP as ordersheet.flendergroup.com>
TTL       300
```

Wait a couple of minutes, then verify:

```bash
dig +short smt.flendergroup.com
# should return the VPS IP
```

### 2. Add env vars to the server's .env — 1 min

SSH into the VPS, go to the deploy directory (the one containing
`docker-compose.yml`), and append these five lines to `.env`:

```
SMT_DB_PASSWORD=<provided separately by Kareem>
MINIO_ROOT_USER=smtadmin
MINIO_ROOT_PASSWORD=<provided separately by Kareem>
SMT_S3_BUCKET=smt-uploads
SMT_URL=https://smt.flendergroup.com
```

> Kareem will send `SMT_DB_PASSWORD` and `MINIO_ROOT_PASSWORD` via a
> secure channel (1Password, Bitwarden, signal, etc.). They are not
> in any git repo.

The existing `POSTGRES_PASSWORD` / `SECRET_KEY` / etc. are already in
`.env` and stay as-is.

### 3. Deploy — automatic, ~3 min

Kareem merges PR
[#2](https://github.com/kareemelsenosy/flender-platform/pull/2) into
`main`. The existing GitHub Actions workflow
(`.github/workflows/deploy.yml`) automatically:

- Runs the test suite
- SCPs the repo to this VPS
- Runs `docker compose build --no-cache && docker compose up -d`

The first build adds about 3 minutes (a fresh Next.js production build for
the new `smt` service). New containers `smt`, `smt-db`, `minio` come up.

**Nothing on your end during this step** — just wait for the workflow to
finish green.

Verify after deploy:

```bash
cd /path/to/deploy
docker compose ps
# expected: app, db, smt, smt-db, minio  — all Up
docker compose logs --tail=50 smt
# expected: Next.js  Local:  http://0.0.0.0:3000  — ready
```

The SMT database schema and the MinIO bucket are created automatically on
the first request. No migration step needed.

### 4. Issue an SSL certificate — 1 min

After DNS has propagated (step 1) and the deploy is up (step 3):

```bash
sudo certbot --nginx -d smt.flendergroup.com
```

Choose **redirect** when prompted. Certbot will add the SSL block and the
HTTP-to-HTTPS redirect to the nginx server file.

### 5. Sync the certbot changes back to the repo — 1 min

Certbot edits `/etc/nginx/sites-available/ordersheet.flendergroup.com`
in place. To keep the repo's `nginx.conf` in sync (so the next deploy
doesn't roll back SSL), copy the updated server file back:

```bash
sudo cp /etc/nginx/sites-available/ordersheet.flendergroup.com /tmp/nginx.conf
sudo chown $USER /tmp/nginx.conf
```

Send the contents of `/tmp/nginx.conf` to Kareem and he'll commit it. Or,
if you have repo access:

```bash
cd /path/to/repo
sudo cp /etc/nginx/sites-available/ordersheet.flendergroup.com nginx.conf
git add nginx.conf
git commit -m "Sync nginx config after certbot for smt.flendergroup.com"
git push
```

---

## Verification

When everything is up:

| Check | Expected |
|---|---|
| `https://smt.flendergroup.com` | Loads the SMT dashboard |
| Log into `https://ordersheet.flendergroup.com`, hit `/` | AI Tools hub with two tiles |
| Click **Social Media Tracker** tile | Opens `https://smt.flendergroup.com` in a new tab |
| `docker compose ps` | `app`, `db`, `smt`, `smt-db`, `minio` — all `Up` |
| `docker compose logs --tail=30 smt` | No tracebacks; `Ready in <Xs>` |
| Browser: try uploading a screenshot through the SMT UI | Should succeed; file stored in MinIO |

---

## Troubleshooting

**Containers won't start: "Set SMT_DB_PASSWORD in .env"**
The new env vars from step 2 are missing. Re-check `.env` has the 5 SMT
lines and no typos.

**`smt` container restarts repeatedly**
```bash
docker compose logs smt | tail -100
```
Look for `DATABASE_URL is not set` (missing env var) or `ECONNREFUSED`
to `smt-db:5432` (smt-db hasn't passed healthcheck — wait, then
`docker compose restart smt`).

**MinIO bucket not created on first upload**
SMT creates it lazily. If a request fails with `NoSuchBucket`, just retry;
the storage layer resets and re-tries on each failure. If it persists:
```bash
docker compose exec minio mc alias set local http://localhost:9000 \
  $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD
docker compose exec minio mc mb local/smt-uploads
```

**Certbot fails with "Failed to authenticate domain"**
DNS hasn't propagated yet. Wait 5 minutes and retry. Check with
`dig +short smt.flendergroup.com`.

**Existing Order Sheet broke**
Shouldn't be possible — SMT uses fully isolated containers and volumes.
But to confirm, the existing services are untouched:
```bash
docker compose ps app db
# both still Up; volumes postgres_data + app_uploads + app_data unchanged
```

If anything still looks off, send Kareem the output of:
```bash
docker compose ps
docker compose logs --tail=50 app smt smt-db minio
```
