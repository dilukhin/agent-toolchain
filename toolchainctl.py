#!/usr/bin/env python3
"""Installed command for managing the agent-toolchain desired state."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import setup_core
from setup_lib import (
    Reporter,
    STATE_CONFIGURED,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_INFO,
    STATE_OUTDATED,
    atomic_write,
    backup_file,
    parse_jsonc_object,
    sha256_bytes,
)
from setup_managed_tools import reconcile_tool_specs
from setup_manifest import MANIFEST_SCHEMA, load_manifest, save_manifest
from setup_path import reconcile_public_bin_path
from setup_tool_skills import reconcile_pinned_tool_skills
from setup_tools import parse_tool_specs
from setup_external_updates import cache_path, load_cache, refresh
from setup_inventory import common_external_cli_inventory

PRODUCT = "agent-toolchain"
LEGACY_PRODUCT = "opencode_setup"
UPDATE_REPOSITORY = "dilukhin/agent-toolchain"
UPDATE_BRANCH = "main"
_GITHUB_API_BRANCH = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/branches/{UPDATE_BRANCH}"
_GITHUB_ARCHIVE_PREFIX = f"https://codeload.github.com/{UPDATE_REPOSITORY}/zip/"
_UPDATE_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_UPDATE_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
_UPDATE_MAX_MEMBERS = 10000
_CORE_REQUIRED_FILES = (
    "toolchainctl.py",
    "setup_core.py",
    "setup_core_adapter.py",
    "setup_lib.py",
    "setup_manifest.py",
    "setup_migration.py",
    "setup_runtime.py",
    "setup_runtime_legacy.py",
    "setup_managed_tools.py",
    "setup_tool_skills.py",
    "setup_tool_skills_impl.py",
    "setup_path.py",
    "setup_inventory.py",
    "setup_external_updates.py",
    "setup_tools.py",
    "proxy_tools.py",
    "config_data.json",
)
_CORE_REQUIRED_TREES = ("templates", "skills/remote-long-running")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class StateMigrationError(RuntimeError):
    pass


class SelfUpdateError(RuntimeError):
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


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _validate_owned_state(path: Path, label: str) -> None:
    if path.is_symlink():
        raise StateMigrationError(f"{label} state path is a symlink; refusing to adopt ambiguous ownership: {path}")
    if not path.is_dir():
        raise StateMigrationError(f"{label} state path exists but is not a directory: {path}")
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise StateMigrationError(
            f"{label} state exists but has no ownership manifest: {manifest_path}; refusing to adopt unknown state"
        )
    _manifest, error, _pending = load_manifest(manifest_path)
    if error:
        raise StateMigrationError(f"{label} ownership manifest is not valid: {error}")


def _validate_legacy_state(path: Path) -> None:
    _validate_owned_state(path, "legacy")


def _validate_copied_state(path: Path) -> None:
    _validate_owned_state(path, "copied")


def prepare_state(*, check: bool) -> tuple[Path, str | None, str | None]:
    """Select current state and perform the one-way legacy import only on apply."""
    new = default_state_dir()
    legacy = legacy_state_dir()
    new_present = _path_present(new)
    legacy_present = _path_present(legacy)

    if new_present:
        _validate_owned_state(new, "agent-toolchain")
        if legacy_present:
            return new, "info", f"legacy state retained as inactive backup: {legacy}"
        return new, None, None
    if not legacy_present:
        return new, None, None

    _validate_legacy_state(legacy)
    if check:
        return legacy, "outdated", f"legacy state detected; toolchainctl apply will import it once into {new}"

    new.parent.mkdir(parents=True, exist_ok=True)
    temporary = new.parent / f".{new.name}.migration-{os.getpid()}-{uuid.uuid4().hex}"
    if _path_present(temporary):
        raise StateMigrationError(f"temporary migration path already exists: {temporary}")
    try:
        shutil.copytree(legacy, temporary, symlinks=True)
        _validate_copied_state(temporary)
        if _path_present(new):
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
    updates = sub.add_parser("updates", help="read-only external CLI update advisories")
    update_sub = updates.add_subparsers(dest="updates_command", required=True)
    update_sub.add_parser("refresh", help="query providers and atomically refresh advisory cache")
    update_sub.add_parser("show", help="show cached advisories without network access")
    update = sub.add_parser("update", help="update the installed agent-toolchain core from GitHub main")
    update.add_argument("--apply", action="store_true", help="run the freshly installed toolchainctl apply after update")
    return parser


def _updates_phase(args: argparse.Namespace) -> int:
    if args.updates_command == "refresh":
        try:
            data = refresh()
        except Exception as exc:
            print(f"update advisory refresh failed: {exc}", file=sys.stderr)
            return 0
    else:
        data = load_cache()
    print(f"cache: {cache_path()}")
    inventories = common_external_cli_inventory()
    for name in ("opencode", "codex"):
        record = data.get("tools", {}).get(name, {})
        inventory = inventories[name]
        print(f"{name}: provider={record.get('provider', inventory.active.provider if inventory.active else 'unknown')} "
              f"installed={record.get('installed_version', inventory.active.version if inventory.active else 'unknown')} "
              f"latest={record.get('latest_version', 'unknown')} status={record.get('status', 'missing')}")
    return 0


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


def _managed_model_aliases(repo_root: Path, config: dict) -> dict[str, set[str]]:
    policy_path = repo_root / "templates" / "routerai_model_policy.json"
    catalog_path = repo_root / "templates" / "routerai_catalog.generated.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    policy_models = policy.get("models")
    managed_names = catalog.get("managed_names", {})
    if not isinstance(policy_models, dict) or not isinstance(managed_names, dict):
        raise ValueError("RouterAI policy/catalog schema is invalid")
    aliases: dict[str, set[str]] = {}
    config_models = config.get("models", {})
    for model_id, spec in policy_models.items():
        if not isinstance(spec, dict):
            raise ValueError(f"RouterAI policy model is not an object: {model_id}")
        names: set[str] = set()
        legacy = spec.get("legacy_names", [])
        if isinstance(legacy, list):
            names.update(x for x in legacy if isinstance(x, str) and x)
        generated = managed_names.get(model_id, [])
        if isinstance(generated, list):
            names.update(x for x in generated if isinstance(x, str) and x)
        current = config_models.get(model_id) if isinstance(config_models, dict) else None
        if isinstance(current, dict) and isinstance(current.get("name"), str):
            names.add(current["name"])
        aliases[model_id] = names
    return aliases


def _reconcile_routerai_model_labels(state_dir: Path, *, check: bool) -> int:
    """Update only known managed RouterAI display names, preserving custom names and fields."""
    repo_root = Path(__file__).resolve().parent
    config_path = _default_paths()["config"] / "opencode.jsonc"
    if not config_path.is_file():
        return 0

    reporter = Reporter()
    manifest_path = state_dir / "manifest.json"
    manifest, error, _migration_pending = load_manifest(manifest_path)
    if error:
        reporter.add("RouterAI model labels", STATE_CONFLICT, f"cannot load ownership manifest: {error}")
        reporter.render()
        return 2
    previous = manifest.get("managed_files", {}).get("OpenCode config")
    if not isinstance(previous, dict):
        return 0

    try:
        current_data = config_path.read_bytes()
        if previous.get("path") != str(config_path):
            return 0
        if previous.get("sha256") != sha256_bytes(current_data):
            return 0
        existing, parse_error, has_jsonc_features = parse_jsonc_object(current_data)
        desired_config = json.loads((repo_root / "config_data.json").read_text(encoding="utf-8"))
        aliases = _managed_model_aliases(repo_root, desired_config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        reporter.add("RouterAI model labels", STATE_CONFLICT, f"cannot load managed model policy: {exc}")
        reporter.render()
        return 2
    if parse_error or existing is None:
        return 0

    providers = existing.get("provider")
    if not isinstance(providers, dict):
        return 0
    routerai = providers.get("routerai")
    if not isinstance(routerai, dict):
        return 0
    models = routerai.get("models")
    if not isinstance(models, dict):
        return 0
    target_models = desired_config.get("models")
    if not isinstance(target_models, dict):
        reporter.add("RouterAI model labels", STATE_CONFLICT, "config_data.json models is not an object")
        reporter.render()
        return 2

    changed_ids: list[str] = []
    custom_ids: list[str] = []
    for model_id, target_spec in target_models.items():
        current_spec = models.get(model_id)
        if not isinstance(current_spec, dict) or not isinstance(target_spec, dict):
            continue
        current_name = current_spec.get("name")
        target_name = target_spec.get("name")
        if not isinstance(current_name, str) or not isinstance(target_name, str) or current_name == target_name:
            continue
        if current_name not in aliases.get(model_id, set()):
            custom_ids.append(model_id)
            continue
        current_spec["name"] = target_name
        changed_ids.append(model_id)

    if not changed_ids:
        if custom_ids:
            reporter.add(
                "RouterAI model labels",
                STATE_INFO,
                f"custom labels preserved for {len(custom_ids)} curated model(s)",
            )
            reporter.render()
        return 0

    if has_jsonc_features:
        reporter.add(
            "RouterAI model labels",
            STATE_CONFLICT,
            "managed OpenCode JSONC needs model-label changes but contains comments/trailing commas; formatting preserved",
        )
        reporter.render()
        return 2

    if check:
        reporter.add(
            "RouterAI model labels",
            STATE_OUTDATED,
            f"{len(changed_ids)} managed label(s) differ; ordinary apply will update only recognized managed names",
        )
        reporter.render()
        return 0

    backup = backup_file(config_path, state_dir, "OpenCode config")
    updated = (json.dumps(existing, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        atomic_write(config_path, updated)
        previous = dict(previous)
        previous["sha256"] = sha256_bytes(updated)
        manifest["managed_files"]["OpenCode config"] = previous
        save_manifest(manifest_path, manifest)
    except (OSError, ValueError) as exc:
        reporter.add("RouterAI model labels", STATE_FAILED, f"cannot persist managed labels: {exc}")
        reporter.render()
        return 2

    detail = f"updated {len(changed_ids)} managed label(s); custom names preserved; backup: {backup}"
    if custom_ids:
        detail += f"; custom labels preserved for {len(custom_ids)} curated model(s)"
    reporter.add("RouterAI model labels", STATE_CONFIGURED, detail)
    reporter.render()
    return 0


def _urlopen_bytes(url: str, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-toolchain-self-update/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_bytes:
                        raise SelfUpdateError(f"remote payload is too large: {length} bytes")
                except ValueError:
                    pass
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SelfUpdateError(f"download failed for {url}: {exc}") from exc
    if len(data) > max_bytes:
        raise SelfUpdateError(f"remote payload exceeds safety limit: {max_bytes} bytes")
    return data


def _resolve_update_sha() -> str:
    try:
        payload = json.loads(_urlopen_bytes(_GITHUB_API_BRANCH, max_bytes=1024 * 1024).decode("utf-8"))
        sha = payload["commit"]["sha"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SelfUpdateError(f"cannot resolve {UPDATE_REPOSITORY} {UPDATE_BRANCH}: {exc}") from exc
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise SelfUpdateError(f"GitHub returned an invalid commit SHA: {sha!r}")
    return sha


def _extract_update_archive(data: bytes, destination: Path) -> Path:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SelfUpdateError(f"downloaded GitHub archive is not a valid ZIP: {exc}") from exc
    roots: set[str] = set()
    extracted_bytes = 0
    destination = destination.resolve()
    with archive:
        members = archive.infolist()
        if len(members) > _UPDATE_MAX_MEMBERS:
            raise SelfUpdateError(f"GitHub archive contains too many members: {len(members)}")
        declared_bytes = sum(info.file_size for info in members if not info.is_dir())
        if declared_bytes > _UPDATE_MAX_EXTRACTED_BYTES:
            raise SelfUpdateError(f"GitHub archive expands beyond safety limit: {declared_bytes} bytes")
        for info in members:
            if "\\" in info.filename:
                raise SelfUpdateError(f"unsafe archive member uses backslash separators: {info.filename!r}")
            posix = PurePosixPath(info.filename)
            if not posix.parts or posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
                raise SelfUpdateError(f"unsafe archive member: {info.filename!r}")
            roots.add(posix.parts[0])
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise SelfUpdateError(f"symlink is not allowed in update archive: {info.filename}")
            target = destination.joinpath(*posix.parts).resolve(strict=False)
            if not target.is_relative_to(destination):
                raise SelfUpdateError(f"archive member escapes extraction root: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    extracted_bytes += len(chunk)
                    if extracted_bytes > _UPDATE_MAX_EXTRACTED_BYTES:
                        raise SelfUpdateError(
                            f"GitHub archive extracted data exceeds safety limit: {_UPDATE_MAX_EXTRACTED_BYTES} bytes"
                        )
                    sink.write(chunk)
    if len(roots) != 1:
        raise SelfUpdateError(f"GitHub archive must contain exactly one root directory, got {sorted(roots)!r}")
    root = destination / next(iter(roots))
    if not (root / "bootstrap_core.py").is_file():
        raise SelfUpdateError("GitHub archive does not contain bootstrap_core.py")
    return root


def _installed_core_fingerprint(core: Path) -> str:
    digest = hashlib.sha256()
    try:
        for relative in _CORE_REQUIRED_FILES:
            path = core / relative
            if not path.is_file():
                raise SelfUpdateError(f"installed core payload is incomplete: {path}")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        for tree in _CORE_REQUIRED_TREES:
            base = core / tree
            if not base.is_dir():
                raise SelfUpdateError(f"installed core payload directory is missing: {base}")
            for path in sorted(base.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    relative = path.relative_to(core).as_posix()
                    digest.update(relative.encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(path.read_bytes())
                    digest.update(b"\0")
    except OSError as exc:
        raise SelfUpdateError(f"cannot validate installed core payload: {exc}") from exc
    return digest.hexdigest()


def _installed_core_fingerprint_from_payload(core: Path, payload: object) -> str:
    if not isinstance(payload, list) or not payload:
        raise SelfUpdateError("installed core ownership payload is invalid")
    digest = hashlib.sha256()
    seen: set[str] = set()
    try:
        for item in payload:
            if not isinstance(item, dict):
                raise SelfUpdateError("installed core ownership payload entry is invalid")
            relative = item.get("path")
            expected = item.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise SelfUpdateError("installed core ownership payload entry is invalid")
            if "\\" in relative or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                raise SelfUpdateError(f"installed core ownership payload entry is invalid: {relative!r}")
            posix = PurePosixPath(relative)
            if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
                raise SelfUpdateError(f"installed core ownership payload path is unsafe: {relative!r}")
            normalized = posix.as_posix()
            if normalized in seen:
                raise SelfUpdateError(f"installed core ownership payload path is duplicated: {normalized}")
            seen.add(normalized)
            path = core.joinpath(*posix.parts)
            if path.is_symlink() or not path.is_file():
                raise SelfUpdateError(f"installed core payload is incomplete: {path}")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise SelfUpdateError(f"installed core managed file was modified: {path}")
            digest.update(normalized.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
    except OSError as exc:
        raise SelfUpdateError(f"cannot validate installed core payload: {exc}") from exc
    return digest.hexdigest()


def _owned_installed_core() -> dict[str, object]:
    core = Path(__file__).resolve().parent
    if core.is_symlink():
        raise SelfUpdateError(f"installed core path is a symlink; refusing self-update: {core}")
    marker_path = core / ".agent-toolchain-managed-core.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfUpdateError(
            "toolchainctl update is supported only from an installed managed core; run bootstrap first"
        ) from exc
    fingerprint = marker.get("fingerprint")
    if marker.get("schema") != 1 or marker.get("owner") != PRODUCT or not isinstance(fingerprint, str):
        raise SelfUpdateError(f"installed core ownership marker is invalid: {marker_path}")
    payload = marker.get("payload")
    actual = (
        _installed_core_fingerprint_from_payload(core, payload)
        if payload is not None
        else _installed_core_fingerprint(core)
    )
    if actual != fingerprint:
        raise SelfUpdateError(
            "installed core payload differs from its ownership fingerprint; local changes are preserved and update is blocked"
        )
    return marker


def _run_self_update(*, apply_after: bool) -> int:
    try:
        before = _owned_installed_core()
        sha = _resolve_update_sha()
        archive_url = _GITHUB_ARCHIVE_PREFIX + sha
        archive_data = _urlopen_bytes(archive_url, max_bytes=_UPDATE_MAX_ARCHIVE_BYTES)
        with tempfile.TemporaryDirectory(prefix="agent-toolchain-update-") as temporary:
            source_root = _extract_update_archive(archive_data, Path(temporary))
            env = dict(os.environ)
            env["AGENT_TOOLCHAIN_UPDATE_REF"] = sha
            completed = subprocess.run(
                [sys.executable, "-B", str(source_root / "bootstrap_core.py")],
                env=env,
                check=False,
            )
            if completed.returncode != 0:
                raise SelfUpdateError(f"bootstrap of GitHub ref {sha} failed with exit code {completed.returncode}")
        after = _owned_installed_core()
    except SelfUpdateError as exc:
        print(f"modified/conflict  agent-toolchain update  {exc}", file=sys.stderr)
        return 2

    state = "up-to-date" if before.get("fingerprint") == after.get("fingerprint") else "configured"
    print(f"{state:<18}agent-toolchain update  {UPDATE_REPOSITORY}@{sha}")
    if not apply_after:
        return 0

    installed_tool = Path(__file__).resolve().parent / "toolchainctl.py"
    completed = subprocess.run([sys.executable, "-B", str(installed_tool), "apply"], check=False)
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "updates":
        return _updates_phase(args)
    if args.command == "update":
        return _run_self_update(apply_after=bool(args.apply))

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

    labels_rc = _reconcile_routerai_model_labels(state_dir, check=check)
    if labels_rc != 0 and not check:
        return labels_rc

    previous = os.environ.get("AGENT_TOOLCHAIN_RUNTIME_PRECONCILED")
    os.environ["AGENT_TOOLCHAIN_RUNTIME_PRECONCILED"] = "1"
    try:
        core_rc = int(setup_core.main(_core_argv(args, state_dir)))
    finally:
        if previous is None:
            os.environ.pop("AGENT_TOOLCHAIN_RUNTIME_PRECONCILED", None)
        else:
            os.environ["AGENT_TOOLCHAIN_RUNTIME_PRECONCILED"] = previous
    return 2 if managed_rc != 0 or labels_rc != 0 or core_rc != 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
