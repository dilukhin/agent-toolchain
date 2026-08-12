#!/usr/bin/env python3
"""Idempotent OpenCode environment reconciler used by Windows/Linux wrappers."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from setup_lib import (
    Reporter,
    STATE_CONFLICT,
    STATE_MISSING,
    STATE_OK,
    STATE_OUTDATED,
    atomic_write,
    load_manifest,
    reconcile_file,
    reconcile_repo,
    save_manifest,
    sha256_bytes,
    validate_skill,
)
from setup_runtime import ensure_agent_safe_runtime, ensure_ssh_relay_runtime, reconcile_npm

LEGACY_AGENTS = """# Global OpenCode instructions

- Never expose secrets, tokens, passwords, or API keys.
- Do not scan .git, node_modules, build output, caches, or logs without a reason.
- Prefer concise, structured answers.
"""


def render_config(template_path: Path, api_key_file: Path) -> bytes:
    template = template_path.read_text(encoding="utf-8")
    escaped = str(api_key_file).replace("\\", "\\\\")
    return template.replace("__ROUTERAI_API_KEY_FILE__", escaped).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reconcile managed OpenCode environment components safely.")
    p.add_argument("--check", action="store_true", help="report only; do not change files or repositories")
    p.add_argument("--force", action="store_true", help="backup and replace locally modified files already tracked by manifest")
    p.add_argument("--repo-root", default=str(Path(__file__).resolve().parent))
    p.add_argument("--config-dir", required=True)
    p.add_argument("--stash-dir", required=True)
    p.add_argument("--skills-dir", required=True)
    p.add_argument("--state-dir", required=True)
    p.add_argument("--projects-dir", required=True)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--skip-package-install", action="store_true")
    p.add_argument("--skip-dependency-install", action="store_true")
    p.add_argument("--ssh-relay-url")
    p.add_argument("--agent-safe-url")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config_dir = Path(args.config_dir).expanduser().resolve()
    stash_dir = Path(args.stash_dir).expanduser().resolve()
    skills_dir = Path(args.skills_dir).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    projects_dir = Path(args.projects_dir).expanduser().resolve()
    manifest_path = state_dir / "manifest.json"

    config = json.loads((repo_root / "config_data.json").read_text(encoding="utf-8"))
    env_cfg = config["managed_environment"]
    ssh_spec = env_cfg["dependencies"]["ssh_relay"]
    safe_spec = env_cfg["dependencies"]["agent_safe"]
    ssh_url = args.ssh_relay_url or os.environ.get("OPENCODE_SETUP_SSH_RELAY_URL") or ssh_spec["repo"]
    safe_url = args.agent_safe_url or os.environ.get("OPENCODE_SETUP_AGENT_SAFE_URL") or safe_spec["repo"]

    reporter = Reporter()
    manifest, manifest_error = load_manifest(manifest_path)
    if manifest_error:
        reporter.add("ownership manifest", STATE_CONFLICT, manifest_error)
        reporter.render()
        return 2

    reconcile_npm(config_dir, config, reporter, args.check, args.skip_package_install)

    api_key_file = stash_dir / "api-key.txt"
    if api_key_file.exists():
        if api_key_file.is_file():
            reporter.add("RouterAI api-key.txt", STATE_OK, "existing bytes preserved")
            if not args.check and os.name != "nt":
                os.chmod(api_key_file, 0o600)
        else:
            reporter.add("RouterAI api-key.txt", STATE_CONFLICT, "path exists but is not a regular file")
    else:
        reporter.add("RouterAI api-key.txt", STATE_MISSING, str(api_key_file))
        if not args.check:
            api_key_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(api_key_file, b"your-routerai-api-key-here\n")
            if os.name != "nt":
                os.chmod(api_key_file, 0o600)

    manifest_changed = False
    config_data = render_config(repo_root / "templates" / "opencode.jsonc", api_key_file)
    manifest_changed |= reconcile_file(
        component="OpenCode config",
        destination=config_dir / "opencode.jsonc",
        source_data=config_data,
        source_label="opencode_setup:templates/opencode.jsonc",
        manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
    )

    agents_data = (repo_root / "templates" / "AGENTS.md").read_bytes()
    manifest_changed |= reconcile_file(
        component="global AGENTS.md",
        destination=config_dir / "AGENTS.md",
        source_data=agents_data,
        source_label="opencode_setup:templates/AGENTS.md",
        manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
        legacy_hashes=[sha256_bytes(LEGACY_AGENTS.encode("utf-8"))],
    )

    remote_skill = repo_root / "skills" / "remote-long-running" / "SKILL.md"
    valid, detail = validate_skill(remote_skill, "remote-long-running")
    if not valid:
        reporter.add("skill remote-long-running", STATE_CONFLICT, f"source invalid: {detail}")
    else:
        manifest_changed |= reconcile_file(
            component="skill remote-long-running",
            destination=skills_dir / "remote-long-running" / "SKILL.md",
            source_data=remote_skill.read_bytes(),
            source_label="opencode_setup:skills/remote-long-running/SKILL.md",
            manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
        )

    if not shutil.which("git"):
        reporter.add("dependency repositories", STATE_CONFLICT, "git is not available")
        ssh_usable = safe_usable = False
        ssh_repo_state = safe_repo_state = STATE_CONFLICT
    else:
        ssh_repo = projects_dir / ssh_spec["directory"]
        safe_repo = projects_dir / safe_spec["directory"]
        ssh_usable, ssh_repo_state = reconcile_repo(
            component="ssh_relay repository", path=ssh_repo, url=ssh_url,
            branch=ssh_spec["branch"], reporter=reporter, check=args.check,
        )
        safe_usable, safe_repo_state = reconcile_repo(
            component="agent-safe repository", path=safe_repo, url=safe_url,
            branch=safe_spec["branch"], reporter=reporter, check=args.check,
        )

        if ssh_usable:
            ensure_ssh_relay_runtime(ssh_repo, args.python, reporter, args.check, args.skip_dependency_install)
        if safe_usable:
            ensure_agent_safe_runtime(safe_repo, args.python, reporter, args.check, args.skip_dependency_install)

        if ssh_usable:
            source = ssh_repo / ssh_spec["skill"]
            valid, detail = validate_skill(source, "ssh-relay")
            if not valid:
                reporter.add("skill ssh-relay", STATE_CONFLICT, f"source invalid: {detail}")
            elif args.check and ssh_repo_state == STATE_OUTDATED:
                reporter.add("skill ssh-relay", STATE_OUTDATED, "dependency repository has upstream changes")
            else:
                manifest_changed |= reconcile_file(
                    component="skill ssh-relay",
                    destination=skills_dir / "ssh-relay" / "SKILL.md",
                    source_data=source.read_bytes(), source_label="ssh_relay:opencode/skills/ssh-relay/SKILL.md",
                    manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
                )
        else:
            state = STATE_MISSING if ssh_repo_state == STATE_MISSING else STATE_CONFLICT
            reporter.add("skill ssh-relay", state, "authoritative repository is not usable")

        if safe_usable:
            for skill_name, relative in safe_spec["skills"].items():
                source = safe_repo / relative
                valid, detail = validate_skill(source, skill_name)
                component = f"skill {skill_name}"
                if not valid:
                    reporter.add(component, STATE_CONFLICT, f"source invalid: {detail}")
                    continue
                if args.check and safe_repo_state == STATE_OUTDATED:
                    reporter.add(component, STATE_OUTDATED, "dependency repository has upstream changes")
                    continue
                manifest_changed |= reconcile_file(
                    component=component, destination=skills_dir / skill_name / "SKILL.md",
                    source_data=source.read_bytes(), source_label=f"agent-safe:{relative}",
                    manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
                )
        else:
            state = STATE_MISSING if safe_repo_state == STATE_MISSING else STATE_CONFLICT
            for skill_name in safe_spec["skills"]:
                reporter.add(f"skill {skill_name}", state, "authoritative repository is not usable")

    if not args.check and manifest_changed:
        state_dir.mkdir(parents=True, exist_ok=True)
        save_manifest(manifest_path, manifest)
        reporter.add("ownership manifest", STATE_OK, str(manifest_path))
    else:
        reporter.add("ownership manifest", STATE_OK if manifest_path.exists() else STATE_MISSING, str(manifest_path))

    reporter.render()
    return 2 if reporter.has_conflict else 0


if __name__ == "__main__":
    raise SystemExit(main())
