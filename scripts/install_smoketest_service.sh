#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-hosting-panel-smoke-test}"
APP_DIR="${APP_DIR:-/opt/hosting-panel}"
APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
TIMER_ONCALENDAR="${TIMER_ONCALENDAR:-*:0/15}"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_PATH="/etc/systemd/system/${SERVICE_NAME}.timer"

[[ "${EUID}" -eq 0 ]] || {
  echo "Ten skrypt musi byc uruchomiony jako root." >&2
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
Description=Hosting Panel scheduled smoke test
After=network-online.target hosting-panel.service
Wants=network-online.target

[Service]
Type=oneshot
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/flask --app wsgi:app smoke-test --source systemd_timer
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=
ReadWritePaths=${APP_DIR}/storage /var/log/hosting-panel
EOF

cat > "$TIMER_PATH" <<EOF
[Unit]
Description=Run Hosting Panel smoke test periodically

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
