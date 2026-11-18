#!/usr/bin/env bash
# Run ON THE VM, as a user with sudo rights, after a new release's code
# has already been rsync'd into /opt/dealercenter-agent/releases/<ts>/.
#
# Usage:
#   ./deploy.sh /opt/dealercenter-agent/releases/2026-11-19-093000
#
# What it does, in order (see docs/deployment/azure-vm-deployment-plan.md
# for the full reasoning behind this ordering):
#   1. Symlink the shared secrets/session file into the new release.
#   2. Build its own venv + install deps + Playwright's Chromium
#      BROWSER BINARY (not the OS-level deps — those are a one-time,
#      system-wide `playwright install-deps` run as root during
#      bootstrap, not per-release; see bootstrap.sh/the deployment
#      plan. Confirmed required (2026-11-18): `install --with-deps`
#      needs root for the apt step, but the dealercenter service user
#      deliberately has no sudo rights — running it unprivileged fails
#      outright, and running it via sudo puts the browser under
#      root's own cache dir, not dealercenter's, where the app
#      actually looks for it at runtime).
#   3. Run `alembic upgrade head` against the real DB — BEFORE this
#      release is live, so a bad migration never takes the site down,
#      it just fails the deploy with the old release still running.
#   4. Atomically flip the `current` symlink.
#   5. Restart all four services.
#   6. Health-check the new release; roll back automatically if it
#      fails to come up.
#   7. Prune old releases, keeping only current + the one before it.
set -euo pipefail

BASE=/opt/dealercenter-agent
SHARED="$BASE/shared"
SERVICES="dealercenter-web dealercenter-ingestion-worker dealercenter-workflow-worker dealercenter-scheduler-tick"

if [ $# -ne 1 ]; then
  echo "usage: $0 <release-dir>" >&2
  exit 1
fi
RELEASE_DIR="$1"
if [ ! -d "$RELEASE_DIR" ]; then
  echo "release dir not found: $RELEASE_DIR" >&2
  exit 1
fi

echo "==> Deploying $RELEASE_DIR"

echo "--> [1/7] Symlinking shared files"
ln -sfn "$SHARED/.env" "$RELEASE_DIR/.env"
ln -sfn "$SHARED/.auth_state.json" "$RELEASE_DIR/.auth_state.json"

echo "--> [2/7] Building venv and installing dependencies"
cd "$RELEASE_DIR/backend"
python3 -m venv venv
venv/bin/pip install --upgrade pip --quiet
venv/bin/pip install -r requirements.txt --quiet
venv/bin/python -m playwright install chromium

echo "--> [3/7] Running migrations (old release still live — a failure here stops here, nothing goes live)"
venv/bin/python -m alembic upgrade head

echo "--> [4/7] Flipping current -> $RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$BASE/current"

echo "--> [5/7] Restarting services"
sudo systemctl restart $SERVICES

echo "--> [6/7] Health check"
sleep 3
if curl -sf -o /dev/null http://127.0.0.1:8000/theme.css; then
  echo "    health check passed"
else
  echo "    HEALTH CHECK FAILED — rolling back"
  PREVIOUS=$(ls -1dt "$BASE"/releases/*/ | sed -n '2p')
  if [ -z "$PREVIOUS" ]; then
    echo "    no previous release to roll back to — manual intervention needed" >&2
    exit 1
  fi
  ln -sfn "${PREVIOUS%/}" "$BASE/current"
  sudo systemctl restart $SERVICES
  echo "    rolled back to ${PREVIOUS%/}"
  exit 1
fi

echo "--> [7/7] Pruning old releases (keeping current + previous only)"
ls -1dt "$BASE"/releases/*/ | tail -n +3 | xargs -r rm -rf

echo "==> Deploy complete. Live release: $(readlink -f "$BASE/current")"
