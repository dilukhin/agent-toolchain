#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash -n "$SCRIPT_DIR/setup_linux.sh"
bash -n "$SCRIPT_DIR/install_bmad_linux.sh" 2>/dev/null || true
bash -n "$SCRIPT_DIR/validate_setup.sh"
python3 -m py_compile "$SCRIPT_DIR/setup_core.py" "$SCRIPT_DIR/setup_lib.py" "$SCRIPT_DIR/setup_migration.py" "$SCRIPT_DIR/setup_runtime.py" "$SCRIPT_DIR/setup_inventory.py"
python3 - <<'PY' "$SCRIPT_DIR/config_data.json"
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data=json.load(fh)
assert len(data["models"]) == 13
skills=data["bmad"]["skills"]
assert skills and len(skills) == len(set(skills))
assert data["managed_environment"]["owned_skills"] == ["remote-long-running"]
assert set(data["managed_environment"]["external_skills"]) == {"ssh-relay","recovery-mode","risk-gate","safe-cli","unknown-system-safety"}
PY
echo "PASS syntax/config_data"

python3 - <<'PY_NPM' "$SCRIPT_DIR"
import importlib.util, json, pathlib, subprocess, sys, tempfile, types
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("setup_runtime_test", root / "setup_runtime.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
commands = []
def fake_run(cmd, cwd=None, env=None):
    commands.append(cmd)
    if len(cmd) > 1 and cmd[1] == "list":
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies":{"opencode-ai":{"version":"1.2.3"}}}), "")
    if len(cmd) > 1 and cmd[1] == "view":
        return subprocess.CompletedProcess(cmd, 0, json.dumps("1.2.3"), "")
    raise AssertionError(f"unexpected command: {cmd}")
mod.run = fake_run
mod.shutil.which = lambda name: "/fake/npm" if name == "npm" else None
mod.report_common_tool_inventory = lambda reporter: None
mod.executable_inventory = lambda command: [
    types.SimpleNamespace(path=pathlib.Path("/fake/npm/opencode"), version="1.2.3", manager="npm", active=True)
] if command == "opencode" else []
mod._known_opencode_managers = lambda npm: {"npm": "1.2.3"}
with tempfile.TemporaryDirectory() as td:
    cfg = pathlib.Path(td)
    pkg = cfg / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
    pkg.parent.mkdir(parents=True)
    pkg.write_text(json.dumps({"version":"1.15.4"}), encoding="utf-8")
    reporter = mod.Reporter()
    mod.reconcile_npm(cfg, {"dependencies":{"opencode-cli-package":"opencode-ai","@opencode-ai/plugin":"1.15.4"}}, reporter, check=False, skip=False)
    assert all(r.state == mod.STATE_OK for r in reporter.results), reporter.results
    assert not any("install" in cmd for cmd in commands), commands
PY_NPM
echo "PASS up-to-date npm components are no-op"

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

make_remote() {
  local kind="$1" branch="$2"
  local seed="$test_root/${kind}-seed" bare="$test_root/${kind}.git"
  git init -q -b "$branch" "$seed"
  git -C "$seed" config user.email test@example.invalid
  git -C "$seed" config user.name opencode-setup-test
  if [[ "$kind" == "ssh" ]]; then
    mkdir -p "$seed/opencode/skills/ssh-relay"
    cat > "$seed/opencode/skills/ssh-relay/SKILL.md" <<'SKILL'
---
name: ssh-relay
description: Test authoritative ssh-relay skill.
compatibility: opencode
---
# ssh relay fixture v1
SKILL
  else
    local skill
    for skill in recovery-mode risk-gate safe-cli unknown-system-safety; do
      mkdir -p "$seed/opencode/skills/$skill"
      cat > "$seed/opencode/skills/$skill/SKILL.md" <<SKILL
---
name: $skill
description: Test authoritative $skill skill.
compatibility: opencode
---
# $skill fixture
SKILL
    done
  fi
  git -C "$seed" add .
  git -C "$seed" commit -qm init
  git clone -q --bare "$seed" "$bare"
  git -C "$seed" remote add origin "$bare"
  git -C "$seed" push -q -u origin "$branch"
}

make_remote ssh main
make_remote safe master
ssh_remote="$test_root/ssh.git"
safe_remote="$test_root/safe.git"
home="$test_root/home"
config_dir="$home/.config/opencode"
stash_dir="$home/projects/stash/opencode.ai"
skills_dir="$home/.agents/skills"
state_dir="$home/.local/state/opencode_setup"
projects_dir="$home/projects"

mkdir -p "$stash_dir" "$skills_dir/custom-user" "$skills_dir/bmad-user-skill"
printf 'rk-test-preserve\r\nsecond-line' > "$stash_dir/api-key.txt"
printf '%s\n' 'user skill must survive' > "$skills_dir/custom-user/SKILL.md"
printf '%s\n' 'BMAD-like user skill must survive' > "$skills_dir/bmad-user-skill/SKILL.md"
key_before="$(sha256sum "$stash_dir/api-key.txt" | awk '{print $1}')"

missing_check="$test_root/check-only-home"
python3 "$SCRIPT_DIR/setup_core.py" \
  --config-dir "$missing_check/.config/opencode" \
  --stash-dir "$missing_check/projects/stash/opencode.ai" \
  --skills-dir "$missing_check/.agents/skills" \
  --state-dir "$missing_check/.local/state/opencode_setup" \
  --projects-dir "$missing_check/projects" \
  --skip-package-install --skip-dependency-install \
  --ssh-relay-url "$ssh_remote" --agent-safe-url "$safe_remote" --check > "$test_root/missing-check.out"
[[ ! -e "$missing_check" ]]
echo "PASS --check creates nothing for a missing target"

core_args=(
  "$SCRIPT_DIR/setup_core.py"
  --config-dir "$config_dir"
  --stash-dir "$stash_dir"
  --skills-dir "$skills_dir"
  --state-dir "$state_dir"
  --projects-dir "$projects_dir"
  --skip-package-install
  --skip-dependency-install
  --ssh-relay-url "$ssh_remote"
  --agent-safe-url "$safe_remote"
)

python3 "${core_args[@]}" > "$test_root/install.out"
[[ "$key_before" == "$(sha256sum "$stash_dir/api-key.txt" | awk '{print $1}')" ]]
[[ -f "$skills_dir/custom-user/SKILL.md" ]]
[[ -f "$skills_dir/bmad-user-skill/SKILL.md" ]]
[[ -f "$state_dir/manifest.json" ]]
for skill in ssh-relay remote-long-running recovery-mode risk-gate safe-cli unknown-system-safety; do
  [[ -f "$skills_dir/$skill/SKILL.md" ]]
done
node -e 'const fs=require("node:fs"); const d=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); if(Object.keys(d.provider.routerai.models).length!==13) process.exit(1)' "$config_dir/opencode.jsonc"
[[ "$(wc -l < "$config_dir/AGENTS.md")" -le 12 ]]
echo "PASS clean isolated install + unknown/BMAD/API-key preservation"

snapshot_tree() {
  local root="$1"
  find "$root" -type f -print0 | sort -z | while IFS= read -r -d '' f; do
    printf '%s  ' "${f#$root/}"
    sha256sum "$f" | awk '{print $1}'
  done
}

before_repeat="$(snapshot_tree "$home")"
python3 "${core_args[@]}" > "$test_root/repeat.out"
after_repeat="$(snapshot_tree "$home")"
[[ "$before_repeat" == "$after_repeat" ]]
echo "PASS repeated setup is idempotent"

cat > "$test_root/ssh-seed/opencode/skills/ssh-relay/SKILL.md" <<'SKILL'
---
name: ssh-relay
description: Test authoritative ssh-relay skill, updated.
compatibility: opencode
---
# ssh relay fixture v2
SKILL
git -C "$test_root/ssh-seed" add .
git -C "$test_root/ssh-seed" commit -qm update
git -C "$test_root/ssh-seed" push -q origin main
python3 "${core_args[@]}" --check > "$test_root/update-check.out"
grep -q 'outdated.*ssh_relay repository' "$test_root/update-check.out"
python3 "${core_args[@]}" > "$test_root/update.out"
grep -q 'fixture v2' "$skills_dir/ssh-relay/SKILL.md"
echo "PASS clean dependency repo fast-forward + managed skill update"

printf '\nLOCAL MANUAL CHANGE\n' >> "$skills_dir/remote-long-running/SKILL.md"
manual_hash="$(sha256sum "$skills_dir/remote-long-running/SKILL.md" | awk '{print $1}')"
set +e
python3 "${core_args[@]}" > "$test_root/manual-conflict.out" 2>&1
rc=$?
set -e
[[ $rc -eq 2 ]]
[[ "$manual_hash" == "$(sha256sum "$skills_dir/remote-long-running/SKILL.md" | awk '{print $1}')" ]]
grep -q 'modified/conflict.*skill remote-long-running' "$test_root/manual-conflict.out"
python3 "${core_args[@]}" --force > "$test_root/force.out"
! grep -q 'LOCAL MANUAL CHANGE' "$skills_dir/remote-long-running/SKILL.md"
find "$state_dir/backups" -type f -name SKILL.md -print -quit | grep -q .
echo "PASS locally modified managed skill is preserved; --force backs up and replaces owned file"

printf '%s\n' 'keep me' > "$projects_dir/agent-safe/local-user-file.txt"
set +e
python3 "${core_args[@]}" > "$test_root/dirty-conflict.out" 2>&1
rc=$?
set -e
[[ $rc -eq 2 ]]
[[ -f "$projects_dir/agent-safe/local-user-file.txt" ]]
grep -q 'modified/conflict.*agent-safe repository' "$test_root/dirty-conflict.out"
rm "$projects_dir/agent-safe/local-user-file.txt"
echo "PASS dirty dependency repo preserved without reset/clean"

python3 "${core_args[@]}" > /dev/null
before_check="$(snapshot_tree "$home")"
python3 "${core_args[@]}" --check > "$test_root/final-check.out"
after_check="$(snapshot_tree "$home")"
[[ "$before_check" == "$after_check" ]]
! grep -qE '^(missing|outdated|modified/conflict)' "$test_root/final-check.out"
echo "PASS --check is read-only and final state is up-to-date"

git -C "$projects_dir/agent-safe" config user.email test@example.invalid
git -C "$projects_dir/agent-safe" config user.name opencode-setup-test
printf '%s\n' 'local committed work' > "$projects_dir/agent-safe/local-commit.txt"
git -C "$projects_dir/agent-safe" add local-commit.txt
git -C "$projects_dir/agent-safe" commit -qm local-work
local_head="$(git -C "$projects_dir/agent-safe" rev-parse HEAD)"
set +e
python3 "${core_args[@]}" > "$test_root/local-commit-conflict.out" 2>&1
rc=$?
set -e
[[ $rc -eq 2 ]]
[[ "$local_head" == "$(git -C "$projects_dir/agent-safe" rev-parse HEAD)" ]]
grep -q 'modified/conflict.*agent-safe repository.*local commits' "$test_root/local-commit-conflict.out"
echo "PASS clean dependency repo with local commits is preserved as conflict"

python3 - <<'PY' "$SCRIPT_DIR" "$skills_dir"
import importlib.util, pathlib, sys
root=pathlib.Path(sys.argv[1]); skills=pathlib.Path(sys.argv[2])
spec=importlib.util.spec_from_file_location("setup_core", root/"setup_core.py")
mod=importlib.util.module_from_spec(spec); sys.modules["setup_core"]=mod; spec.loader.exec_module(mod)
for name in ["ssh-relay","remote-long-running","recovery-mode","risk-gate","safe-cli","unknown-system-safety"]:
    ok, detail=mod.validate_skill(skills/name/"SKILL.md", name)
    assert ok, (name, detail)
PY
echo "PASS managed skill structure/front matter"

home2="$test_root/home2"
python3 "$SCRIPT_DIR/setup_core.py" \
  --config-dir "$home2/.config/opencode" \
  --stash-dir "$home2/projects/stash/opencode.ai" \
  --skills-dir "$home2/.agents/skills" \
  --state-dir "$home2/.local/state/opencode_setup" \
  --projects-dir "$home2/projects" \
  --skip-package-install --skip-dependency-install \
  --ssh-relay-url "$ssh_remote" --agent-safe-url "$safe_remote" > "$test_root/missing-key.out"
[[ ! -e "$home2/projects/stash/opencode.ai/api-key.txt" ]]
grep -q 'missing.*RouterAI credential.*ключ RouterAI не настроен' "$test_root/missing-key.out"
python3 - <<'PY_KEY' "$home2/.config/opencode/opencode.jsonc" "$home2/projects/stash/opencode.ai/api-key.txt"
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = "{file:" + str(pathlib.Path(sys.argv[2]).resolve()) + "}"
assert config["provider"]["routerai"]["options"]["apiKey"] == expected
PY_KEY
echo "PASS missing API key remains unprovisioned without fake file"

home3="$test_root/home3"
mkdir -p "$home3/.config/opencode" "$home3/projects/stash/opencode.ai"
printf '%s\n' '{"user_setting":true}' > "$home3/.config/opencode/opencode.jsonc"
printf '%s' 'existing-key' > "$home3/projects/stash/opencode.ai/api-key.txt"
key3_before="$(sha256sum "$home3/projects/stash/opencode.ai/api-key.txt" | awk '{print $1}')"
python3 "$SCRIPT_DIR/setup_core.py" \
  --config-dir "$home3/.config/opencode" \
  --stash-dir "$home3/projects/stash/opencode.ai" \
  --skills-dir "$home3/.agents/skills" \
  --state-dir "$home3/.local/state/opencode_setup" \
  --projects-dir "$home3/projects" \
  --skip-package-install --skip-dependency-install \
  --ssh-relay-url "$ssh_remote" --agent-safe-url "$safe_remote" > "$test_root/plain-config-migration.out" 2>&1
[[ "$key3_before" == "$(sha256sum "$home3/projects/stash/opencode.ai/api-key.txt" | awk '{print $1}')" ]]
python3 - <<'PY_PLAIN' "$home3/.config/opencode/opencode.jsonc" "$home3/projects/stash/opencode.ai/api-key.txt"
import json, pathlib, sys
config = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = "{file:" + str(pathlib.Path(sys.argv[2]).resolve()) + "}"
assert config["user_setting"] is True
assert config["provider"]["routerai"]["options"]["apiKey"] == expected
PY_PLAIN
find "$home3/.local/state/opencode_setup/backups" -type f -name opencode.jsonc -print -quit | grep -q .
grep -q 'up-to-date.*RouterAI credential' "$test_root/plain-config-migration.out"
grep -q 'OpenCode config migration.*up-to-date' "$test_root/plain-config-migration.out"
echo "PASS existing plain OpenCode config is safely migrated with backup and credential preservation"

if grep -RInE --exclude='validate_setup.sh' --exclude='*.md' -- '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{20,}' "$SCRIPT_DIR"; then
  echo "Possible secret material found" >&2
  exit 1
fi
echo "PASS basic secret scan"

if [[ "${1:-}" == "--bmad" ]]; then
  bmad_target="$test_root/bmad-project"
  mkdir "$bmad_target"
  "$SCRIPT_DIR/install_bmad_linux.sh" "$bmad_target"
  "$SCRIPT_DIR/install_bmad_linux.sh" "$bmad_target"
  echo "PASS isolated BMAD install and repeated install"
fi
