# Azure VM Deployment Plan

Manual deployment to a single Azure Linux VM via SSH/SCP — no Docker,
no CI/CD, PostgreSQL and Redis running on the same VM as the app.

## Is the codebase ready?

Yes, with a few things to set up that don't exist on the dev machine
by default. Nothing found needs a code change to deploy:

- `normalize_database_url()` (`db/engine.py`) only rewrites bare
  `postgresql://`/`mysql://` schemes — no Supabase-specific logic.
  Works identically against a self-hosted Postgres URL.
- Config (`config.py`) resolves `.env` by an absolute path anchored to
  the repo root, not the process's CWD — safe regardless of what a
  systemd unit's `WorkingDirectory` is.
- No Dockerfile, no npm/Node build step anywhere — static HTML/CSS/JS
  served directly via `FileResponse`, nothing to compile.
- uvicorn has no hardcoded `--host`, defaults to `127.0.0.1` — already
  the right posture for sitting behind a reverse proxy.

Three real gaps, all closed by this plan (not code changes):

1. **Chromium isn't installed by `pip install`** — Playwright's browser
   binary is a separate download, undocumented anywhere in the repo.
2. **No `.env.example`** existed — added at repo root; copy it to
   `.env` and fill in real values.
3. **`STORAGE_LOCAL_PATH` defaults to a relative path** (`./invoice_pdfs`)
   — must be set to an absolute path in prod `.env`, since a systemd
   unit's CWD isn't guaranteed to match dev assumptions.

One thing worth knowing but not fixing now: `BrowserConfig.auth_state_path`
(in `config.py`) is defined but unused — the actual session file path
used everywhere (`web/runner.py`'s `STATE_FILE`, all manual scripts) is
the repo-root `.auth_state.json`, resolved independently via
`automation/session_state.py`. Harmless, just two definitions where
one would do.

## What you need beyond `requirements.txt`

| Thing | Why | Install |
|---|---|---|
| PostgreSQL server | the DB, on the same VM | `apt install postgresql postgresql-contrib` |
| Redis server | RQ's queue backend | `apt install redis-server` |
| nginx | TLS termination + reverse proxy (incl. WebSocket) | `apt install nginx` |
| certbot | free TLS cert (Let's Encrypt) | `apt install certbot python3-certbot-nginx` |
| Python 3.11+ | matches what's used in dev (3.13); 3.11/3.12/3.13 all fine | usually already on the image, or `apt install python3 python3-venv` |
| Chromium + its OS libs | Playwright's actual browser — **not** installed by `pip install playwright` | `venv/bin/python -m playwright install --with-deps chromium` (run once, needs sudo for the `--with-deps` system packages) |
| git (optional) | only if you want `git pull` for later updates instead of re-scp'ing everything | `apt install git` |

Nothing else — no Node, no Docker, no extra Python packages beyond
`requirements.txt`.

## Step-by-step

### 1. Provision the VM
- Ubuntu 22.04 or 24.04 LTS, Azure Standard_B2s or similar (2 vCPU /
  4GB RAM is enough to start — headless Chromium + Postgres + Redis
  all on one box).
- Azure NSG: allow **22** (SSH, restrict source to your own IP),
  **80** and **443** (HTTP/HTTPS, for nginx + the Let's Encrypt
  challenge). Do **not** open Postgres (5432) or Redis (6379)
  externally — both bind to localhost only, which is correct; leave
  them off the NSG entirely.
- A DNS A record pointing some domain/subdomain at the VM's public IP
  — Aviara Connect needs a real HTTPS domain to POST to and to build
  `callback_url` values against (plain IP + self-signed cert won't
  work for their side, and Let's Encrypt needs a resolvable domain for
  its challenge).

### 2. Base OS setup (as root/sudo over SSH)
```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib redis-server nginx certbot python3-certbot-nginx git build-essential

# Dedicated, unprivileged service account — never run the app as root.
useradd --system --create-home --shell /bin/bash dealercenter
mkdir -p /opt/dealercenter-agent
chown dealercenter:dealercenter /opt/dealercenter-agent
```

### 3. PostgreSQL setup
```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE USER dealercenter WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE "browser-automation" OWNER dealercenter;
SQL
```
Default Postgres config only listens on localhost and uses `peer`/`md5`
auth for local connections — fine as-is for this single-VM setup, no
`pg_hba.conf`/`postgresql.conf` changes needed unless you want remote
access (you don't).

Note the double quotes around `"browser-automation"` — Postgres
identifiers with a hyphen need quoting in raw SQL (unquoted, the
hyphen parses as a minus sign). The connection-string form in
`DATABASE_URL` doesn't need this — a hyphen in a URL path segment is
fine as-is.

### 4. Redis setup
Ships ready to go after `apt install redis-server` — it starts and
enables itself automatically on Debian/Ubuntu, bound to `127.0.0.1`
by default. Confirm with `redis-cli ping` → `PONG`. No config changes
needed.

### 5. Directory structure — releases + shared + current

Not a flat copy of the repo — a `releases/` + `shared/` + `current`
symlink layout, so every future deploy is near-zero-downtime with a
one-command rollback:

```
/opt/dealercenter-agent/
├── releases/
│   ├── 2026-11-18-210000/          ← one full code snapshot per deploy
│   │   ├── backend/ (own venv, built fresh each release)
│   │   ├── .env -> ../../shared/.env                      (symlink)
│   │   └── .auth_state.json -> ../../shared/.auth_state.json  (symlink)
│   └── 2026-11-19-093000/
├── shared/
│   ├── .env                         ← real secrets, lives ONCE
│   ├── .auth_state.json             ← Playwright session, lives ONCE
│   └── storage_data/                ← invoice PDFs, lives ONCE
└── current -> releases/2026-11-19-093000/   ← atomic symlink, "what's live"
```

Why `.env`/`.auth_state.json` are symlinks rather than files directly
under `shared/`: the app resolves both paths as "2-3 directories above
wherever the running code file lives" (`config.py`, `runner.py`) —
that lands on each release's *own* root, not a fixed top-level path.
Symlinking the one real file into every release's root makes that
resolution always hit the same physical file regardless of which
release is current — including writes: `save_state()`'s
`path.write_text(...)` follows the symlink and overwrites the real
target in place (POSIX write-through-symlink semantics), so a fresh
MFA login on any release updates the one shared `.auth_state.json`,
and every other release's symlink sees that update immediately.

Permissions:

| Path | Owner | Mode | Why |
|---|---|---|---|
| `shared/.env` | `dealercenter:dealercenter` | `600` | secrets |
| `shared/.auth_state.json` | `dealercenter:dealercenter` | `600` | effectively a bearer credential — bypasses MFA if leaked |
| `shared/storage_data/` | `dealercenter:dealercenter` | `700` | invoice PDFs; nginx never serves this directly, only the app's own processes touch it |

One-time setup, via `deploy/bootstrap.sh` (already shipped in the
repo, scp it over first):
```bash
scp -i /path/to/key.pem deploy/bootstrap.sh azureuser@<vm-ip>:/tmp/
ssh -i /path/to/key.pem azureuser@<vm-ip> "bash /tmp/bootstrap.sh"
```
Then place the real secrets (never part of any code sync, never
committed):
```bash
scp -i /path/to/key.pem .env.production azureuser@<vm-ip>:/tmp/.env
ssh -i /path/to/key.pem azureuser@<vm-ip> "sudo mv /tmp/.env /opt/dealercenter-agent/shared/.env && sudo chown dealercenter:dealercenter /opt/dealercenter-agent/shared/.env && sudo chmod 600 /opt/dealercenter-agent/shared/.env"

# Your existing, already-logged-in session — uploading this means the
# VM's first run skips MFA entirely instead of needing to redo it.
scp -i /path/to/key.pem .auth_state.json azureuser@<vm-ip>:/tmp/.auth_state.json
ssh -i /path/to/key.pem azureuser@<vm-ip> "sudo mv /tmp/.auth_state.json /opt/dealercenter-agent/shared/.auth_state.json && sudo chown dealercenter:dealercenter /opt/dealercenter-agent/shared/.auth_state.json && sudo chmod 600 /opt/dealercenter-agent/shared/.auth_state.json"
```

### 6. Ship a release and deploy it

From your local machine — rsync the code into a fresh, timestamped
release directory (not over `current` directly):
```bash
RELEASE=$(date +%Y-%m-%d-%H%M%S)
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'storage_data' --exclude '.auth_state.json*' --exclude '.env*' \
  -e "ssh -i /path/to/key.pem" \
  /home/pratyush/Desktop/Aviara_Labs/Projects/DealerCenterAgent/ \
  azureuser@<vm-ip>:/tmp/release/

ssh -i /path/to/key.pem azureuser@<vm-ip> "
  sudo mkdir -p /opt/dealercenter-agent/releases/$RELEASE
  sudo rsync -a /tmp/release/ /opt/dealercenter-agent/releases/$RELEASE/
  sudo chown -R dealercenter:dealercenter /opt/dealercenter-agent/releases/$RELEASE
  sudo -u dealercenter /opt/dealercenter-agent/releases/$RELEASE/deploy/deploy.sh /opt/dealercenter-agent/releases/$RELEASE
"
```
`deploy.sh` (shipped in `deploy/`, already executable) does everything
from there: symlinks the shared secrets in, builds a fresh venv,
installs deps + Playwright's Chromium, runs `alembic upgrade head`
**against the real DB while the old release is still live** (so a bad
migration fails the deploy, not the site), flips `current` atomically,
restarts all 4 services, health-checks the result, and **automatically
rolls back** to the previous release if the health check fails. It
also prunes anything older than current+previous at the end of a
successful run.

Read the script's own comments (`deploy/deploy.sh`) for the exact
step-by-step — it's short and meant to be read, not just trusted
blindly.

### 7. systemd services
Four units, written in `deploy/systemd/`, each pointed at
`/opt/dealercenter-agent/current/backend/...` (never a fixed
timestamped path — that's what makes the symlink flip actually take
effect on restart):
```bash
sudo cp /opt/dealercenter-agent/current/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick
sudo systemctl status dealercenter-web   # confirm it's running
journalctl -u dealercenter-web -f        # live log tail
```
Each is `Restart=on-failure`, so a crash (e.g. a transient Postgres
blip) restarts it automatically; `journalctl -u <unit>` is the log
viewer for all four (systemd captures stdout/stderr, no extra logging
setup needed). `enable` also means all four come back up automatically
on a VM reboot.

### 8. nginx + TLS
```bash
sudo cp /opt/dealercenter-agent/current/deploy/nginx/dealercenter-agent.conf /etc/nginx/sites-available/dealercenter-agent
# Edit it first: replace your-domain.example.com with the real domain.
sudo ln -s /etc/nginx/sites-available/dealercenter-agent /etc/nginx/sites-enabled/

# Bump worker_connections for sustained webhook burst volume (500-1k+
# deliveries) — this directive is global, in nginx.conf's events{}
# block, not something a per-site config can override:
sudo sed -i 's/worker_connections .*/worker_connections 2048;/' /etc/nginx/nginx.conf

sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.example.com
```
Certbot rewrites the config in place to add the HTTPS server block and
sets up auto-renewal (a systemd timer, installed automatically — no
cron needed).

Three things already baked into `deploy/nginx/dealercenter-agent.conf`
for the 500-1k+ webhook volume this needs to handle:
- `client_max_body_size 25m` — nginx's 1MB default would 413-reject
  any webhook payload with an embedded base64 PDF.
- `proxy_read_timeout 120s` on the main location — headroom so nginx
  doesn't give up on a request that's just waiting its turn in
  FastAPI's threadpool during a burst.
- The `/ws/` location's `Upgrade`/`Connection` headers — required for
  the Live Run page's screencast WebSocket; without them nginx proxies
  it as plain HTTP and the socket silently fails to connect.

The real bottleneck at 500-1k+ concurrent deliveries is the app's own
threadpool/RQ workers, not nginx — these three settings just make sure
nginx itself isn't the thing capping out first.

### 9. Verify
```bash
curl -I https://your-domain.example.com/           # the Live Run page, 200
curl -s https://your-domain.example.com/invoices    # should return JSON, not an error
```
Then from a browser: load the dashboard over HTTPS, confirm all 4
pages render and the tables load. Do **not** click "Run" or "Login /
Refresh Session" from here without first confirming the `.env` DB/
Redis wiring is correct — same standing caution as dev: verify
plumbing before triggering a real DealerCenter session.

### 10. Give Aviara Connect the real webhook URL
Once confirmed working, the production webhook endpoint for them to
POST to is:
```
https://your-domain.example.com/webhooks/invoices
```
They'll need to build `callback_url` values against this same domain
(matching the pattern `.../organization/connectors/dealercenter/callback/<token>/`)
— this is a coordination step with them, not something to fix in code.

## Redeploying after a code change

Exactly the same as step 6 above, every time — a fresh timestamped
release dir, then `deploy.sh`:
```bash
RELEASE=$(date +%Y-%m-%d-%H%M%S)
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'storage_data' --exclude '.auth_state.json*' --exclude '.env*' \
  -e "ssh -i /path/to/key.pem" \
  /home/pratyush/Desktop/Aviara_Labs/Projects/DealerCenterAgent/ \
  azureuser@<vm-ip>:/tmp/release/

ssh -i /path/to/key.pem azureuser@<vm-ip> "
  sudo mkdir -p /opt/dealercenter-agent/releases/$RELEASE
  sudo rsync -a /tmp/release/ /opt/dealercenter-agent/releases/$RELEASE/
  sudo chown -R dealercenter:dealercenter /opt/dealercenter-agent/releases/$RELEASE
  sudo -u dealercenter /opt/dealercenter-agent/releases/$RELEASE/deploy/deploy.sh /opt/dealercenter-agent/releases/$RELEASE
"
```
If `deploy.sh` prints `HEALTH CHECK FAILED`, it's already rolled back
to the previous release by the time you see that message — the site
never actually went down. If the failure was a migration (not a
startup crash), the script stops *before* the flip, same guarantee.

**Manual rollback**, if you ever need to roll back a release that
*did* pass its health check but turned out to be wrong in some other
way (a real bug the smoke test can't catch):
```bash
ssh -i /path/to/key.pem azureuser@<vm-ip> "
  PREVIOUS=\$(ls -1dt /opt/dealercenter-agent/releases/*/ | sed -n '2p')
  sudo ln -sfn \"\${PREVIOUS%/}\" /opt/dealercenter-agent/current
  sudo systemctl restart dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick
"
```

## Things deliberately left out of this plan (not needed for v1, flag if you want them)

- **DB backups**: no automated `pg_dump` cron set up. Cheap to add
  later (`pg_dump` + a daily cron + off-box copy); not blocking a
  first deployment.
- **Multiple workflow-worker processes** (the original design doc's
  "future scale path" — N workers pulled from the same RQ queue for
  higher invoice throughput): the current setup is one process = one
  browser = one invoice at a time, matching dev. Scaling that up later
  is a process-count change (more systemd units from the same
  `run_workflow_worker.py`, no code change), not something this plan
  sets up preemptively.
- **fail2ban / extra SSH hardening**: standard Azure NSG + key-only
  SSH (no password auth) is assumed sufficient for now.
- **Log rotation beyond journald's own default retention**: systemd's
  journal already rotates by default; revisit only if disk usage
  becomes a problem.
