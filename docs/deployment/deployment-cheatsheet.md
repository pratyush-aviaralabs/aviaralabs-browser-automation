# Deployment Cheat Sheet — DealerCenter Agent (Azure VM)

Reference values used throughout this sheet:

| Name | Value |
|---|---|
| VM public IP | `74.235.98.128` |
| VM DNS | `aviaralabs-browser-automation.eastus.cloudapp.azure.com` |
| SSH key | `~/.ssh/browser_automation` |
| SSH user | `azureuser` |
| App base dir | `/opt/dealercenter-agent` |
| Service user | `dealercenter` |
| Services | `dealercenter-web`, `dealercenter-ingestion-worker`, `dealercenter-workflow-worker`, `dealercenter-scheduler-tick` |

All commands below assume you're running them from the repo root (`DealerCenterAgent/`) on your local machine unless marked **[on VM]**.

---

## 1. Connect

| # | Command | Why |
|---|---|---|
| 1.1 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128` | Open an interactive shell on the VM. Use this for anything exploratory; use the one-liner `ssh ... "command"` form (below) for scripted steps. |
| 1.2 | `ssh -i ~/.ssh/browser_automation -o ConnectTimeout=10 azureuser@74.235.98.128 "echo ok"` | Quick connectivity check with a short timeout, so a dead VM/network fails fast instead of hanging. |

---

## 2. Pre-flight checks (run before every deploy)

| # | Command | Why |
|---|---|---|
| 2.1 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "readlink -f /opt/dealercenter-agent/current"` | Shows which release directory `current` currently points at — your rollback target if this deploy goes wrong. |
| 2.2 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "cd /opt/dealercenter-agent/current/backend && sudo -u dealercenter venv/bin/python -m alembic current"` | Shows the DB's actual applied migration. Must run as `dealercenter` (not `azureuser`) — `.env` is `600`-mode, owned by `dealercenter`, so a plain `venv/bin/python -m alembic current` as `azureuser` fails with `PermissionError` reading `.env`. |
| 2.3 | `cd backend && git log -1 --format=%H -- alembic/versions/ \| xargs -I{} echo {}` / just `ls backend/alembic/versions/` | Compare your **local** migration chain's head against what 2.2 reported. If they differ, `deploy.sh` will run pending migrations — know that before you start, don't be surprised by it mid-deploy. |
| 2.4 | `systemctl status dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick --no-pager \| grep -E 'Active\|●'` **[on VM]** | Baseline: confirm all 4 services are healthy *before* you touch anything, so you know any new problem after deploying was actually caused by the deploy. |

---

## 3. Full code release (Python/backend changes — needs a service restart)

Use this path whenever you change anything under `backend/dealercenter_agent/` (routes, jobs, tick logic, models, migrations, requirements.txt). Skip to §4 for a static-file-only fix.

| # | Command | Why |
|---|---|---|
| 3.1 | `RELEASE_TS=$(date +%Y-%m-%d-%H%M%S)` | One timestamp used for every subsequent command in this release — keeps all the paths below consistent and copy-pasteable. |
| 3.2 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo mkdir -p /opt/dealercenter-agent/releases/$RELEASE_TS && sudo chown azureuser:azureuser /opt/dealercenter-agent/releases/$RELEASE_TS"` | Creates the new release dir. Temporarily owned by `azureuser` (not `dealercenter`) — `azureuser` is the SSH/rsync identity and has no write access to a `dealercenter`-owned dir otherwise. |
| 3.3 | `rsync -az --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' --exclude 'storage_data' -e "ssh -i ~/.ssh/browser_automation" backend/ azureuser@74.235.98.128:/opt/dealercenter-agent/releases/$RELEASE_TS/backend/` | Copies your code to the VM. The excludes keep local venvs/caches/runtime invoice files (which don't belong in a release and can be large) out of the copy. |
| 3.4 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo chown -R dealercenter:dealercenter /opt/dealercenter-agent/releases/$RELEASE_TS"` | Hands the release back to the `dealercenter` service user before `deploy.sh` runs — every later step (venv build, migrations, the app itself) runs as `dealercenter`, not `azureuser`. |
| 3.5 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo -u dealercenter bash /tmp/deploy.sh /opt/dealercenter-agent/releases/$RELEASE_TS"` | Runs the actual deploy: symlinks `.env`/`.auth_state.json` in → builds a fresh venv → installs deps + Chromium → `alembic upgrade head` (while the **old** release is still live, so a bad migration fails here without taking the site down) → flips the `current` symlink → restarts all 4 services → health-checks → **auto-rolls back if the health check fails** → prunes old releases (keeps current + previous only). See `deploy/deploy.sh` in the repo for the exact step-by-step. |
| 3.6 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "md5sum /tmp/deploy.sh" && md5sum deploy/deploy.sh` | **Run this before 3.5, not after.** Confirms the copy of `deploy.sh` sitting in `/tmp` on the VM matches your repo's current `deploy/deploy.sh` — if you've edited the script locally, `scp deploy/deploy.sh azureuser@74.235.98.128:/tmp/deploy.sh` first, or 3.5 will run stale logic. |

---

## 4. Static-file-only fix (HTML/CSS/JS, no backend/Python change)

A lighter-weight path for e.g. a UI bug fix — skips the venv rebuild, migration run, and service restart entirely, since none of those are affected.

| # | Command | Why |
|---|---|---|
| 4.1 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo cp /opt/dealercenter-agent/current/backend/dealercenter_agent/web/static/<FILE> /opt/dealercenter-agent/current/backend/dealercenter_agent/web/static/<FILE>.bak-$(date +%s)"` | Backs up the live file before overwriting it — your instant rollback if the new one has a bug. |
| 4.2 | `scp -i ~/.ssh/browser_automation backend/dealercenter_agent/web/static/<FILE> azureuser@74.235.98.128:/tmp/<FILE>.new` | Copies the fixed file to the VM's `/tmp` (writable by `azureuser`) — can't `scp` directly into the `dealercenter`-owned static dir. |
| 4.3 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo cp /tmp/<FILE>.new /opt/dealercenter-agent/current/backend/dealercenter_agent/web/static/<FILE> && sudo chown dealercenter:dealercenter /opt/dealercenter-agent/current/backend/dealercenter_agent/web/static/<FILE>"` | Moves it into place and fixes ownership. No service restart needed — static files are served fresh off disk per request with `Cache-Control: no-cache` already set, so the change is live immediately. |
| 4.4 | `md5sum backend/dealercenter_agent/web/static/<FILE>` and `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "md5sum /opt/dealercenter-agent/current/backend/dealercenter_agent/web/static/<FILE>"` | Confirms the deployed file is byte-for-byte what you intended — don't trust "the commands didn't error," actually compare checksums. |

---

## 5. Post-deploy health checks (run after every deploy, either path)

| # | Command | Why |
|---|---|---|
| 5.1 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "systemctl status dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick --no-pager \| grep -E 'Active\|●'"` | All 4 should read `active (running)` with a *recent* start time matching your deploy, and ideally `0` restarts — a service that's been "active" since before your deploy didn't actually pick up the new code. |
| 5.2 | `curl -sf -o /dev/null -w "%{http_code}\n" https://aviaralabs-browser-automation.eastus.cloudapp.azure.com/theme.css` | The same endpoint `deploy.sh`'s own automatic health check hits. `200` = web process is up and serving. This is what triggers auto-rollback if it fails during `deploy.sh` itself — re-running it manually afterward is just an extra confirmation. |
| 5.3 | `curl -s https://aviaralabs-browser-automation.eastus.cloudapp.azure.com/<page>.html \| grep "<something that should/shouldn't be there>"` | Confirms the *specific* change you shipped is actually live (not just "a 200 came back") — e.g. a new dropdown option, removed dead code, a new button. Always check the actual content, not just the status code. |
| 5.4 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo journalctl -u dealercenter-web -n 50 --no-pager"` (swap the unit name for whichever service you care about) | Tails recent logs for one service — the first place to look if 5.1/5.2 show a problem. Add `-f` instead of `-n 50` to follow live. |
| 5.5 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "cd /opt/dealercenter-agent/current/backend && sudo -u dealercenter venv/bin/python -m alembic current"` | Re-confirms the DB migration actually landed where you expected, same command as 2.2 — compare before/after. |

---

## 6. Rollback

| # | Command | Why |
|---|---|---|
| 6.1 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "ls -1dt /opt/dealercenter-agent/releases/*/ \| head -3"` | Lists releases newest-first so you can see exactly what's available to roll back to (`deploy.sh` only keeps current + previous, so there's rarely more than 2). |
| 6.2 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo ln -sfn /opt/dealercenter-agent/releases/<PREVIOUS_TS> /opt/dealercenter-agent/current"` | Atomically flips `current` back to the previous release. Note: this does **not** reverse a DB migration — if the bad release included one, you also need `alembic downgrade` (handle that deliberately, don't script it blindly). |
| 6.3 | `ssh -i ~/.ssh/browser_automation azureuser@74.235.98.128 "sudo systemctl restart dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick"` | Services must be restarted to actually pick up the re-pointed `current` symlink — Python has already imported the old code into memory otherwise. |
| 6.4 | Re-run §5 (health checks) | Confirm the rollback actually fixed things before walking away. |

---

## Notes

- **`deploy.sh` already does steps 3.2–3.4 automatically if you give it SSH access to do so** — this sheet exists because the agent that originally ran these commands didn't have passwordless sudo configured for a one-shot "deploy everything" script, so each piece was run by hand. If you set up the scoped `sudoers.d` rule described in `docs/deployment/azure-vm-deployment-plan.md`, most of §3 collapses into a single `ssh ... "deploy.sh ..."` call.
- Always run `git status`/`git diff` locally and confirm what you're about to ship **before** step 3.3 — `rsync` will faithfully copy whatever's on disk, uncommitted changes included.
- Keep `/tmp/deploy.sh` on the VM in sync with `deploy/deploy.sh` in the repo (§3.6) — it's easy to edit the script locally and forget to push the copy the VM actually runs.
