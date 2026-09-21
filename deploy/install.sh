#!/usr/bin/env bash
# One-shot installer for Ubuntu 22.04 / 24.04. Run as root:
#   sudo bash deploy/install.sh
# Installs system deps, creates the geoagent user, builds both apps, and
# registers the systemd units + nginx site. No Docker.
set -euo pipefail

APP_DIR=/opt/geoagent
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${DOMAIN:-geoagent.example.com}"

echo "==> system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential \
    gdal-bin libgdal-dev libgeos-dev libproj-dev nginx curl git

if ! command -v node >/dev/null || [[ "$(node -v | cut -c2-3)" -lt 20 ]]; then
  echo "==> node 20"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi

echo "==> service user + files"
id -u geoagent >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin geoagent
mkdir -p "$APP_DIR"
rsync -a --delete --exclude node_modules --exclude .next --exclude out --exclude .venv \
      --exclude '__pycache__' --exclude 'data/outputs/*' --exclude 'data/previews/*' \
      "$SRC_DIR/" "$APP_DIR/"

echo "==> backend"
cd "$APP_DIR/backend"
[[ -f .env ]] || cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip wheel
.venv/bin/pip install -q -r requirements.txt
mkdir -p data/outputs data/previews data/osm_cache

echo "==> dashboard (built into the backend, so there is only one service)"
cd "$APP_DIR/backend"
.venv/bin/python scripts/build_ui.py

chown -R geoagent:geoagent "$APP_DIR"

echo "==> systemd"
install -m 644 "$APP_DIR/deploy/geoagent-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now geoagent-api

echo "==> nginx"
sed "s/geoagent.example.com/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/geoagent
ln -sf /etc/nginx/sites-available/geoagent /etc/nginx/sites-enabled/geoagent
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

cat <<MSG

Done. Next steps:
  1. Edit $APP_DIR/backend/.env  (EE_PROJECT, LLM key, DATABASE_URL if using Postgres)
  2. Authenticate Earth Engine for the service user, either:
       - service account: set EE_SERVICE_ACCOUNT + EE_PRIVATE_KEY_FILE in .env, or
       - sudo -u geoagent -H $APP_DIR/backend/.venv/bin/earthengine authenticate
  3. systemctl restart geoagent-api
  4. certbot --nginx -d $DOMAIN

Logs:  journalctl -u geoagent-api -f
MSG
