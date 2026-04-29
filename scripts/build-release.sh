#!/usr/bin/env bash

set -euo pipefail

export LC_ALL=C
export LANG=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
DIST_DIR="$ROOT_DIR/dist"
BUNDLE_DIR="$DIST_DIR/asm-cli-$VERSION"
ARCHIVE_PATH="$DIST_DIR/asm-cli-$VERSION.tar.gz"
CHECKSUM_PATH="$ARCHIVE_PATH.sha256"

rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR/libexec/asm-cli" "$BUNDLE_DIR/docs"

install -m 755 "$ROOT_DIR/bin/asm" "$BUNDLE_DIR/libexec/asm-cli/asm"
install -m 644 "$ROOT_DIR/bin/asm_runtime.py" "$BUNDLE_DIR/libexec/asm-cli/asm_runtime.py"
install -m 644 "$ROOT_DIR/bin/asm_store.py" "$BUNDLE_DIR/libexec/asm-cli/asm_store.py"
install -m 644 "$ROOT_DIR/bin/asm_inspect.py" "$BUNDLE_DIR/libexec/asm-cli/asm_inspect.py"
install -m 644 "$ROOT_DIR/VERSION" "$BUNDLE_DIR/libexec/asm-cli/VERSION"

install -m 755 "$ROOT_DIR/scripts/install.sh" "$BUNDLE_DIR/install.sh"
install -m 644 "$ROOT_DIR/README.md" "$BUNDLE_DIR/README.md"
if [ -f "$ROOT_DIR/README.ko.md" ]; then
  install -m 644 "$ROOT_DIR/README.ko.md" "$BUNDLE_DIR/README.ko.md"
fi
install -m 644 "$ROOT_DIR/LICENSE" "$BUNDLE_DIR/LICENSE"
install -m 644 "$ROOT_DIR/THIRD_PARTY_NOTICES.md" "$BUNDLE_DIR/THIRD_PARTY_NOTICES.md"
install -m 644 "$ROOT_DIR/docs/qa-matrix.md" "$BUNDLE_DIR/docs/qa-matrix.md"

rm -f "$ARCHIVE_PATH" "$CHECKSUM_PATH"
tar -C "$DIST_DIR" -czf "$ARCHIVE_PATH" "asm-cli-$VERSION"
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$ARCHIVE_PATH" > "$CHECKSUM_PATH"
else
  sha256sum "$ARCHIVE_PATH" > "$CHECKSUM_PATH"
fi

echo "Built release bundle:"
echo "  $ARCHIVE_PATH"
echo "  $CHECKSUM_PATH"
