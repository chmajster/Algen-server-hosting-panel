#!/usr/bin/env bash
set -Eeuo pipefail

APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
APP_DIR="${APP_DIR:-/opt/hosting-panel}"
PYTHON_BASE="${PYTHON_BASE:-/opt/python}"
DB_NAME="${DB_NAME:-hosting_panel}"
DB_USER="${DB_USER:-hosting_panel}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 18)}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
APP_DOMAIN="${APP_DOMAIN:-_}"
INSTALL_NGINX="${INSTALL_NGINX:-true}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/var/log/hosting-panel"
HOSTS_HELPER_TARGET="/usr/local/bin/hosting-panel-hosts-helper"
SUDOERS_TARGET="/etc/sudoers.d/hosting-panel-hosts-helper"
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
}

backup_file() {
  local target="$1"
  if [[ -f "$target" ]]; then
    cp -a "$target" "${target}.bak.$(date '+%Y%m%d%H%M%S')"
  fi
}

install_system_packages() {
  log "Instaluję pakiety systemowe"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential curl wget git rsync ca-certificates pkg-config openssl \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev \
    liblzma-dev libgdbm-dev uuid-dev libmariadb-dev mariadb-server mariadb-client \
    nginx sudo
}

resolve_latest_python() {
  curl -fsSL https://www.python.org/ftp/python/ \
    | grep -oE '3\.[0-9]+\.[0-9]+/' \
    | tr -d '/' \
    | sort -V \
    | tail -n1
}

install_python() {
  local latest_version
  latest_version="$(resolve_latest_python)"
  [[ -n "$latest_version" ]] || fail "Nie udało się ustalić najnowszej stabilnej wersji Python 3."
  PY_PREFIX="$PYTHON_BASE/$latest_version"
  PYTHON_BIN="$PY_PREFIX/bin/python3"

  if [[ ! -x "$PYTHON_BIN" ]]; then
    log "Buduję Python $latest_version z oficjalnego źródła"
    mkdir -p "$PYTHON_BASE" /usr/local/src
    local archive="Python-${latest_version}.tar.xz"
    local src_dir="/usr/local/src/Python-${latest_version}"
    cd /usr/local/src
    [[ -f "$archive" ]] || curl -fsSLO "https://www.python.org/ftp/python/${latest_version}/${archive}"
    rm -rf "$src_dir"
    tar -xf "$archive"
    cd "$src_dir"
    ./configure --prefix="$PY_PREFIX" --enable-optimizations --with-ensurepip=install
    make -j"$(nproc)"
    make install
  else
    log "Python $latest_version jest już zainstalowany"
  fi
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
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
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
  set_env_key "APP_NAME" "\"Hosting Panel\""
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
  set_env_key "DEFAULT_TIMEZONE" "Europe/Warsaw"
  set_env_key "SESSION_COOKIE_SECURE" "false"
  chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
}

run_migrations_and_seed() {
  log "Uruchamiam migracje i seed danych"
  cd "$APP_DIR"
  sudo -u "$APP_USER" bash -lc "source '$ENV_FILE'; FLASK_APP=wsgi:app '$APP_DIR/.venv/bin/flask' db upgrade"
  sudo -u "$APP_USER" bash -lc "source '$ENV_FILE'; FLASK_APP=wsgi:app '$APP_DIR/.venv/bin/flask' seed-data --admin-username '$ADMIN_USERNAME' --admin-password '$ADMIN_PASSWORD'"
  sudo -u "$APP_USER" bash -lc "source '$ENV_FILE'; FLASK_APP=wsgi:app '$APP_DIR/.venv/bin/flask' create-admin --username '$ADMIN_USERNAME' --password '$ADMIN_PASSWORD' --email '$ADMIN_EMAIL' || true"
}

install_hosts_helper() {
  log "Instaluję helper do zarządzania /etc/hosts"
  mkdir -p /var/backups/hosting-panel/hosts
  install -o root -g root -m 750 "$APP_DIR/scripts/hosts_helper.py" "$HOSTS_HELPER_TARGET"
  backup_file "$SUDOERS_TARGET"
  cp "$APP_DIR/deploy/hosting-panel-hosts-helper.sudoers" "$SUDOERS_TARGET"
  chmod 440 "$SUDOERS_TARGET"
  visudo -cf "$SUDOERS_TARGET"
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
  local app_url="http://$(hostname -I | awk '{print $1}')"
  log "Instalacja zakończona"
  cat <<EOF
Adres aplikacji: ${app_url}
Panel lokalny Gunicorn: http://127.0.0.1:8000
Administrator: ${ADMIN_USERNAME}
Hasło administratora: ${ADMIN_PASSWORD}
Plik środowiskowy: ${ENV_FILE}
Logi aplikacji: ${LOG_DIR}
Usługi systemd: hosting-panel.service, mariadb.service$( [[ "$INSTALL_NGINX" == "true" ]] && printf ', nginx.service' )
Helper hosts: ${HOSTS_HELPER_TARGET}
Backupy hosts: /var/backups/hosting-panel/hosts
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
  configure_nginx
  print_summary
}

main "$@"
