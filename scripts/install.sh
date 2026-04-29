#!/usr/bin/env bash

set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="${BIN_DIR:-$PREFIX/bin}"
LIBEXEC_DIR="${LIBEXEC_DIR:-$PREFIX/libexec/asm-cli}"
DOC_DIR="${DOC_DIR:-$PREFIX/share/doc/asm-cli}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOURCE_DIR=""
SOURCE_ROOT=""

detect_source_layout() {
  if [ -f "$SCRIPT_DIR/../bin/asm" ]; then
    SOURCE_DIR="$(cd "$SCRIPT_DIR/../bin" && pwd)"
    SOURCE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    return 0
  fi

  if [ -f "$SCRIPT_DIR/libexec/asm-cli/asm" ]; then
    SOURCE_DIR="$(cd "$SCRIPT_DIR/libexec/asm-cli" && pwd)"
    SOURCE_ROOT="$SCRIPT_DIR"
    return 0
  fi

  echo "failed to locate asm source files from: $SCRIPT_DIR" >&2
  return 1
}

detect_source_layout

mkdir -p "$BIN_DIR" "$LIBEXEC_DIR" "$DOC_DIR"

install -m 755 "$SOURCE_DIR/asm" "$LIBEXEC_DIR/asm"
install -m 644 "$SOURCE_DIR/asm_runtime.py" "$LIBEXEC_DIR/asm_runtime.py"
install -m 644 "$SOURCE_DIR/asm_store.py" "$LIBEXEC_DIR/asm_store.py"
install -m 644 "$SOURCE_DIR/asm_inspect.py" "$LIBEXEC_DIR/asm_inspect.py"
install -m 644 "$SOURCE_ROOT/VERSION" "$LIBEXEC_DIR/VERSION"

install -m 644 "$SOURCE_ROOT/README.md" "$DOC_DIR/README.md"
if [ -f "$SOURCE_ROOT/README.ko.md" ]; then
  install -m 644 "$SOURCE_ROOT/README.ko.md" "$DOC_DIR/README.ko.md"
fi
install -m 644 "$SOURCE_ROOT/LICENSE" "$DOC_DIR/LICENSE"
install -m 644 "$SOURCE_ROOT/THIRD_PARTY_NOTICES.md" "$DOC_DIR/THIRD_PARTY_NOTICES.md"

cat > "$BIN_DIR/asm" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export ASM_LIBEXEC_DIR="$LIBEXEC_DIR"
exec "$LIBEXEC_DIR/asm" "\$@"
EOF
chmod 755 "$BIN_DIR/asm"

echo "Installed asm"
echo "  prefix: $PREFIX"
echo "  bin: $BIN_DIR/asm"
echo "  libexec: $LIBEXEC_DIR"
echo "  docs: $DOC_DIR"
