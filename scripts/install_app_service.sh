#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-hosting-panel}"
APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
APP_DIR="${APP_DIR:-/opt/hosting-panel}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
GUNICORN_BIN="${GUNICORN_BIN:-$APP_DIR/.venv/bin/gunicorn}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

[[ "${EUID}" -eq 0 ]] || {
  echo "Ten skrypt musi być uruchomiony jako root." >&2
  exit 1
}

mkdir -p /var/log/hosting-panel

if [[ -f "$UNIT_PATH" ]]; then
  cp -a "$UNIT_PATH" "${UNIT_PATH}.bak.$(date '+%Y%m%d%H%M%S')"
fi

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Hosting Panel Flask application
After=network-online.target mariadb.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${GUNICORN_BIN} -c ${APP_DIR}/deploy/gunicorn.conf.py wsgi:app
Restart=always
RestartSec=5
TimeoutStartSec=60
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=${APP_DIR}/storage /var/log/hosting-panel

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"
systemctl status "${SERVICE_NAME}.service" --no-pager || true
