#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash -n "$SCRIPT_DIR/setup_linux.sh"
bash -n "$SCRIPT_DIR/install_bmad_linux.sh"
bash -n "$SCRIPT_DIR/validate_setup.sh"
echo "PASS Bash syntax"

node -e 'const d=require(process.argv[1]); if (d.bmad.skills.length !== 44) process.exit(1)' "$SCRIPT_DIR/config_data.json"
echo "PASS config_data.json (44 BMAD IDs)"

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
config_dir="$test_root/config"
stash_dir="$test_root/stash"

OPENCODE_CONFIG_DIR="$config_dir" OPENCODE_STASH_DIR="$stash_dir" OPENCODE_SETUP_SKIP_NPM=1 "$SCRIPT_DIR/setup_linux.sh"
printf '%s' 'test-key-must-survive' > "$stash_dir/api-key.txt"
OPENCODE_CONFIG_DIR="$config_dir" OPENCODE_STASH_DIR="$stash_dir" OPENCODE_SETUP_SKIP_NPM=1 "$SCRIPT_DIR/setup_linux.sh"

[[ "$(cat "$stash_dir/api-key.txt")" == "test-key-must-survive" ]]
[[ "$(stat -c '%a' "$stash_dir/api-key.txt")" == "600" ]]
node -e 'const fs=require("node:fs"); const d=JSON.parse(fs.readFileSync(process.argv[1], "utf8")); if (d.$schema !== "https://opencode.ai/config.json" || Object.keys(d.provider.routerai.models).length !== 13) process.exit(1)' "$config_dir/opencode.jsonc"
echo "PASS isolated Linux setup, config structure, API key preservation, and mode 0600"

if [[ "${1:-}" == "--bmad" ]]; then
  mkdir "$test_root/bmad-project"
  "$SCRIPT_DIR/install_bmad_linux.sh" "$test_root/bmad-project"
  "$SCRIPT_DIR/install_bmad_linux.sh" "$test_root/bmad-project"
  echo "PASS isolated BMAD install and repeated install"
fi
