#!/usr/bin/env bash
# Run ONCE on a fresh VM, before the first deploy.sh call. Creates the
# releases/shared/current directory structure and the dealercenter
# service user. Does NOT install apt packages (postgresql, redis,
# nginx, certbot) or create the DB — those are separate, already-done
# steps (see docs/deployment/azure-vm-deployment-plan.md).
#
# After this runs, you still need to manually place the real secrets:
#   scp .env.production you@vm:/tmp/.env
#   ssh you@vm "sudo mv /tmp/.env /opt/dealercenter-agent/shared/.env && \
#                sudo chown dealercenter:dealercenter /opt/dealercenter-agent/shared/.env && \
#                sudo chmod 600 /opt/dealercenter-agent/shared/.env"
#   scp .auth_state.json you@vm:/tmp/.auth_state.json
#   ssh you@vm "sudo mv /tmp/.auth_state.json /opt/dealercenter-agent/shared/.auth_state.json && \
#                sudo chown dealercenter:dealercenter /opt/dealercenter-agent/shared/.auth_state.json && \
#                sudo chmod 600 /opt/dealercenter-agent/shared/.auth_state.json"
set -euo pipefail

BASE=/opt/dealercenter-agent

if ! id dealercenter &>/dev/null; then
  echo "--> Creating dealercenter service user"
  sudo useradd --system --create-home --shell /bin/bash dealercenter
fi

echo "--> Creating directory structure"
sudo mkdir -p "$BASE/releases" "$BASE/shared/storage_data"
sudo chown -R dealercenter:dealercenter "$BASE"
sudo chmod 700 "$BASE/shared/storage_data"

echo "--> Done. $BASE now has:"
sudo -u dealercenter find "$BASE" -maxdepth 2

cat <<EOF

Next steps (manual — secrets never go through this script):
  1. scp your real .env to $BASE/shared/.env (chmod 600, owned by dealercenter)
  2. scp your existing .auth_state.json to $BASE/shared/.auth_state.json (same perms)
  3. rsync your code into $BASE/releases/<timestamp>/
  4. ONE-TIME ONLY, as root, after that first release's venv exists —
     installs the OS-level shared libs Chromium needs system-wide.
     deploy.sh deliberately does NOT do this itself (the dealercenter
     user has no sudo rights, by design):
       sudo $BASE/releases/<timestamp>/backend/venv/bin/python -m playwright install-deps chromium
  5. Run deploy.sh $BASE/releases/<timestamp> (as the dealercenter user)
EOF
