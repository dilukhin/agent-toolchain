#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA_ROOT="${AGENT_TOOLCHAIN_DATA_DIR:-$DATA_BASE/agent-toolchain}"
CORE_DIR="$DATA_ROOT/core"
BIN_DIR="${AGENT_TOOLCHAIN_BIN_DIR:-$HOME/.local/bin}"
ENTRYPOINT="$BIN_DIR/toolchainctl"
MARKER="agent-toolchain-core-v1"
MARKER_FILE=".agent-toolchain-managed-core"
ENTRYPOINT_MARKER="# agent-toolchain:managed-core-entrypoint:v1"
PYTHON_CMD="${AGENT_TOOLCHAIN_PYTHON:-python3}"

required_files=(
  toolchainctl.py
  setup_core.py
  setup_lib.py
  setup_manifest.py
  setup_migration.py
  setup_runtime.py
  setup_runtime_legacy.py
  setup_managed_tools.py
  setup_inventory.py
  setup_tools.py
  config_data.json
)

for path in "${required_files[@]}"; do
  if [[ ! -f "$SOURCE_DIR/$path" ]]; then
    echo "Missing bootstrap source file: $SOURCE_DIR/$path" >&2
    exit 2
  fi
done
for path in templates skills/remote-long-running; do
  if [[ ! -d "$SOURCE_DIR/$path" ]]; then
    echo "Missing bootstrap source directory: $SOURCE_DIR/$path" >&2
    exit 2
  fi
done

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python 3 is required to bootstrap agent-toolchain: $PYTHON_CMD" >&2
  exit 2
fi
PYTHON_EXE="$(command -v "$PYTHON_CMD")"
if ! "$PYTHON_EXE" -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'; then
  echo "agent-toolchain requires Python 3.10+ for its stdlib-only core." >&2
  exit 2
fi

if [[ -e "$ENTRYPOINT" ]]; then
  second_line="$(sed -n '2p' "$ENTRYPOINT" 2>/dev/null || true)"
  if [[ "$second_line" != "$ENTRYPOINT_MARKER" ]]; then
    echo "Refusing to replace foreign toolchainctl entrypoint: $ENTRYPOINT" >&2
    exit 2
  fi
fi
if [[ -e "$CORE_DIR" ]]; then
  if [[ ! -f "$CORE_DIR/$MARKER_FILE" || "$(cat "$CORE_DIR/$MARKER_FILE")" != "$MARKER" ]]; then
    echo "Refusing to replace core directory without exact agent-toolchain ownership marker: $CORE_DIR" >&2
    exit 2
  fi
fi

mkdir -p "$DATA_ROOT" "$BIN_DIR"
staging="$(mktemp -d "$DATA_ROOT/.core.tmp.XXXXXX")"
backup=""
cleanup() {
  if [[ -n "${staging:-}" && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT

for path in "${required_files[@]}"; do
  cp -p "$SOURCE_DIR/$path" "$staging/$path"
done
cp -a "$SOURCE_DIR/templates" "$staging/templates"
mkdir -p "$staging/skills"
cp -a "$SOURCE_DIR/skills/remote-long-running" "$staging/skills/remote-long-running"
printf '%s\n' "$MARKER" > "$staging/$MARKER_FILE"

"$PYTHON_EXE" -B -m py_compile \
  "$staging/toolchainctl.py" \
  "$staging/setup_core.py" \
  "$staging/setup_lib.py" \
  "$staging/setup_manifest.py" \
  "$staging/setup_migration.py" \
  "$staging/setup_runtime.py" \
  "$staging/setup_runtime_legacy.py" \
  "$staging/setup_managed_tools.py" \
  "$staging/setup_inventory.py" \
  "$staging/setup_tools.py"

if [[ -e "$CORE_DIR" ]]; then
  backup="$DATA_ROOT/core.previous.$(date +%Y%m%d%H%M%S).$$"
  if [[ -e "$backup" ]]; then
    echo "Bootstrap backup path already exists: $backup" >&2
    exit 2
  fi
  mv "$CORE_DIR" "$backup"
fi
if ! mv "$staging" "$CORE_DIR"; then
  if [[ -n "$backup" && -d "$backup" && ! -e "$CORE_DIR" ]]; then
    mv "$backup" "$CORE_DIR"
  fi
  echo "Failed to publish agent-toolchain core; previous managed core restored when possible." >&2
  exit 2
fi
staging=""

entry_tmp="$ENTRYPOINT.tmp.$$"
cat > "$entry_tmp" <<EOF
#!/usr/bin/env bash
$ENTRYPOINT_MARKER
exec "$PYTHON_EXE" -B "$CORE_DIR/toolchainctl.py" "\$@"
EOF
chmod 0755 "$entry_tmp"
mv -f "$entry_tmp" "$ENTRYPOINT"

printf 'configured        agent-toolchain core  %s\n' "$CORE_DIR"
printf 'configured        toolchainctl entrypoint  %s\n' "$ENTRYPOINT"
if [[ -n "$backup" ]]; then
  printf 'info              previous managed core retained  %s\n' "$backup"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*)
    "$ENTRYPOINT" --help >/dev/null
    printf 'up-to-date        toolchainctl command resolution  %s\n' "$ENTRYPOINT"
    ;;
  *)
    printf 'outdated          toolchainctl command resolution  MANUAL ACTION REQUIRED: add %s to PATH or start a new login shell; absolute command: %s\n' "$BIN_DIR" "$ENTRYPOINT"
    ;;
esac
