#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-hosting-panel-update}"
APP_DIR="${APP_DIR:-/opt/hosting-panel}"
APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
TIMER_ONCALENDAR="${TIMER_ONCALENDAR:-*:0/15}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_PATH="/etc/systemd/system/${SERVICE_NAME}.timer"

[[ "${EUID}" -eq 0 ]] || {
  echo "Ten skrypt musi być uruchomiony jako root." >&2
  exit 1
}

backup_if_exists() {
  local path="$1"
  if [[ -f "$path" ]]; then
    cp -a "$path" "${path}.bak.$(date '+%Y%m%d%H%M%S')"
  fi
}

backup_if_exists "$UNIT_PATH"
backup_if_exists "$TIMER_PATH"

cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Hosting Panel auto-update from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=root
EnvironmentFile=${APP_DIR}/.env
Environment=ENV_FILE=${APP_DIR}/.env
Environment=APP_DIR=${APP_DIR}
Environment=APP_USER=${APP_USER}
Environment=APP_GROUP=${APP_GROUP}
ExecStart=/usr/local/bin/hosting-panel-update
EOF

cat > "$TIMER_PATH" <<EOF
[Unit]
Description=Run Hosting Panel auto-update periodically

[Timer]
OnCalendar=${TIMER_ONCALENDAR}
Persistent=true
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.timer"
systemctl restart "${SERVICE_NAME}.timer"
systemctl status "${SERVICE_NAME}.timer" --no-pager || true
