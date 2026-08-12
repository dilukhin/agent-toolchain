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

args=(
  "$SCRIPT_DIR/setup_core.py"
  --config-dir "$CONFIG_DIR"
  --stash-dir "$STASH_DIR"
  --credential-dir "$CREDENTIAL_DIR"
  --skills-dir "$SKILLS_DIR"
  --state-dir "$STATE_DIR"
  --projects-dir "$PROJECTS_DIR"
)

for arg in "$@"; do
  case "$arg" in
    --check|--force|--skip-package-install|--skip-dependency-install)
      args+=("$arg")
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--check] [--force] [--skip-package-install] [--skip-dependency-install]" >&2
      exit 2
      ;;
  esac
done

if [[ " ${args[*]} " == *" --check "* ]]; then
  echo "=== OpenCode managed environment check (Linux) ==="
else
  echo "=== OpenCode managed environment setup (Linux) ==="
fi

python3 -B "${args[@]}"
