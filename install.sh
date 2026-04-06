#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
APP_DIR="${APP_DIR:-/opt/hosting-panel}"
PYTHON_PACKAGE="${PYTHON_PACKAGE:-}"
PYTHON_VENV_PACKAGE="${PYTHON_VENV_PACKAGE:-}"
PYTHON_DEV_PACKAGE="${PYTHON_DEV_PACKAGE:-}"
PYTHON_BIN="${PYTHON_BIN:-}"
DB_NAME="${DB_NAME:-hosting_panel}"
DB_USER="${DB_USER:-hosting_panel}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 18)}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
APP_DOMAIN="${APP_DOMAIN:-_}"
INSTALL_NGINX="${INSTALL_NGINX:-true}"
AUTOUPDATE_ENABLED="${AUTOUPDATE_ENABLED:-true}"
AUTOUPDATE_REPO_URL="${AUTOUPDATE_REPO_URL:-https://github.com/chmajster/Algen-server-hosting-panel}"
AUTOUPDATE_BRANCH="${AUTOUPDATE_BRANCH:-main}"
AUTOUPDATE_INTERVAL="${AUTOUPDATE_INTERVAL:-*:0/15}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/RELEASE" ]]; then
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/RELEASE"
fi
LOG_DIR="/var/log/hosting-panel"
HOSTS_HELPER_TARGET="/usr/local/bin/hosting-panel-hosts-helper"
SSL_HELPER_TARGET="/usr/local/bin/hosting-panel-ssl-helper"
SUDOERS_TARGET="/etc/sudoers.d/hosting-panel-hosts-helper"
SSL_SUDOERS_TARGET="/etc/sudoers.d/hosting-panel-ssl-helper"
ENV_FILE="$APP_DIR/.env"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

fail() {
  echo "Błąd: $1" >&2
  exit 1
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || fail "Uruchom installer jako root lub przez sudo."
}

detect_os() {
  [[ -f /etc/os-release ]] || fail "Nie znaleziono /etc/os-release."
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}" in
    debian|ubuntu) ;;
    *) fail "Obsługiwane są Debian, Ubuntu lub zgodne dystrybucje." ;;
  esac
  [[ "$INSTALL_NGINX" == "true" ]] || fail "Panel ma działać na porcie 80, więc nginx musi pozostać włączony."
}

backup_file() {
  local target="$1"
  if [[ -f "$target" ]]; then
    cp -a "$target" "${target}.bak.$(date '+%Y%m%d%H%M%S')"
  fi
}

format_env_value() {
  local value="$1"
  value="${value//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

install_system_packages() {
  log "Instaluję pakiety systemowe"
  apt-get update
  local packages=(
    build-essential curl wget git rsync ca-certificates pkg-config openssl \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
    liblzma-dev libgdbm-dev uuid-dev libmariadb-dev mariadb-server mariadb-client \
    sudo
  )
  if [[ "$INSTALL_NGINX" == "true" ]]; then
    packages+=(nginx)
  fi
  packages+=(certbot)
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
}

install_python() {
  local candidate_pkg candidate_venv candidate_dev candidate_bin resolved_version

  if [[ -n "$PYTHON_PACKAGE" ]] && [[ -n "$PYTHON_VENV_PACKAGE" ]] && [[ -n "$PYTHON_DEV_PACKAGE" ]] && [[ -n "$PYTHON_BIN" ]]; then
    candidate_pkg="$PYTHON_PACKAGE"
    candidate_venv="$PYTHON_VENV_PACKAGE"
    candidate_dev="$PYTHON_DEV_PACKAGE"
    candidate_bin="$PYTHON_BIN"
  elif apt-cache show python3.14 >/dev/null 2>&1; then
    candidate_pkg="python3.14"
    candidate_venv="python3.14-venv"
    candidate_dev="python3.14-dev"
    candidate_bin="/usr/bin/python3.14"
  else
    candidate_pkg="python3"
    candidate_venv="python3-venv"
    candidate_dev="python3-dev"
    candidate_bin="/usr/bin/python3"
  fi

  log "Instaluję ${candidate_pkg} z oficjalnego repozytorium APT systemu Ubuntu"

  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "$candidate_pkg" \
    "$candidate_venv" \
    "$candidate_dev"

  [[ -x "$candidate_bin" ]] || fail "Po instalacji nie znaleziono interpretera ${candidate_bin}."

  PYTHON_PACKAGE="$candidate_pkg"
  PYTHON_VENV_PACKAGE="$candidate_venv"
  PYTHON_DEV_PACKAGE="$candidate_dev"
  PYTHON_BIN="$candidate_bin"
  resolved_version="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
  log "Używany interpreter: ${PYTHON_BIN} (${resolved_version})"
}

ensure_app_user() {
  if ! getent group "$APP_GROUP" >/dev/null; then
    groupadd --system "$APP_GROUP"
  fi
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --gid "$APP_GROUP" --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  fi
}

deploy_code() {
  log "Wdrażam kod aplikacji do $APP_DIR"
  mkdir -p "$APP_DIR"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    "$SCRIPT_DIR"/ "$APP_DIR"/
  mkdir -p "$APP_DIR/storage/uploads" "$APP_DIR/storage/backups" "$LOG_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR/storage" "$LOG_DIR"
}

setup_virtualenv() {
  log "Tworzę virtualenv i instaluję zależności Pythona"
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel setuptools
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
}

configure_database() {
  log "Konfiguruję MariaDB"
  systemctl enable mariadb
  systemctl restart mariadb
  mariadb <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'127.0.0.1' IDENTIFIED BY '${DB_PASSWORD}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL
}

set_env_key() {
  local key="$1"
  local value="$2"
  local formatted_value
  formatted_value="$(format_env_value "$value")"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${formatted_value}|" "$ENV_FILE"
  else
    echo "${key}=${formatted_value}" >> "$ENV_FILE"
  fi
}

generate_env() {
  log "Generuję plik .env"
  mkdir -p "$APP_DIR"
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
  else
    backup_file "$ENV_FILE"
  fi
  set_env_key "APP_ENV" "production"
  set_env_key "APP_NAME" "Hosting Panel"
  set_env_key "APP_HOST" "127.0.0.1"
  set_env_key "APP_PORT" "8000"
  set_env_key "PREFERRED_URL_SCHEME" "http"
  set_env_key "SECRET_KEY" "$(openssl rand -hex 32)"
  set_env_key "DATABASE_URL" "mysql+pymysql://${DB_USER}:${DB_PASSWORD}@127.0.0.1/${DB_NAME}"
  set_env_key "STORAGE_ROOT" "${APP_DIR}/storage/uploads"
  set_env_key "BACKUP_ROOT" "${APP_DIR}/storage/backups"
  set_env_key "HOSTS_HELPER_PATH" "$HOSTS_HELPER_TARGET"
  set_env_key "HOSTS_BACKUP_DIR" "/var/backups/hosting-panel/hosts"
  set_env_key "HOSTS_SUDO_BIN" "/usr/bin/sudo"
  set_env_key "HOSTS_ALLOWED_FILE" "/etc/hosts"
  set_env_key "SSL_HELPER_PATH" "$SSL_HELPER_TARGET"
  set_env_key "LETSENCRYPT_EMAIL" "$ADMIN_EMAIL"
  set_env_key "DEFAULT_TIMEZONE" "Europe/Warsaw"
  set_env_key "RATELIMIT_DEFAULT" "200/day;50/hour"
  set_env_key "RATELIMIT_STORAGE_URI" "memory://"
  set_env_key "SESSION_COOKIE_SECURE" "false"
  set_env_key "AUTOUPDATE_ENABLED" "$AUTOUPDATE_ENABLED"
  set_env_key "AUTOUPDATE_REPO_URL" "$AUTOUPDATE_REPO_URL"
  set_env_key "AUTOUPDATE_BRANCH" "$AUTOUPDATE_BRANCH"
  set_env_key "AUTOUPDATE_INTERVAL" "$AUTOUPDATE_INTERVAL"
  chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
}

run_migrations_and_seed() {
  log "Uruchamiam migracje i seed danych"
  sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && '$APP_DIR/.venv/bin/flask' --app wsgi:app db upgrade"
  sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && '$APP_DIR/.venv/bin/flask' --app wsgi:app seed-data --admin-username '$ADMIN_USERNAME' --admin-password '$ADMIN_PASSWORD' --admin-email '$ADMIN_EMAIL'"
}

install_hosts_helper() {
  log "Instaluję helper do zarządzania /etc/hosts"
  mkdir -p /var/backups/hosting-panel/hosts
  install -o root -g root -m 750 "$APP_DIR/scripts/hosts_helper.py" "$HOSTS_HELPER_TARGET"
  install -o root -g root -m 750 "$APP_DIR/scripts/ssl_helper.py" "$SSL_HELPER_TARGET"
  backup_file "$SUDOERS_TARGET"
  backup_file "$SSL_SUDOERS_TARGET"
  cp "$APP_DIR/deploy/hosting-panel-hosts-helper.sudoers" "$SUDOERS_TARGET"
  cp "$APP_DIR/deploy/hosting-panel-ssl-helper.sudoers" "$SSL_SUDOERS_TARGET"
  chmod 440 "$SUDOERS_TARGET"
  chmod 440 "$SSL_SUDOERS_TARGET"
  visudo -cf "$SUDOERS_TARGET"
  visudo -cf "$SSL_SUDOERS_TARGET"
}

configure_systemd() {
  log "Konfiguruję usługę systemd"
  mkdir -p "$LOG_DIR"
  install -o root -g root -m 750 "$APP_DIR/scripts/install_app_service.sh" /usr/local/bin/hosting-panel-install-service
  APP_USER="$APP_USER" \
  APP_GROUP="$APP_GROUP" \
  APP_DIR="$APP_DIR" \
  ENV_FILE="$ENV_FILE" \
  GUNICORN_BIN="$APP_DIR/.venv/bin/gunicorn" \
  /usr/local/bin/hosting-panel-install-service
}

configure_autoupdate() {
  if [[ "$AUTOUPDATE_ENABLED" != "true" ]]; then
    log "Auto-update wyłączony"
    systemctl disable --now hosting-panel-update.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/hosting-panel-update.service /etc/systemd/system/hosting-panel-update.timer
    systemctl daemon-reload
    return 0
  fi

  log "Konfiguruję auto-update z GitHub"
  install -o root -g root -m 750 "$APP_DIR/scripts/update_from_github.sh" /usr/local/bin/hosting-panel-update
  install -o root -g root -m 750 "$APP_DIR/scripts/install_autoupdate_service.sh" /usr/local/bin/hosting-panel-install-autoupdate
  APP_DIR="$APP_DIR" \
  APP_USER="$APP_USER" \
  APP_GROUP="$APP_GROUP" \
  TIMER_ONCALENDAR="$AUTOUPDATE_INTERVAL" \
  /usr/local/bin/hosting-panel-install-autoupdate
}

configure_nginx() {
  [[ "$INSTALL_NGINX" == "true" ]] || return 0
  log "Konfiguruję nginx"
  backup_file /etc/nginx/sites-available/hosting-panel
  sed "s/server_name _;/server_name ${APP_DOMAIN};/" "$APP_DIR/deploy/nginx-hosting-panel.conf" > /etc/nginx/sites-available/hosting-panel
  ln -sf /etc/nginx/sites-available/hosting-panel /etc/nginx/sites-enabled/hosting-panel
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable nginx
  systemctl restart nginx
}

print_summary() {
  local primary_ip
  primary_ip="$(hostname -I | awk '{print $1}')"
  if [[ -z "$primary_ip" ]]; then
    primary_ip="$(ip -4 route get 1.1.1.1 | awk '{print $7; exit}')"
  fi
  local app_url="http://${primary_ip}"
  log "Instalacja zakończona"
  cat <<EOF
Adres IP serwera: ${primary_ip}
Panel publiczny: ${app_url}
Panel publiczny port 80: http://${primary_ip}:80
Panel lokalny Gunicorn: http://127.0.0.1:8000
Administrator: ${ADMIN_USERNAME}
Hasło administratora: ${ADMIN_PASSWORD}
Plik środowiskowy: ${ENV_FILE}
Logi aplikacji: ${LOG_DIR}
Usługi systemd: hosting-panel.service, mariadb.service$( [[ "$INSTALL_NGINX" == "true" ]] && printf ', nginx.service' )
Helper hosts: ${HOSTS_HELPER_TARGET}
Backupy hosts: /var/backups/hosting-panel/hosts
Auto-update repo: ${AUTOUPDATE_REPO_URL}
Auto-update branch: ${AUTOUPDATE_BRANCH}
Auto-update: $( [[ "$AUTOUPDATE_ENABLED" == "true" ]] && printf 'hosting-panel-update.timer (%s)' "$AUTOUPDATE_INTERVAL" || printf 'wyłączony' )
Źródło pakietu: ${PACKAGE_NAME:-repo} ${VERSION:-local}
EOF
}

main() {
  require_root
  detect_os
  install_system_packages
  install_python
  ensure_app_user
  deploy_code
  setup_virtualenv
  configure_database
  generate_env
  install_hosts_helper
  run_migrations_and_seed
  configure_systemd
  configure_autoupdate
  configure_nginx
  print_summary
}

main "$@"
