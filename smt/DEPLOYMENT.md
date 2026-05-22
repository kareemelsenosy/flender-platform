# SMT Deployment Guide

The Social Media Tracker deploys as part of the `flender-platform` monorepo,
alongside the Order Sheet app, on the same VPS via Docker Compose + nginx.

## Architecture

```
VPS
├── nginx (host)
│   ├── ordersheet.flendergroup.com  ->  localhost:8000  (app container)
│   └── smt.flendergroup.com         ->  localhost:3001  (smt container)
└── docker compose
    ├── app        FastAPI — Order Sheet + AI Tools hub
    ├── db         Postgres — Order Sheet data
    ├── smt        Next.js — Social Media Tracker
    ├── smt-db     Postgres — SMT data (isolated)
    └── minio      S3-compatible object storage — SMT uploads
```

SMT's database and object storage are **fully isolated** from the Order Sheet —
separate containers, separate volumes. Nothing the SMT does can affect the
Order Sheet's data.

## One-time setup

### 1. DNS

Add an `A` record:

```
smt.flendergroup.com   ->   <your VPS IP>
```

### 2. Server environment variables

On the server, add these to the existing `.env` file in the deploy directory
(`POSTGRES_PASSWORD` and `SECRET_KEY` are already there):

```
SMT_DB_PASSWORD=<a strong password>
MINIO_ROOT_USER=smtadmin
MINIO_ROOT_PASSWORD=<a strong password>
SMT_S3_BUCKET=smt-uploads
SMT_URL=https://smt.flendergroup.com
```

### 3. Deploy

Merge the `feature/ai-tools-hub` branch into `main`. The existing GitHub Actions
workflow (`.github/workflows/deploy.yml`) copies the repo to the server and runs
`docker compose build && docker compose up -d`. The new `smt`, `smt-db` and
`minio` containers come up automatically.

The SMT database schema and the MinIO bucket are created automatically on first
use — no migration step needed.

### 4. SSL certificate

After DNS has propagated and the first deploy is done:

```bash
sudo certbot --nginx -d smt.flendergroup.com
```

Certbot adds the SSL block and HTTP->HTTPS redirect to the nginx server file.
Then copy the updated `/etc/nginx/sites-available/ordersheet.flendergroup.com`
back into this repo's `nginx.conf` and commit it — so future deploys keep SSL.

## Verifying

- `https://ordersheet.flendergroup.com` → log in → AI Tools hub
- Click **Social Media Tracker** → opens `https://smt.flendergroup.com`
- `docker compose ps` on the server → `app`, `db`, `smt`, `smt-db`, `minio` all `Up`

## MinIO admin console

The MinIO console is bound to `127.0.0.1:9001` on the server (not public).
To reach it, SSH-tunnel:

```bash
ssh -L 9001:localhost:9001 user@your-vps
# then open http://localhost:9001
```

## Local development

```bash
cd smt
cp .env.local.example .env.local   # then edit
npm install
npm run dev
```

`.env.local` needs `DATABASE_URL` (a local Postgres) and the `S3_*` vars
(a local MinIO, or any S3-compatible storage).
