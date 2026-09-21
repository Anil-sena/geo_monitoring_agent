#!/usr/bin/env bash
# Redeploy after pulling new code. sudo bash deploy/update.sh
set -euo pipefail
APP_DIR=/opt/geoagent
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --exclude node_modules --exclude .next --exclude .venv --exclude '__pycache__' \
      --exclude .env --exclude 'data/' "$SRC_DIR/" "$APP_DIR/"
cd "$APP_DIR/backend"
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python scripts/build_ui.py
chown -R geoagent:geoagent "$APP_DIR"
systemctl restart geoagent-api
echo "restarted"
