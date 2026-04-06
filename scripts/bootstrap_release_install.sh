#!/usr/bin/env bash
set -Eeuo pipefail

REPO_SLUG="${REPO_SLUG:-chmajster/Algen-server-hosting-panel}"
ASSET_NAME="${ASSET_NAME:-hosting-panel-release.tar.gz}"
RELEASE_TAG="${RELEASE_TAG:-latest}"
TMP_DIR="$(mktemp -d)"
ARCHIVE_PATH="$TMP_DIR/$ASSET_NAME"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || {
    echo "Uruchom bootstrap installer jako root lub przez sudo." >&2
    exit 1
  }
}

build_download_url() {
  if [[ "$RELEASE_TAG" == "latest" ]]; then
    printf 'https://github.com/%s/releases/latest/download/%s' "$REPO_SLUG" "$ASSET_NAME"
  else
    printf 'https://github.com/%s/releases/download/%s/%s' "$REPO_SLUG" "$RELEASE_TAG" "$ASSET_NAME"
  fi
}

require_root
DOWNLOAD_URL="$(build_download_url)"

log "Pobieram paczkę release: $DOWNLOAD_URL"
curl -fL "$DOWNLOAD_URL" -o "$ARCHIVE_PATH"

log "Rozpakowuję paczkę"
tar -xzf "$ARCHIVE_PATH" -C "$TMP_DIR"

PACKAGE_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -n "$PACKAGE_DIR" ]] || {
  echo "Nie znaleziono katalogu paczki po rozpakowaniu." >&2
  exit 1
}

[[ -f "$PACKAGE_DIR/install.sh" ]] || {
  echo "Paczka release nie zawiera install.sh." >&2
  exit 1
}

chmod +x "$PACKAGE_DIR/install.sh"
log "Uruchamiam installer z paczki"
cd "$PACKAGE_DIR"
exec ./install.sh
