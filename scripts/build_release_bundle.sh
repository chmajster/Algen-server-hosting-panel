#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist}"
PACKAGE_NAME="${PACKAGE_NAME:-hosting-panel-release}"
VERSION="${VERSION:-$(git -C "$ROOT_DIR" describe --tags --always 2>/dev/null || date '+%Y%m%d%H%M%S')}"
BUILD_DIR="$(mktemp -d)"
OUTPUT_FILE="$DIST_DIR/${PACKAGE_NAME}.tar.gz"

cleanup() {
  rm -rf "$BUILD_DIR"
}
trap cleanup EXIT

mkdir -p "$DIST_DIR"
mkdir -p "$BUILD_DIR/${PACKAGE_NAME}"

rsync -a \
  --exclude '.git' \
  --exclude '.github' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude 'dist' \
  --exclude 'storage' \
  "$ROOT_DIR"/ "$BUILD_DIR/${PACKAGE_NAME}/"

cat > "$BUILD_DIR/${PACKAGE_NAME}/RELEASE" <<EOF
PACKAGE_NAME=${PACKAGE_NAME}
VERSION=${VERSION}
BUILT_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
EOF

tar -C "$BUILD_DIR" -czf "$OUTPUT_FILE" "$PACKAGE_NAME"
echo "Utworzono paczkę: $OUTPUT_FILE"
