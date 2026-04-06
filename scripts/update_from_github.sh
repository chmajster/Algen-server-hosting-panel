#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/hosting-panel}"
APP_USER="${APP_USER:-hosting-panel}"
APP_GROUP="${APP_GROUP:-hosting-panel}"
SERVICE_NAME="${SERVICE_NAME:-hosting-panel.service}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
AUTOUPDATE_REPO_URL="${AUTOUPDATE_REPO_URL:-https://github.com/chmajster/Algen-server-hosting-panel}"
AUTOUPDATE_BRANCH="${AUTOUPDATE_BRANCH:-main}"
STATE_DIR="${STATE_DIR:-$APP_DIR/storage/autoupdate}"
WORK_DIR="${WORK_DIR:-/tmp/hosting-panel-update}"
LOG_FILE="${LOG_FILE:-/var/log/hosting-panel/autoupdate.log}"

log() {
  mkdir -p "$(dirname "$LOG_FILE")"
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" | tee -a "$LOG_FILE"
}

fail() {
  log "ERROR: $1"
  exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "Skrypt update musi być uruchamiany jako root."
[[ -f "$ENV_FILE" ]] || fail "Brak pliku środowiskowego: $ENV_FILE"

# shellcheck disable=SC1090
source "$ENV_FILE"

mkdir -p "$STATE_DIR" "$WORK_DIR"
LOCK_FILE="$STATE_DIR/update.lock"
LAST_COMMIT_FILE="$STATE_DIR/last_commit"

exec 9>"$LOCK_FILE"
flock -n 9 || {
  log "Pominięto aktualizację, poprzednia instancja nadal działa."
  exit 0
}

remote_commit="$(git ls-remote --heads "$AUTOUPDATE_REPO_URL" "$AUTOUPDATE_BRANCH" | awk '{print $1}' | head -n1)"
[[ -n "$remote_commit" ]] || fail "Nie udało się pobrać SHA dla ${AUTOUPDATE_REPO_URL}#${AUTOUPDATE_BRANCH}."

last_commit=""
if [[ -f "$LAST_COMMIT_FILE" ]]; then
  last_commit="$(cat "$LAST_COMMIT_FILE")"
fi

if [[ "$remote_commit" == "$last_commit" ]]; then
  log "Brak nowych commitów. Aktualny SHA: $remote_commit"
  exit 0
fi

tmp_checkout="$WORK_DIR/repo-$remote_commit"
rm -rf "$tmp_checkout"
log "Pobieram nową wersję z GitHub: $remote_commit"
git clone --depth 1 --branch "$AUTOUPDATE_BRANCH" "$AUTOUPDATE_REPO_URL" "$tmp_checkout" >> "$LOG_FILE" 2>&1

if [[ ! -f "$tmp_checkout/requirements.txt" ]] || [[ ! -d "$tmp_checkout/panel" ]]; then
  fail "Pobrane repo nie wygląda jak poprawny projekt aplikacji."
fi

log "Synchronizuję pliki aplikacji"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude 'storage' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  "$tmp_checkout"/ "$APP_DIR"/ >> "$LOG_FILE" 2>&1

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
mkdir -p "$APP_DIR/storage/uploads" "$APP_DIR/storage/backups" "$APP_DIR/storage/autoupdate"
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR/storage"

log "Instaluję zależności Python"
sudo -u "$APP_USER" bash -lc "'$APP_DIR/.venv/bin/pip' install -r '$APP_DIR/requirements.txt'" >> "$LOG_FILE" 2>&1

log "Uruchamiam migracje bazy"
sudo -u "$APP_USER" bash -lc "source '$ENV_FILE'; FLASK_APP=wsgi:app '$APP_DIR/.venv/bin/flask' db upgrade" >> "$LOG_FILE" 2>&1

echo "$remote_commit" > "$LAST_COMMIT_FILE"
chown "$APP_USER:$APP_GROUP" "$LAST_COMMIT_FILE"

log "Restartuję usługę aplikacji"
systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" || fail "Usługa ${SERVICE_NAME} nie wystartowała po aktualizacji."

rm -rf "$tmp_checkout"
log "Aktualizacja zakończona sukcesem. Nowy SHA: $remote_commit"
