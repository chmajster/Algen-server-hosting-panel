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
SMOKE_TEST_SCHEDULE_ENABLED="${SMOKE_TEST_SCHEDULE_ENABLED:-true}"
SMOKE_TEST_INTERVAL="${SMOKE_TEST_INTERVAL:-*:0/15}"
SMOKE_TEST_LOG_FILE="${SMOKE_TEST_LOG_FILE:-/var/log/hosting-panel/smoke-test.log}"
SMOKE_TEST_API_TOKEN="${SMOKE_TEST_API_TOKEN:-$(openssl rand -hex 24)}"
SMOKE_TEST_API_ALLOWLIST="${SMOKE_TEST_API_ALLOWLIST:-127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
SMOKE_TEST_API_RATELIMIT="${SMOKE_TEST_API_RATELIMIT:-5 per minute}"
TWO_FACTOR_AVAILABLE="${TWO_FACTOR_AVAILABLE:-false}"
TWO_FACTOR_ISSUER="${TWO_FACTOR_ISSUER:-Hosting Panel}"
TWO_FACTOR_LOGIN_RATELIMIT="${TWO_FACTOR_LOGIN_RATELIMIT:-10 per 10 minutes}"
ONLINE_PAYMENTS_ENABLED="${ONLINE_PAYMENTS_ENABLED:-false}"
ONLINE_PAYMENTS_PROVIDER="${ONLINE_PAYMENTS_PROVIDER:-stripe}"
ONLINE_PAYMENTS_CURRENCY="${ONLINE_PAYMENTS_CURRENCY:-PLN}"
ONLINE_PAYMENTS_MIN_AMOUNT="${ONLINE_PAYMENTS_MIN_AMOUNT:-5.00}"
ONLINE_PAYMENTS_MAX_AMOUNT="${ONLINE_PAYMENTS_MAX_AMOUNT:-50000.00}"
ONLINE_PAYMENTS_SUCCESS_URL="${ONLINE_PAYMENTS_SUCCESS_URL:-}"
ONLINE_PAYMENTS_CANCEL_URL="${ONLINE_PAYMENTS_CANCEL_URL:-}"
STRIPE_SECRET_KEY="${STRIPE_SECRET_KEY:-}"
STRIPE_PUBLISHABLE_KEY="${STRIPE_PUBLISHABLE_KEY:-}"
STRIPE_WEBHOOK_SECRET="${STRIPE_WEBHOOK_SECRET:-}"
STRIPE_WEBHOOK_TOLERANCE_SECONDS="${STRIPE_WEBHOOK_TOLERANCE_SECONDS:-300}"
ADMIN_LOCAL_ONLY="${ADMIN_LOCAL_ONLY:-true}"
ADMIN_ALLOWED_NETWORKS="${ADMIN_ALLOWED_NETWORKS:-127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
ADMIN_ACCOUNT_PREEXISTED="false"
ADMIN_PASSWORD_EXPLICIT="false"
ADMIN_PASSWORD_UPDATED="false"
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

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_MAGENTA=$'\033[35m'
  C_CYAN=$'\033[36m'
  C_WHITE=$'\033[37m'
else
  C_RESET=""
  C_BOLD=""
  C_DIM=""
  C_RED=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_MAGENTA=""
  C_CYAN=""
  C_WHITE=""
fi

CURRENT_STEP=0
TOTAL_STEPS=16

print_banner() {
  printf '%s\n' "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
  printf '%s\n' "${C_CYAN}${C_BOLD}                Hosting Panel Installer                     ${C_RESET}"
  printf '%s\n' "${C_CYAN}${C_BOLD}============================================================${C_RESET}"
}

usage() {
  cat <<EOF
Uzycie:
  ./install.sh [-p HASLO_ADMINA]

Opcje:
  -p HASLO_ADMINA   Ustawia haslo administratora; jesli konto juz istnieje, haslo zostanie zaktualizowane
  -h                Pokazuje te pomoc
EOF
}

log() {
  printf '\n%s[%s]%s %s\n' "${C_DIM}" "$(date '+%Y-%m-%d %H:%M:%S')" "${C_RESET}" "$1"
}

step() {
  CURRENT_STEP=$((CURRENT_STEP + 1))
  printf '\n%s[%02d/%02d]%s %s%s%s\n' \
    "${C_BLUE}${C_BOLD}" \
    "$CURRENT_STEP" \
    "$TOTAL_STEPS" \
    "${C_RESET}" \
    "${C_BOLD}" \
    "$1" \
    "${C_RESET}"
}

ok() {
  printf '%s[OK]%s %s\n' "${C_GREEN}${C_BOLD}" "${C_RESET}" "$1"
}

warn() {
  printf '%s[WARN]%s %s\n' "${C_YELLOW}${C_BOLD}" "${C_RESET}" "$1"
}

info() {
  printf '%s[INFO]%s %s\n' "${C_CYAN}${C_BOLD}" "${C_RESET}" "$1"
}

fail() {
  printf '%s[ERROR]%s %s\n' "${C_RED}${C_BOLD}" "${C_RESET}" "$1" >&2
  exit 1
}

parse_args() {
  local opt
  OPTIND=1
  while getopts ":p:h" opt; do
    case "$opt" in
      p)
        [[ -n "$OPTARG" ]] || fail "Argument -p wymaga podania hasla."
        ADMIN_PASSWORD="$OPTARG"
        ADMIN_PASSWORD_EXPLICIT="true"
        ;;
      h)
        usage
        exit 0
        ;;
      :)
        fail "Argument -${OPTARG} wymaga wartosci."
        ;;
      \?)
        fail "Nieznany argument: -${OPTARG}. Uzyj -h, aby zobaczyc pomoc."
        ;;
    esac
  done
  shift $((OPTIND - 1))
  [[ $# -eq 0 ]] || fail "Nieznane dodatkowe argumenty: $*"
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
    *) fail "Obslugiwane sa Debian, Ubuntu lub zgodne dystrybucje." ;;
  esac
  [[ "$INSTALL_NGINX" == "true" ]] || fail "Panel ma dzialac na porcie 80, wiec nginx musi pozostac wlaczony."
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

sql_escape() {
  local value="$1"
  printf "%s" "${value//\'/\'\'}"
}

detect_php_fpm_unit() {
  systemctl list-unit-files 'php*-fpm.service' --no-legend 2>/dev/null | awk 'NR==1 {print $1}'
}

detect_php_fpm_socket() {
  local socket_path
  socket_path="$(find /run/php -maxdepth 1 -type s -name 'php*-fpm.sock' 2>/dev/null | sort | head -n 1 || true)"
  [[ -n "$socket_path" ]] || fail "Nie znaleziono socketu php-fpm (oczekiwano /run/php/php*-fpm.sock)."
  printf "%s" "$socket_path"
}

install_system_packages() {
  step "Instalacja pakietow systemowych"
  apt-get update
  local packages=(
    build-essential curl wget git rsync ca-certificates pkg-config openssl
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev
    libffi-dev libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev
    liblzma-dev libgdbm-dev uuid-dev libmariadb-dev mariadb-server mariadb-client
    sudo certbot docker.io php-fpm php-mysql php-mbstring php-xml php-zip php-curl php-gd phpmyadmin
  )
  if [[ "$INSTALL_NGINX" == "true" ]]; then
    packages+=(nginx)
  fi

  echo "phpmyadmin phpmyadmin/reconfigure-webserver multiselect none" | debconf-set-selections
  echo "phpmyadmin phpmyadmin/dbconfig-install boolean false" | debconf-set-selections

  DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"

  local php_fpm_unit
  php_fpm_unit="$(detect_php_fpm_unit)"
  if [[ -n "$php_fpm_unit" ]]; then
    systemctl enable "$php_fpm_unit"
    systemctl restart "$php_fpm_unit"
    info "Aktywowano usluge ${php_fpm_unit}"
  else
    warn "Nie wykryto uslugi php-fpm po instalacji pakietow."
  fi

  ok "Pakiety systemowe i phpMyAdmin zainstalowane"
}

install_python() {
  step "Instalacja Pythona"
  local candidate_pkg candidate_venv candidate_dev candidate_bin resolved_version

  if [[ -n "$PYTHON_PACKAGE" && -n "$PYTHON_VENV_PACKAGE" && -n "$PYTHON_DEV_PACKAGE" && -n "$PYTHON_BIN" ]]; then
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

  log "Instaluje ${candidate_pkg} z oficjalnego repozytorium APT systemu Ubuntu"
  DEBIAN_FRONTEND=noninteractive apt-get install -y "$candidate_pkg" "$candidate_venv" "$candidate_dev"
  [[ -x "$candidate_bin" ]] || fail "Po instalacji nie znaleziono interpretera ${candidate_bin}."

  PYTHON_PACKAGE="$candidate_pkg"
  PYTHON_VENV_PACKAGE="$candidate_venv"
  PYTHON_DEV_PACKAGE="$candidate_dev"
  PYTHON_BIN="$candidate_bin"
  resolved_version="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
  ok "Uzywany interpreter: ${PYTHON_BIN} (${resolved_version})"
}

ensure_app_user() {
  step "Tworzenie uzytkownika systemowego"
  if ! getent group "$APP_GROUP" >/dev/null; then
    groupadd --system "$APP_GROUP"
  fi
  if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --gid "$APP_GROUP" --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  fi
  ok "Uzytkownik ${APP_USER}:${APP_GROUP} gotowy"
}

configure_docker_runtime() {
  step "Konfiguracja Docker Runtime"
  systemctl enable docker
  systemctl restart docker
  if getent group docker >/dev/null; then
    usermod -aG docker "$APP_USER"
  fi
  ok "Docker aktywny, a ${APP_USER} ma dostep do grupy docker"
}

deploy_code() {
  step "Wdrozenie kodu aplikacji"
  mkdir -p "$APP_DIR"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    "$SCRIPT_DIR"/ "$APP_DIR"/
  mkdir -p "$APP_DIR/storage/uploads" "$APP_DIR/storage/clients" "$APP_DIR/storage/backups" "$LOG_DIR"
  chown -R "$APP_USER:$APP_GROUP" "$APP_DIR/storage" "$LOG_DIR"
  ok "Kod wdrozony do ${APP_DIR}"
}

setup_virtualenv() {
  step "Konfiguracja virtualenv"
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel setuptools
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
  ok "Virtualenv i zaleznosci Python gotowe"
}

configure_database() {
  step "Konfiguracja bazy danych"
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
  ok "MariaDB skonfigurowana"
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
  step "Generowanie konfiguracji .env"
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
  set_env_key "CLIENT_HOME_ROOT" "${APP_DIR}/storage/clients"
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
  set_env_key "LOGIN_RATELIMIT" "10 per 10 minutes"
  set_env_key "TWO_FACTOR_AVAILABLE" "$TWO_FACTOR_AVAILABLE"
  set_env_key "TWO_FACTOR_ISSUER" "$TWO_FACTOR_ISSUER"
  set_env_key "TWO_FACTOR_LOGIN_RATELIMIT" "$TWO_FACTOR_LOGIN_RATELIMIT"
  set_env_key "PHPMYADMIN_URL" "/phpmyadmin/"
  set_env_key "ONLINE_PAYMENTS_ENABLED" "$ONLINE_PAYMENTS_ENABLED"
  set_env_key "ONLINE_PAYMENTS_PROVIDER" "$ONLINE_PAYMENTS_PROVIDER"
  set_env_key "ONLINE_PAYMENTS_CURRENCY" "$ONLINE_PAYMENTS_CURRENCY"
  set_env_key "ONLINE_PAYMENTS_MIN_AMOUNT" "$ONLINE_PAYMENTS_MIN_AMOUNT"
  set_env_key "ONLINE_PAYMENTS_MAX_AMOUNT" "$ONLINE_PAYMENTS_MAX_AMOUNT"
  set_env_key "ONLINE_PAYMENTS_SUCCESS_URL" "$ONLINE_PAYMENTS_SUCCESS_URL"
  set_env_key "ONLINE_PAYMENTS_CANCEL_URL" "$ONLINE_PAYMENTS_CANCEL_URL"
  set_env_key "STRIPE_SECRET_KEY" "$STRIPE_SECRET_KEY"
  set_env_key "STRIPE_PUBLISHABLE_KEY" "$STRIPE_PUBLISHABLE_KEY"
  set_env_key "STRIPE_WEBHOOK_SECRET" "$STRIPE_WEBHOOK_SECRET"
  set_env_key "STRIPE_WEBHOOK_TOLERANCE_SECONDS" "$STRIPE_WEBHOOK_TOLERANCE_SECONDS"
  set_env_key "CLIENT_APACHE_ENABLED" "true"
  set_env_key "CLIENT_APACHE_IMAGE" "httpd:2.4"
  set_env_key "CLIENT_APACHE_BIND_ADDRESS" "127.0.0.1"
  set_env_key "CLIENT_APACHE_HTTP_PORT_BASE" "18000"
  set_env_key "CLIENT_APACHE_CONTAINER_PREFIX" "hosting-panel-client-apache"
  set_env_key "CLIENT_APACHE_REMOVE_EMPTY" "true"
  set_env_key "SMOKE_TEST_LOG_FILE" "$SMOKE_TEST_LOG_FILE"
  set_env_key "SMOKE_TEST_API_TOKEN" "$SMOKE_TEST_API_TOKEN"
  set_env_key "SMOKE_TEST_API_ALLOWLIST" "$SMOKE_TEST_API_ALLOWLIST"
  set_env_key "SMOKE_TEST_API_RATELIMIT" "$SMOKE_TEST_API_RATELIMIT"
  set_env_key "SMOKE_TEST_SCHEDULE_ENABLED" "$SMOKE_TEST_SCHEDULE_ENABLED"
  set_env_key "SMOKE_TEST_INTERVAL" "$SMOKE_TEST_INTERVAL"
  set_env_key "ADMIN_LOCAL_ONLY" "$ADMIN_LOCAL_ONLY"
  set_env_key "ADMIN_ALLOWED_NETWORKS" "$ADMIN_ALLOWED_NETWORKS"
  set_env_key "SESSION_COOKIE_SECURE" "false"
  set_env_key "AUTOUPDATE_ENABLED" "$AUTOUPDATE_ENABLED"
  set_env_key "AUTOUPDATE_REPO_URL" "$AUTOUPDATE_REPO_URL"
  set_env_key "AUTOUPDATE_BRANCH" "$AUTOUPDATE_BRANCH"
  set_env_key "AUTOUPDATE_INTERVAL" "$AUTOUPDATE_INTERVAL"
  chown "$APP_USER:$APP_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  ok "Plik .env gotowy: ${ENV_FILE}"
}

run_migrations_and_seed() {
  step "Migracje i dane startowe"
  sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && '$APP_DIR/.venv/bin/flask' --app wsgi:app db upgrade"
  local escaped_admin_username
  local existing_admin_count
  escaped_admin_username="$(sql_escape "$ADMIN_USERNAME")"
  existing_admin_count="$(mariadb --batch --skip-column-names "$DB_NAME" -e "SELECT COUNT(*) FROM users WHERE username='${escaped_admin_username}';" 2>/dev/null || printf '0')"
  if [[ "$existing_admin_count" -gt 0 ]]; then
    ADMIN_ACCOUNT_PREEXISTED="true"
    if [[ "$ADMIN_PASSWORD_EXPLICIT" == "true" ]]; then
      warn "Konto administratora ${ADMIN_USERNAME} juz istnieje. Haslo zostanie zaktualizowane zgodnie z argumentem -p."
    else
      warn "Konto administratora ${ADMIN_USERNAME} juz istnieje. Installer zachowa obecne haslo."
    fi
  fi
  sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && '$APP_DIR/.venv/bin/flask' --app wsgi:app seed-data --admin-username '$ADMIN_USERNAME' --admin-password '$ADMIN_PASSWORD' --admin-email '$ADMIN_EMAIL'"
  if [[ "$ADMIN_ACCOUNT_PREEXISTED" == "true" && "$ADMIN_PASSWORD_EXPLICIT" == "true" ]]; then
    sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && '$APP_DIR/.venv/bin/flask' --app wsgi:app create-admin --username '$ADMIN_USERNAME' --password '$ADMIN_PASSWORD' --email '$ADMIN_EMAIL'"
    ADMIN_PASSWORD_UPDATED="true"
    ok "Haslo istniejacego administratora zostalo zaktualizowane przez argument -p"
  fi
  ok "Migracje i seed zakonczone"
}

install_hosts_helper() {
  step "Instalacja helperow uprzywilejowanych"
  mkdir -p /var/backups/hosting-panel/hosts
  install -o root -g root -m 750 "$APP_DIR/scripts/hosts_helper.py" "$HOSTS_HELPER_TARGET"
  install -o root -g root -m 750 "$APP_DIR/scripts/ssl_helper.py" "$SSL_HELPER_TARGET"
  backup_file "$SUDOERS_TARGET"
  backup_file "$SSL_SUDOERS_TARGET"
  cp "$APP_DIR/deploy/hosting-panel-hosts-helper.sudoers" "$SUDOERS_TARGET"
  cp "$APP_DIR/deploy/hosting-panel-ssl-helper.sudoers" "$SSL_SUDOERS_TARGET"
  chmod 440 "$SUDOERS_TARGET" "$SSL_SUDOERS_TARGET"
  visudo -cf "$SUDOERS_TARGET"
  visudo -cf "$SSL_SUDOERS_TARGET"
  ok "Helpery hosts i SSL zainstalowane"
}

configure_systemd() {
  step "Konfiguracja uslugi aplikacji"
  mkdir -p "$LOG_DIR"
  install -o root -g root -m 750 "$APP_DIR/scripts/install_app_service.sh" /usr/local/bin/hosting-panel-install-service
  APP_USER="$APP_USER" \
  APP_GROUP="$APP_GROUP" \
  APP_DIR="$APP_DIR" \
  ENV_FILE="$ENV_FILE" \
  GUNICORN_BIN="$APP_DIR/.venv/bin/gunicorn" \
  /usr/local/bin/hosting-panel-install-service
  ok "Usluga hosting-panel.service aktywna"
}

configure_autoupdate() {
  step "Konfiguracja auto-update"
  if [[ "$AUTOUPDATE_ENABLED" != "true" ]]; then
    warn "Auto-update wylaczony"
    systemctl disable --now hosting-panel-update.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/hosting-panel-update.service /etc/systemd/system/hosting-panel-update.timer
    systemctl daemon-reload
    return 0
  fi

  log "Konfiguruje auto-update z GitHub"
  install -o root -g root -m 750 "$APP_DIR/scripts/update_from_github.sh" /usr/local/bin/hosting-panel-update
  install -o root -g root -m 750 "$APP_DIR/scripts/install_autoupdate_service.sh" /usr/local/bin/hosting-panel-install-autoupdate
  APP_DIR="$APP_DIR" \
  APP_USER="$APP_USER" \
  APP_GROUP="$APP_GROUP" \
  TIMER_ONCALENDAR="$AUTOUPDATE_INTERVAL" \
  /usr/local/bin/hosting-panel-install-autoupdate
  ok "Timer auto-update aktywny"
}

configure_smoketest_schedule() {
  step "Konfiguracja harmonogramu smoketestu"
  if [[ "$SMOKE_TEST_SCHEDULE_ENABLED" != "true" ]]; then
    warn "Harmonogram smoketestu wylaczony"
    systemctl disable --now hosting-panel-smoke-test.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/hosting-panel-smoke-test.service /etc/systemd/system/hosting-panel-smoke-test.timer
    systemctl daemon-reload
    return 0
  fi

  log "Konfiguruje timer smoketestu"
  install -o root -g root -m 750 "$APP_DIR/scripts/install_smoketest_service.sh" /usr/local/bin/hosting-panel-install-smoketest
  APP_DIR="$APP_DIR" \
  APP_USER="$APP_USER" \
  APP_GROUP="$APP_GROUP" \
  TIMER_ONCALENDAR="$SMOKE_TEST_INTERVAL" \
  /usr/local/bin/hosting-panel-install-smoketest
  ok "Timer smoketestu aktywny"
}

configure_nginx() {
  step "Konfiguracja nginx na porcie 80"
  [[ "$INSTALL_NGINX" == "true" ]] || return 0
  local php_fpm_socket
  php_fpm_socket="$(detect_php_fpm_socket)"
  log "Konfiguruje nginx"
  backup_file /etc/nginx/sites-available/hosting-panel
  sed \
    -e "s/server_name _;/server_name ${APP_DOMAIN};/" \
    -e "s#__PHP_FPM_SOCK__#${php_fpm_socket}#" \
    "$APP_DIR/deploy/nginx-hosting-panel.conf" > /etc/nginx/sites-available/hosting-panel
  ln -sf /etc/nginx/sites-available/hosting-panel /etc/nginx/sites-enabled/hosting-panel
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl enable nginx
  systemctl restart nginx
  ok "nginx dziala i publikuje panel na porcie 80"
}

report_unit_status() {
  local label="$1"
  local unit_name="$2"
  local active_state enabled_state

  active_state="$(systemctl is-active "$unit_name" 2>/dev/null || true)"
  enabled_state="$(systemctl is-enabled "$unit_name" 2>/dev/null || true)"

  if [[ "$active_state" == "active" ]]; then
    ok "${label}: dziala (active), autostart: ${enabled_state:-unknown}"
  else
    warn "${label}: nie dziala (${active_state:-unknown}), autostart: ${enabled_state:-unknown}"
  fi
}

report_http_status() {
  local label="$1"
  local url="$2"
  local http_code

  http_code="$(curl -k -L -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" 2>/dev/null || printf '000')"
  if [[ "$http_code" =~ ^(200|301|302|303|307|308)$ ]]; then
    ok "${label}: odpowiada (HTTP ${http_code})"
  else
    warn "${label}: brak poprawnej odpowiedzi (HTTP ${http_code})"
  fi
}

verify_services() {
  step "Test uslug po instalacji"
  info "Ponizszy raport ma charakter informacyjny i nie przerywa instalacji."
  local php_fpm_unit
  php_fpm_unit="$(detect_php_fpm_unit)"
  report_unit_status "Hosting Panel" "hosting-panel.service"
  report_unit_status "MariaDB" "mariadb.service"
  report_unit_status "Docker" "docker.service"
  if [[ "$INSTALL_NGINX" == "true" ]]; then
    report_unit_status "nginx" "nginx.service"
  fi
  if [[ -n "$php_fpm_unit" ]]; then
    report_unit_status "PHP-FPM" "$php_fpm_unit"
  fi
  if [[ "$AUTOUPDATE_ENABLED" == "true" ]]; then
    report_unit_status "Auto-update timer" "hosting-panel-update.timer"
  fi
  if [[ "$SMOKE_TEST_SCHEDULE_ENABLED" == "true" ]]; then
    report_unit_status "Smoke-test timer" "hosting-panel-smoke-test.timer"
  fi
  report_http_status "Panel przez Gunicorn" "http://127.0.0.1:8000/"
  if [[ "$INSTALL_NGINX" == "true" ]]; then
    report_http_status "Panel przez nginx" "http://127.0.0.1/"
    report_http_status "phpMyAdmin przez nginx" "http://127.0.0.1/phpmyadmin/"
  fi
}

print_summary() {
  step "Podsumowanie instalacji"
  local primary_ip
  local php_fpm_unit
  primary_ip="$(hostname -I | awk '{print $1}')"
  if [[ -z "$primary_ip" ]]; then
    primary_ip="$(ip -4 route get 1.1.1.1 | awk '{print $7; exit}')"
  fi
  php_fpm_unit="$(detect_php_fpm_unit)"
  local app_url="http://${primary_ip}"
  local admin_password_summary="${ADMIN_PASSWORD}"
  if [[ "$ADMIN_ACCOUNT_PREEXISTED" == "true" && "$ADMIN_PASSWORD_UPDATED" != "true" ]]; then
    admin_password_summary="zachowano istniejace haslo"
  fi
  printf '\n%sInstalacja zakonczona sukcesem%s\n' "${C_GREEN}${C_BOLD}" "${C_RESET}"
  cat <<EOF
${C_CYAN}${C_BOLD}Adres IP serwera:${C_RESET} ${C_WHITE}${primary_ip}${C_RESET}
${C_CYAN}${C_BOLD}Panel publiczny:${C_RESET} ${C_GREEN}${app_url}${C_RESET}
${C_CYAN}${C_BOLD}Panel publiczny port 80:${C_RESET} ${C_GREEN}http://${primary_ip}:80${C_RESET}
${C_CYAN}${C_BOLD}Panel lokalny Gunicorn:${C_RESET} http://127.0.0.1:8000
${C_CYAN}${C_BOLD}phpMyAdmin:${C_RESET} ${C_GREEN}http://${primary_ip}/phpmyadmin/${C_RESET}
${C_MAGENTA}${C_BOLD}Administrator:${C_RESET} ${ADMIN_USERNAME}
${C_MAGENTA}${C_BOLD}Haslo administratora:${C_RESET} ${admin_password_summary}
${C_BLUE}${C_BOLD}Plik srodowiskowy:${C_RESET} ${ENV_FILE}
${C_BLUE}${C_BOLD}Logi aplikacji:${C_RESET} ${LOG_DIR}
${C_BLUE}${C_BOLD}Uslugi systemd:${C_RESET} hosting-panel.service, mariadb.service, docker.service$( [[ "$INSTALL_NGINX" == "true" ]] && printf ', nginx.service' )$( [[ -n "$php_fpm_unit" ]] && printf ', %s' "$php_fpm_unit" )
${C_BLUE}${C_BOLD}Helper hosts:${C_RESET} ${HOSTS_HELPER_TARGET}
${C_BLUE}${C_BOLD}Helper SSL:${C_RESET} ${SSL_HELPER_TARGET}
${C_BLUE}${C_BOLD}Backupy hosts:${C_RESET} /var/backups/hosting-panel/hosts
${C_YELLOW}${C_BOLD}Auto-update repo:${C_RESET} ${AUTOUPDATE_REPO_URL}
${C_YELLOW}${C_BOLD}Auto-update branch:${C_RESET} ${AUTOUPDATE_BRANCH}
${C_YELLOW}${C_BOLD}Auto-update:${C_RESET} $( [[ "$AUTOUPDATE_ENABLED" == "true" ]] && printf 'hosting-panel-update.timer (%s)' "$AUTOUPDATE_INTERVAL" || printf 'wylaczony' )
${C_YELLOW}${C_BOLD}Smoke-test timer:${C_RESET} $( [[ "$SMOKE_TEST_SCHEDULE_ENABLED" == "true" ]] && printf 'hosting-panel-smoke-test.timer (%s)' "$SMOKE_TEST_INTERVAL" || printf 'wylaczony' )
${C_YELLOW}${C_BOLD}Smoke-test log:${C_RESET} ${SMOKE_TEST_LOG_FILE}
${C_DIM}Zrodlo pakietu: ${PACKAGE_NAME:-repo} ${VERSION:-local}${C_RESET}
EOF
}

main() {
  print_banner
  parse_args "$@"
  require_root
  detect_os
  install_system_packages
  install_python
  ensure_app_user
  configure_docker_runtime
  deploy_code
  setup_virtualenv
  configure_database
  generate_env
  install_hosts_helper
  run_migrations_and_seed
  configure_systemd
  configure_autoupdate
  configure_smoketest_schedule
  configure_nginx
  verify_services
  print_summary
}

main "$@"
