#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${OPENCODE_CONFIG_DIR:-$HOME/.config/opencode}"
STASH_DIR="${OPENCODE_STASH_DIR:-$HOME/projects/stash/opencode.ai}"
CREDENTIAL_DIR="${OPENCODE_CREDENTIAL_DIR:-$CONFIG_DIR/credentials}"
SKILLS_DIR="${OPENCODE_SKILLS_DIR:-$HOME/.agents/skills}"
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}"
STATE_DIR="${OPENCODE_SETUP_STATE_DIR:-$STATE_BASE/opencode_setup}"
PROJECTS_DIR="${OPENCODE_PROJECTS_DIR:-$HOME/projects}"
DATA_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
RUNTIME_DIR="${OPENCODE_SETUP_RUNTIME_DIR:-$DATA_BASE/opencode_setup/runtime/python}"

args=(
  "$SCRIPT_DIR/setup_core.py"
  --config-dir "$CONFIG_DIR"
  --stash-dir "$STASH_DIR"
  --credential-dir "$CREDENTIAL_DIR"
  --skills-dir "$SKILLS_DIR"
  --state-dir "$STATE_DIR"
  --projects-dir "$PROJECTS_DIR"
)

check_mode=0
for arg in "$@"; do
  case "$arg" in
    --check)
      check_mode=1
      args+=("$arg")
      ;;
    --force|--skip-package-install|--skip-dependency-install)
      args+=("$arg")
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--check] [--force] [--skip-package-install] [--skip-dependency-install]" >&2
      exit 2
      ;;
  esac
done

if [[ $check_mode -eq 1 ]]; then
  echo "=== OpenCode managed environment check (Linux) ==="
else
  echo "=== OpenCode managed environment setup (Linux) ==="
fi

base_python="${OPENCODE_SETUP_PYTHON:-python3}"
venv_python="$RUNTIME_DIR/bin/python"
core_python="$base_python"

if [[ $check_mode -eq 1 ]]; then
  if [[ -x "$venv_python" ]]; then
    core_python="$venv_python"
  fi
else
  if [[ -e "$RUNTIME_DIR" && ! -x "$venv_python" ]]; then
    echo "Managed Python runtime path exists but is incomplete: $RUNTIME_DIR" >&2
    echo "Refusing to replace an unknown/incomplete runtime automatically." >&2
    exit 2
  fi

  if [[ ! -x "$venv_python" ]]; then
    runtime_parent="$(dirname "$RUNTIME_DIR")"
    mkdir -p "$runtime_parent"
    runtime_tmp="$runtime_parent/.python-runtime.tmp.$$"
    if [[ -e "$runtime_tmp" ]]; then
      echo "Temporary runtime path already exists: $runtime_tmp" >&2
      exit 2
    fi

    cleanup_runtime_tmp() {
      if [[ -n "${runtime_tmp:-}" && -e "$runtime_tmp" ]]; then
        rm -rf -- "$runtime_tmp"
      fi
    }
    trap cleanup_runtime_tmp EXIT

    if ! "$base_python" -B -m venv "$runtime_tmp"; then
      echo "Failed to create isolated Python runtime." >&2
      echo "On Debian/Ubuntu/Mint install the matching python3-venv package, then rerun setup." >&2
      exit 2
    fi
    if ! "$runtime_tmp/bin/python" -m pip --version >/dev/null 2>&1; then
      echo "Created Python runtime has no working pip: $runtime_tmp" >&2
      exit 2
    fi
    if [[ -e "$RUNTIME_DIR" ]]; then
      echo "Runtime path appeared concurrently; refusing to replace it: $RUNTIME_DIR" >&2
      exit 2
    fi
    mv "$runtime_tmp" "$RUNTIME_DIR"
    runtime_tmp=""
    trap - EXIT
  fi
  core_python="$venv_python"
fi

"$core_python" -B "${args[@]}"
