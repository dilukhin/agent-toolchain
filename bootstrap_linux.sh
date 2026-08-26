#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_CMD="${AGENT_TOOLCHAIN_PYTHON:-python3}"

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python 3.10+ is required to bootstrap agent-toolchain: $PYTHON_CMD" >&2
  exit 2
fi
PYTHON_EXE="$(command -v "$PYTHON_CMD")"
if ! "$PYTHON_EXE" -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 2)'; then
  echo "agent-toolchain requires Python 3.10+ for its stdlib-only core." >&2
  exit 2
fi

exec "$PYTHON_EXE" -B "$SOURCE_DIR/bootstrap_core.py"
