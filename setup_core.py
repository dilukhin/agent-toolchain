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
    inspect_repo,
    parse_jsonc_object,
    reconcile_agents_file,
    reconcile_file,
    reconcile_repo,
    resolve_credential_path,
    routerai_file_credential,
    routerai_provider,
    run,
    sha256_bytes,
    validate_skill,
)
from setup_manifest import MANIFEST_SCHEMA, load_manifest, save_manifest
from setup_migration import reconcile_opencode_config
from setup_runtime import ensure_agent_safe_runtime, ensure_ssh_relay_runtime, reconcile_npm
from setup_tools import parse_tool_specs

LEGACY_AGENTS = """# Global OpenCode instructions

- Never expose secrets, tokens, passwords, or API keys.
- Do not scan .git, node_modules, build output, caches, or logs without a reason.
- Prefer concise, structured answers.
"""

_AGENT_SAFE_LEGACY_EGG_INFO_PREFIX = "src/agent_safe.egg-info/"
_ROUTERAI_LEGACY_PLACEHOLDER = b"your-routerai-api-key-here\n"
_MANAGED_CREDENTIAL_MODES = frozenset({"managed-path", "legacy-managed-path"})


def render_config(template_path: Path, api_key_file: Path) -> bytes:
    template = template_path.read_text(encoding="utf-8")
    escaped = str(api_key_file).replace("\\", "\\\\")
    return template.replace("__ROUTERAI_API_KEY_FILE__", escaped).encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reconcile managed OpenCode environment components safely.")
    p.add_argument("--check", action="store_true", help="report only; do not change files or repositories")
    p.add_argument("--force", action="store_true", help="backup and replace locally modified managed content")
    p.add_argument("--repo-root", default=str(Path(__file__).resolve().parent))
    p.add_argument("--config-dir", required=True)
    p.add_argument("--stash-dir", required=True,
                   help="legacy credential location for direct setup_core callers")
    p.add_argument("--credential-dir",
                   help="canonical credential directory for fresh installs; wrappers use <config-dir>/credentials")
    p.add_argument("--skills-dir", required=True)
    p.add_argument("--state-dir", required=True)
    p.add_argument("--projects-dir", required=True)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--skip-package-install", action="store_true")
    p.add_argument("--skip-dependency-install", action="store_true")
    p.add_argument("--ssh-relay-url")
    p.add_argument("--agent-safe-url")
    return p


def _credential_from_manifest(manifest: dict, config_dir: Path) -> tuple[Path | None, str | None]:
    credentials = manifest.get("credentials")
    if not isinstance(credentials, dict):
        return None, None
    routerai = credentials.get("routerai")
    if not isinstance(routerai, dict):
        return None, None
    path = routerai.get("path")
    mode = routerai.get("mode")
    if not isinstance(path, str) or not path:
        return None, None
    return resolve_credential_path(path, config_dir), str(mode or "external-file")


def _record_credential(manifest: dict, path: Path, mode: str) -> bool:
    credentials = manifest.setdefault("credentials", {})
    desired = {"provider": "routerai", "mode": mode, "path": str(path)}
    if credentials.get("routerai") == desired:
        return False
    credentials["routerai"] = desired
    return True


def _has_nonfile_routerai_credential(config: dict | None) -> bool:
    """Return True when an existing RouterAI apiKey is present but is not {file:...}.

    Such values may be inline secrets or another credential mechanism.  The setup must
    never print, migrate, or replace them automatically with a placeholder file.
    """
    if config is None:
        return False
    routerai = routerai_provider(config)
    if routerai is None:
        return False
    options = routerai.get("options")
    if not isinstance(options, dict) or "apiKey" not in options:
        return False
    return routerai_file_credential(config) is None


def _managed_credential_is_legacy_placeholder(path: Path, mode: str | None) -> bool:
    """Recognize only the exact placeholder written by older opencode_setup versions.

    External credential files are never inspected. For setup-managed paths, read at
    most one byte beyond the known placeholder length so a real credential is not read
    in full merely to classify this legacy state.
    """
    if mode not in _MANAGED_CREDENTIAL_MODES or path.is_symlink():
        return False
    try:
        with path.open("rb") as stream:
            return stream.read(len(_ROUTERAI_LEGACY_PLACEHOLDER) + 1) == _ROUTERAI_LEGACY_PLACEHOLDER
    except OSError:
        return False


def _agent_safe_legacy_metadata_only(path: Path) -> bool:
    """Recognize only the setuptools metadata created by the legacy editable install.

    This is intentionally dependency-specific.  Any tracked change or any other
    untracked path keeps the normal strict repository conflict behavior.
    """
    if not (path / ".git").exists():
        return False
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    status = run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=path, env=env)
    if status.returncode != 0:
        return False
    saw_legacy_metadata = False
    for line in status.stdout.splitlines():
        if line.startswith("?? "):
            normalized = line[3:].replace("\\", "/")
            if normalized.startswith(_AGENT_SAFE_LEGACY_EGG_INFO_PREFIX):
                saw_legacy_metadata = True
                continue
        if line.strip():
            return False
    return saw_legacy_metadata


def _agent_safe_remote_differs(path: Path, branch: str) -> bool:
    local = run(["git", "rev-parse", "HEAD"], cwd=path)
    remote = run(["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"], cwd=path)
    if local.returncode != 0 or remote.returncode != 0 or not remote.stdout.strip():
        return False
    return local.stdout.strip() != remote.stdout.split()[0]


def reconcile_agent_safe_repo(*, component: str, path: Path, url: str, branch: str,
                              reporter: Reporter, check: bool) -> tuple[bool, str]:
    """Recover the one legacy self-conflict caused by editable-install egg-info.

    Older opencode_setup versions installed agent-safe editable before agent-safe ignored
    ``*.egg-info/``.  That left ``src/agent_safe.egg-info/`` untracked, which then blocked
    the normal safe updater.  If and only if this generated directory is the entire
    working-tree delta and upstream differs, allow a read-only ``outdated`` result or a
    single ff-only pull.  No files are deleted; normal strict reconciliation runs again
    immediately after the pull.
    """
    state, _ = inspect_repo(path, url, branch)
    legacy_recovery = (
        state == STATE_CONFLICT
        and _agent_safe_legacy_metadata_only(path)
        and _agent_safe_remote_differs(path, branch)
    )
    if not legacy_recovery:
        return reconcile_repo(
            component=component, path=path, url=url, branch=branch,
            reporter=reporter, check=check,
        )

    if check:
        reporter.add(
            component,
            STATE_OUTDATED,
            "legacy editable-install egg-info is the only local artifact; ff-only update can recover without deleting files",
        )
        return True, STATE_OUTDATED

    pull = run(["git", "pull", "--ff-only", "origin", branch], cwd=path)
    if pull.returncode != 0:
        reporter.add(
            component + " recovery",
            STATE_CONFLICT,
            "ff-only recovery update failed; repository and generated metadata preserved",
        )
        return False, STATE_CONFLICT

    reporter.add(
        component + " recovery",
        STATE_OK,
        "applied ff-only upstream update; legacy generated metadata was not deleted",
    )
    return reconcile_repo(
        component=component, path=path, url=url, branch=branch,
        reporter=reporter, check=False,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    config_dir = Path(args.config_dir).expanduser().resolve()
    stash_dir = Path(args.stash_dir).expanduser().resolve()
    credential_dir = (Path(args.credential_dir).expanduser().resolve()
                      if args.credential_dir else stash_dir)
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
    if env_cfg.get("manifest_schema") != MANIFEST_SCHEMA:
        reporter.add(
            "manifest schema policy",
            STATE_CONFLICT,
            f"config_data.json requires manifest schema {env_cfg.get('manifest_schema')!r}; runtime supports {MANIFEST_SCHEMA}",
        )
        reporter.render()
        return 2
    _tool_specs, tool_spec_error = parse_tool_specs(env_cfg)
    if tool_spec_error:
        reporter.add("ToolSpec registry", STATE_CONFLICT, tool_spec_error)
        reporter.render()
        return 2

    manifest, manifest_error, manifest_migration_pending = load_manifest(manifest_path)
    if manifest_error:
        reporter.add("ownership manifest", STATE_CONFLICT, manifest_error)
        reporter.render()
        return 2
    if manifest_migration_pending:
        if args.check:
            reporter.add(
                "ownership manifest schema",
                STATE_OUTDATED,
                "schema 1 распознана; обычный apply мигрирует manifest в schema 2 без удаления существующих metadata",
            )
        else:
            reporter.add(
                "ownership manifest schema",
                STATE_OK,
                "schema 1 мигрирована в памяти в schema 2; результат будет сохранён после reconciliation",
            )

    reconcile_npm(config_dir, config, reporter, args.check, args.skip_package_install)

    manifest_changed = manifest_migration_pending
    config_path = config_dir / "opencode.jsonc"
    existing_config = None
    config_parse_error = None
    existing_has_jsonc_features = False
    existing_file_ref = None
    existing_nonfile_credential = False
    if config_path.is_file():
        existing_config, config_parse_error, existing_has_jsonc_features = parse_jsonc_object(config_path.read_bytes())
        if existing_config is not None:
            existing_file_ref = routerai_file_credential(existing_config)
            existing_nonfile_credential = _has_nonfile_routerai_credential(existing_config)

    previous_config = manifest["managed_files"].get("OpenCode config")
    existing_provider = existing_config.get("provider") if isinstance(existing_config, dict) else None
    existing_routerai = routerai_provider(existing_config) if isinstance(existing_config, dict) else None
    existing_provider_present = isinstance(existing_config, dict) and "provider" in existing_config
    compatible_provider_shape = not existing_provider_present or isinstance(existing_provider, dict)
    compatible_existing = (
        existing_config is not None
        and compatible_provider_shape
        and (existing_routerai is not None or not existing_has_jsonc_features)
    )
    config_can_be_managed = (not config_path.exists()) or previous_config is not None or compatible_existing

    manifest_credential, manifest_mode = _credential_from_manifest(manifest, config_dir)
    api_key_file: Path | None = None
    credential_mode: str | None = None
    if existing_nonfile_credential:
        # Existing credential semantics are authoritative.  Do not inspect or migrate
        # the value and do not create a parallel placeholder.
        pass
    elif existing_file_ref:
        referenced = resolve_credential_path(existing_file_ref, config_dir)
        api_key_file = referenced
        if manifest_credential is not None and referenced == manifest_credential:
            credential_mode = manifest_mode or "external-file"
        else:
            credential_mode = "external-file"
    elif manifest_credential is not None:
        api_key_file = manifest_credential
        credential_mode = manifest_mode or "external-file"
    elif config_can_be_managed and config_parse_error is None:
        if args.credential_dir:
            api_key_file = credential_dir / "routerai-api-key.txt"
            credential_mode = "managed-path"
        else:
            # Compatibility for direct setup_core callers from the first reconciler generation.
            api_key_file = stash_dir / "api-key.txt"
            credential_mode = "legacy-managed-path"

    if api_key_file is None:
        if existing_nonfile_credential:
            detail = "existing RouterAI apiKey is not a {file:...} reference; preserved without reading or creating a placeholder"
        elif config_parse_error:
            detail = f"cannot determine credential because existing config is not safely mergeable: {config_parse_error}"
        elif existing_config is not None and existing_routerai is None and existing_has_jsonc_features:
            detail = "existing config has another provider but contains comments/trailing commas; no credential created until config can be migrated safely"
        else:
            detail = "existing config is not safely adoptable for RouterAI; no unused placeholder created"
        reporter.add("RouterAI credential", STATE_CONFLICT, detail)
    elif api_key_file.exists():
        if api_key_file.is_file():
            if _managed_credential_is_legacy_placeholder(api_key_file, credential_mode):
                reporter.add(
                    "RouterAI credential",
                    STATE_MISSING,
                    f"служебная заглушка предыдущей версии не является API key; запишите реальный ключ RouterAI: {api_key_file}",
                )
            else:
                mode_detail = "external file referenced by config" if credential_mode == "external-file" else "existing credential file"
                reporter.add("RouterAI credential", STATE_OK, f"{mode_detail}; bytes preserved: {api_key_file}")
                if not args.check and credential_mode != "external-file" and os.name != "nt":
                    os.chmod(api_key_file, 0o600)
        else:
            reporter.add("RouterAI credential", STATE_CONFLICT, f"path exists but is not a regular file: {api_key_file}")
    else:
        if credential_mode == "external-file":
            state = STATE_MISSING if args.check else STATE_CONFLICT
            reporter.add("RouterAI credential", state,
                         f"existing config references missing credential; no alternate placeholder created: {api_key_file}")
        else:
            reporter.add(
                "RouterAI credential",
                STATE_MISSING,
                f"ключ RouterAI не настроен; запишите реальный API key по canonical path: {api_key_file}",
            )

    if api_key_file is not None and not args.check:
        manifest_changed |= _record_credential(manifest, api_key_file, credential_mode or "external-file")

    if api_key_file is not None:
        config_data = render_config(repo_root / "templates" / "opencode.jsonc", api_key_file)
        manifest_changed |= reconcile_opencode_config(
            destination=config_path,
            desired_data=config_data,
            source_label="opencode_setup:managed-merge:templates/opencode.jsonc",
            manifest=manifest, reporter=reporter, check=args.check, force=args.force, state_dir=state_dir,
        )
    else:
        reporter.add("OpenCode config", STATE_CONFLICT, "preserved because RouterAI credential path is unresolved")

    agents_data = (repo_root / "templates" / "AGENTS.md").read_bytes()
    manifest_changed |= reconcile_agents_file(
        destination=config_dir / "AGENTS.md", template_data=agents_data,
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
        safe_usable, safe_repo_state = reconcile_agent_safe_repo(
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
