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
marker_file="$RUNTIME_DIR/.opencode-setup-managed-runtime"
marker_expected="opencode_setup-bootstrap-python-v1"
runtime_owned=0
if [[ -f "$marker_file" ]] && [[ "$(cat "$marker_file")" == "$marker_expected" ]]; then
  runtime_owned=1
fi
core_python="$base_python"

print_manual_venv_action() {
  local version="$1"
  echo "MANUAL ACTION REQUIRED: install Python venv support for base Python $version, then rerun setup." >&2
  if command -v apt >/dev/null 2>&1; then
    echo "  sudo apt install python${version}-venv" >&2
  else
    echo "  Install the distribution package that provides ensurepip/venv for Python $version." >&2
  fi
}

venv_prerequisite_ok=1
base_python_version=""
if [[ ! -e "$RUNTIME_DIR" ]]; then
  if ! base_python_version="$("$base_python" -B -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"; then
    echo "Base Python is not runnable: $base_python" >&2
    echo "MANUAL ACTION REQUIRED: install a runnable Python or set OPENCODE_SETUP_PYTHON to one, then rerun setup." >&2
    exit 2
  fi
  if ! "$base_python" -B -c 'import ensurepip, venv' >/dev/null 2>&1; then
    venv_prerequisite_ok=0
  fi
fi

if [[ $check_mode -eq 1 ]]; then
  if [[ -e "$RUNTIME_DIR" ]]; then
    if [[ $runtime_owned -ne 1 || ! -x "$venv_python" ]]; then
      echo "Managed Python runtime path exists but ownership/health is not proven: $RUNTIME_DIR" >&2
      echo "Check mode is read-only; refusing to adopt or repair this directory." >&2
      echo "MANUAL ACTION REQUIRED: inspect this runtime path and resolve its ownership/health before rerunning setup; do not delete or overwrite unknown data." >&2
      exit 2
    fi
    core_python="$venv_python"
  elif [[ $venv_prerequisite_ok -ne 1 ]]; then
    echo "PREREQUISITE: base Python $base_python_version cannot create the isolated bootstrap runtime because ensurepip/venv support is unavailable." >&2
    print_manual_venv_action "$base_python_version"
  fi
else
  if [[ -e "$RUNTIME_DIR" && ( $runtime_owned -ne 1 || ! -x "$venv_python" ) ]]; then
    echo "Managed Python runtime path exists but ownership/health is not proven: $RUNTIME_DIR" >&2
    echo "Refusing to replace an unknown/incomplete runtime automatically." >&2
    echo "MANUAL ACTION REQUIRED: inspect this runtime path and resolve its ownership/health before rerunning setup; do not delete or overwrite unknown data." >&2
    exit 2
  fi

  if [[ ! -e "$RUNTIME_DIR" ]]; then
    if [[ $venv_prerequisite_ok -ne 1 ]]; then
      echo "Cannot create isolated Python runtime: base Python $base_python_version has no ensurepip/venv support." >&2
      print_manual_venv_action "$base_python_version"
      exit 2
    fi

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
      echo "Failed to create isolated Python runtime after prerequisite checks passed." >&2
      exit 2
    fi
    if ! "$runtime_tmp/bin/python" -m pip --version >/dev/null 2>&1; then
      echo "Created Python runtime has no working pip: $runtime_tmp" >&2
      exit 2
    fi
    printf '%s\n' "$marker_expected" > "$runtime_tmp/.opencode-setup-managed-runtime"
    if [[ -e "$RUNTIME_DIR" ]]; then
      echo "Runtime path appeared concurrently; refusing to replace it: $RUNTIME_DIR" >&2
      exit 2
    fi
    mv "$runtime_tmp" "$RUNTIME_DIR"
    runtime_tmp=""
    trap - EXIT
    runtime_owned=1
  fi
  core_python="$venv_python"
fi

"$core_python" -B "${args[@]}"
