#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BMAD_VERSION="$(node -p "require('$SCRIPT_DIR/config_data.json').bmad.version")"
EXPECTED_INTEGRITY="$(node -p "require('$SCRIPT_DIR/config_data.json').bmad.npm_integrity")"
PROJECT_PATH="$(realpath "${1:-.}")"
MANIFEST_PATH="$PROJECT_PATH/_bmad/_config/manifest.yaml"
SKILLS_PATH="$PROJECT_PATH/.agents/skills"

if [[ ! -d "$PROJECT_PATH" ]]; then
  echo "Project directory does not exist: $PROJECT_PATH" >&2
  exit 1
fi

node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 20 || (major === 20 && minor < 12)) process.exit(1)' || {
  echo "BMAD $BMAD_VERSION requires Node.js 20.12.0 or newer." >&2
  exit 1
}

if [[ -f "$MANIFEST_PATH" ]]; then
  BMAD_VERSION_REGEX="${BMAD_VERSION//./\\.}"
  if ! grep -Eq "^[[:space:]]*version:[[:space:]]*$BMAD_VERSION_REGEX[[:space:]]*$" "$MANIFEST_PATH"; then
    echo "Existing BMAD is not version $BMAD_VERSION. It was preserved; update it manually before retrying." >&2
    exit 1
  fi
elif compgen -G "$SKILLS_PATH/bmad-*/SKILL.md" >/dev/null; then
  echo "Unmanaged BMAD skills already exist in $SKILLS_PATH. They were preserved; move or reconcile them before retrying." >&2
  exit 1
fi

echo "Checking official bmad-method@$BMAD_VERSION package..."
actual_integrity="$(npm view "bmad-method@$BMAD_VERSION" dist.integrity)"
if [[ "$actual_integrity" != "$EXPECTED_INTEGRITY" ]]; then
  echo "npm integrity mismatch for bmad-method@$BMAD_VERSION." >&2
  exit 1
fi

echo "Installing project-local BMAD $BMAD_VERSION into $PROJECT_PATH..."
npx --yes "bmad-method@$BMAD_VERSION" install --directory "$PROJECT_PATH" --modules bmm --tools opencode --yes
node "$SCRIPT_DIR/validate_bmad.js" "$PROJECT_PATH"
