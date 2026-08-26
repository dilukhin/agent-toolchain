#!/usr/bin/env python3
"""Installed command for managing the agent-toolchain desired state."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import setup_core
from setup_lib import Reporter, STATE_CONFIGURED, STATE_CONFLICT, STATE_FAILED, STATE_OUTDATED
from setup_managed_tools import reconcile_tool_specs
from setup_manifest import MANIFEST_SCHEMA, load_manifest, save_manifest
from setup_path import reconcile_public_bin_path
from setup_tool_skills import reconcile_pinned_tool_skills
from setup_tools import parse_tool_specs

PRODUCT = "agent-toolchain"
LEGACY_PRODUCT = "opencode_setup"


class StateMigrationError(RuntimeError):
    pass


def _state_base() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local).resolve()
        return (Path.home() / ".local" / "state").resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve()
    return (Path.home() / ".local" / "state").resolve()


def default_state_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = _state_base()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return base / PRODUCT / "state"
    return base / PRODUCT


def legacy_state_dir() -> Path:
    override = os.environ.get("OPENCODE_SETUP_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = _state_base()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return base / LEGACY_PRODUCT / "state"
    return base / LEGACY_PRODUCT


def _validate_legacy_state(path: Path) -> None:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise StateMigrationError(
            f"legacy state exists but has no ownership manifest: {manifest_path}; refusing to adopt unknown state"
        )
    _manifest, error, _pending = load_manifest(manifest_path)
    if error:
        raise StateMigrationError(f"legacy ownership manifest is not valid: {error}")


def _validate_copied_state(path: Path) -> None:
    _manifest, error, _pending = load_manifest(path / "manifest.json")
    if error:
        raise StateMigrationError(f"copied ownership manifest failed validation: {error}")


def prepare_state(*, check: bool) -> tuple[Path, str | None, str | None]:
    """Select current state and perform the one-way legacy import only on apply."""
    new = default_state_dir()
    legacy = legacy_state_dir()
    if new.exists():
        if legacy.exists():
            return new, "info", f"legacy state retained as inactive backup: {legacy}"
        return new, None, None
    if not legacy.exists():
        return new, None, None

    _validate_legacy_state(legacy)
    if check:
        return legacy, "outdated", f"legacy state detected; toolchainctl apply will import it once into {new}"

    new.parent.mkdir(parents=True, exist_ok=True)
    temporary = new.parent / f".{new.name}.migration-{os.getpid()}-{uuid.uuid4().hex}"
    if temporary.exists():
        raise StateMigrationError(f"temporary migration path already exists: {temporary}")
    try:
        shutil.copytree(legacy, temporary, symlinks=True)
        _validate_copied_state(temporary)
        if new.exists():
            raise StateMigrationError(f"new state path appeared concurrently; refusing to replace it: {new}")
        os.replace(temporary, new)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    return new, "configured", f"legacy state imported to {new}; original retained unchanged at {legacy}"


def _default_paths() -> dict[str, Path]:
    home = Path.home()
    config_dir = Path(os.environ.get("OPENCODE_CONFIG_DIR", str(home / ".config" / "opencode"))).expanduser().resolve()
    credential_dir = Path(os.environ.get("OPENCODE_CREDENTIAL_DIR", str(config_dir / "credentials"))).expanduser().resolve()
    stash_dir = Path(os.environ.get("OPENCODE_STASH_DIR", str(home / "projects" / "stash" / "opencode.ai"))).expanduser().resolve()
    skills_dir = Path(os.environ.get("OPENCODE_SKILLS_DIR", str(home / ".agents" / "skills"))).expanduser().resolve()
    projects_dir = Path(os.environ.get("OPENCODE_PROJECTS_DIR", str(home / "projects"))).expanduser().resolve()
    return {
        "config": config_dir,
        "credential": credential_dir,
        "stash": stash_dir,
        "skills": skills_dir,
        "projects": projects_dir,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolchainctl", description="Manage the installed agent toolchain safely.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "apply"):
        cmd = sub.add_parser(name, help="read-only state check" if name == "check" else "apply desired state")
        cmd.add_argument("--force", action="store_true", help="replace only already-owned modified content with backup")
        cmd.add_argument("--skip-package-install", action="store_true", help=argparse.SUPPRESS)
        cmd.add_argument("--skip-dependency-install", action="store_true", help=argparse.SUPPRESS)
        cmd.add_argument("--ssh-relay-url", help=argparse.SUPPRESS)
        cmd.add_argument("--agent-safe-url", help=argparse.SUPPRESS)
    return parser


def _core_argv(args: argparse.Namespace, state_dir: Path) -> list[str]:
    paths = _default_paths()
    argv = [
        "--repo-root", str(Path(__file__).resolve().parent),
        "--config-dir", str(paths["config"]),
        "--stash-dir", str(paths["stash"]),
        "--credential-dir", str(paths["credential"]),
        "--skills-dir", str(paths["skills"]),
        "--state-dir", str(state_dir),
        "--projects-dir", str(paths["projects"]),
        "--python", sys.executable,
        "--skip-dependency-install",
    ]
    if args.command == "check":
        argv.append("--check")
    if args.force:
        argv.append("--force")
    if args.skip_package_install:
        argv.append("--skip-package-install")
    if args.ssh_relay_url:
        argv.extend(["--ssh-relay-url", args.ssh_relay_url])
    if args.agent_safe_url:
        argv.extend(["--agent-safe-url", args.agent_safe_url])
    return argv


def _managed_phase(state_dir: Path, *, check: bool, skip_install: bool, force: bool) -> int:
    reporter = Reporter()
    repo_root = Path(__file__).resolve().parent
    try:
        config = json.loads((repo_root / "config_data.json").read_text(encoding="utf-8"))
        env_cfg = config["managed_environment"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        reporter.add("ToolSpec registry", STATE_CONFLICT, f"cannot load managed_environment config: {exc}")
        reporter.render()
        return 2
    if env_cfg.get("manifest_schema") != MANIFEST_SCHEMA:
        reporter.add(
            "manifest schema policy",
            STATE_CONFLICT,
            f"config requires manifest schema {env_cfg.get('manifest_schema')!r}; runtime supports {MANIFEST_SCHEMA}",
        )
        reporter.render()
        return 2
    specs, error = parse_tool_specs(env_cfg)
    if error:
        reporter.add("ToolSpec registry", STATE_CONFLICT, error)
        reporter.render()
        return 2
    if not specs:
        reporter.add("ToolSpec registry", STATE_CONFLICT, "no managed production tools are declared")
        reporter.render()
        return 2

    manifest_path = state_dir / "manifest.json"
    manifest, error, migration_pending = load_manifest(manifest_path)
    if error:
        reporter.add("ownership manifest", STATE_CONFLICT, error)
        reporter.render()
        return 2
    changed = migration_pending
    if migration_pending:
        reporter.add(
            "ownership manifest schema",
            STATE_OUTDATED if check else STATE_CONFIGURED,
            "legacy schema is valid; apply persists schema 2 before recording managed tools",
        )

    changed |= reconcile_public_bin_path(manifest, reporter, check=check)
    changed |= reconcile_tool_specs(
        specs,
        sys.executable,
        reporter,
        check=check,
        skip_install=skip_install,
        manifest=manifest,
    )
    paths = _default_paths()
    changed |= reconcile_pinned_tool_skills(
        env_cfg,
        specs,
        manifest,
        reporter,
        skills_dir=paths["skills"],
        state_dir=state_dir,
        check=check,
        force=force,
        skip_install=skip_install,
    )
    if not check and changed:
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            save_manifest(manifest_path, manifest)
        except (OSError, ValueError) as exc:
            reporter.add("ownership manifest", STATE_FAILED, f"cannot save {manifest_path}: {exc}")
        else:
            reporter.add("ownership manifest", STATE_CONFIGURED, f"managed tool ownership recorded: {manifest_path}")

    reporter.render()
    return 2 if reporter.has_conflict else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    check = args.command == "check"
    try:
        state_dir, migration_state, migration_detail = prepare_state(check=check)
    except StateMigrationError as exc:
        print(f"modified/conflict  agent-toolchain state migration  {exc}", file=sys.stderr)
        return 2

    if migration_detail and migration_state:
        print(f"{migration_state:<18}agent-toolchain state migration  {migration_detail}")

    managed_rc = _managed_phase(state_dir, check=check, skip_install=bool(args.skip_dependency_install), force=bool(args.force))
    if managed_rc != 0 and not check:
        return managed_rc

    previous = os.environ.get("AGENT_TOOLCHAIN_RUNTIME_PRECONCILED")
    os.environ["AGENT_TOOLCHAIN_RUNTIME_PRECONCILED"] = "1"
    try:
        core_rc = int(setup_core.main(_core_argv(args, state_dir)))
    finally:
        if previous is None:
            os.environ.pop("AGENT_TOOLCHAIN_RUNTIME_PRECONCILED", None)
        else:
            os.environ["AGENT_TOOLCHAIN_RUNTIME_PRECONCILED"] = previous
    return 2 if managed_rc != 0 or core_rc != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
