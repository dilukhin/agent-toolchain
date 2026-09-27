"""Explicit project-scoped P0 activation after an exact OpenCode preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import setup_opencode_permissions_pilot as pilot
import setup_opencode_permissions_pilot_control as control
import setup_opencode_permissions_pilot_source as source


class EnableConflict(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EnableConflict(code)


def _resolved_config(executable: str, workspace: Path) -> dict[str, Any]:
    # Config can contain credentials. Keep stdout/stderr private and never
    # include it in an exception or a CLI diagnostic.
    try:
        try:
            run = subprocess.run(
                [executable, "debug", "config"], cwd=workspace, text=True,
                capture_output=True, timeout=10, check=False,
            )
            _require(run.returncode == 0, "P0_EFFECTIVE_CONFIG_UNAVAILABLE")
            raw = run.stdout
        except subprocess.TimeoutExpired as exc:
            # OpenCode can print the resolved config and then retain a detached
            # dependency install. subprocess.run has killed that process here.
            # A complete JSON document is sufficient; partial output fails.
            raw = (exc.stdout or b"").decode("utf-8", "strict")
        _require(len(raw) < 1024 * 1024, "P0_EFFECTIVE_CONFIG_UNAVAILABLE")
        value = json.loads(raw)
        _require(isinstance(value, dict), "P0_EFFECTIVE_CONFIG_INVALID")
        return value
    except (OSError, ValueError) as exc:
        if isinstance(exc, EnableConflict):
            raise
        raise EnableConflict("P0_EFFECTIVE_CONFIG_UNAVAILABLE") from exc


def _preflight_environment() -> None:
    blocked = (
        "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_PERMISSION", "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_PURE", "OPENCODE_FAKE_VCS", "OPENCODE_TEST_HOME",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    )
    _require(not any(os.environ.get(name) for name in blocked),
             "P0_CUSTOM_CONFIG_ENVIRONMENT")


def _version(executable: str) -> str:
    try:
        result = subprocess.run([executable, "--version"], text=True, capture_output=True,
                                timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnableConflict("P0_OPENCODE_VERSION_UNAVAILABLE") from exc
    _require(result.returncode == 0, "P0_OPENCODE_VERSION_UNAVAILABLE")
    return result.stdout.strip()


def _no_competing_effective_layer(config: dict[str, Any]) -> None:
    # The installer owns the entire project permission tree. A pre-existing
    # effective tree, agent override or plugin could alter its meaning.
    _require(not config.get("permission"), "P0_COMPETING_PERMISSION_LAYER")
    agents = config.get("agent") or {}
    _require(isinstance(agents, dict), "P0_AGENT_CONFIG_INVALID")
    _require(not any(isinstance(value, dict) and value.get("permission")
                     for value in agents.values()), "P0_COMPETING_AGENT_PERMISSION")
    _require(not config.get("plugin"), "P0_COMPETING_PLUGIN")
    _require(not config.get("tools"), "P0_LEGACY_TOOLS_LAYER")


def enable(*, workspace: Path, executable: str | None = None) -> dict[str, Any]:
    _preflight_environment()
    config_dir, data_dir, state_dir, cache_root = control.canonical_paths(workspace)
    workspace = config_dir.parent
    _require(not (config_dir / "plugins").is_symlink(), "P0_WORKSPACE_PLUGIN_CONFLICT")
    _require(not (config_dir / "opencode.json").exists(), "P0_WORKSPACE_CONFIG_CONFLICT")
    _require(not (config_dir / "opencode.jsonc").is_symlink(), "P0_WORKSPACE_CONFIG_CONFLICT")
    # One owned pilot at a time. Do not mistakenly interpret a global or other
    # project deployment as the currently selected workspace.
    observed = control.status(config_dir=config_dir, data_dir=data_dir,
                              state_dir=state_dir, cache_root=cache_root)
    if observed["status"] == "active":
        _require(observed["artifact_id"] == source.CURRENT.pilot_id,
                 "P0_ACTIVE_ARTIFACT_MISMATCH")
        return {**observed, "changed": False}
    _require(observed["status"] == "disabled", "P0_PREPARED_RECOVERY_REQUIRED")

    binary = executable or shutil.which("opencode")
    _require(bool(binary) and Path(binary).is_absolute(), "P0_OPENCODE_MISSING")
    _require(_version(str(binary)) == source.CURRENT.version,
             "P0_INSTALLED_VERSION_MISMATCH")

    before = _resolved_config(str(binary), workspace)
    _no_competing_effective_layer(before)
    bundle, native = source.materialize_source(
        cache_root=cache_root, installed_version=source.CURRENT.version,
    )
    validation = pilot.validate_artifacts(
        pilot_bundle_dir=bundle, native_artifact_dir=native,
        installed_version=source.CURRENT.version, installed_platform="linux",
    )
    _require(validation["pilot_artifact_id"] == source.CURRENT.pilot_id
             and validation["native_artifact_id"] == source.CURRENT.native_id,
             "P0_SOURCE_IDENTITY_MISMATCH")
    result = pilot.apply_pilot(
        pilot_bundle_dir=bundle, native_artifact_dir=native,
        installed_version=source.CURRENT.version, config_dir=config_dir,
        data_dir=data_dir, state_dir=state_dir, ensure_schema=True,
    )
    try:
        observed = pilot.inspect_pilot(
            pilot_bundle_dir=bundle, native_artifact_dir=native,
            installed_version=source.CURRENT.version, config_dir=config_dir,
            data_dir=data_dir, state_dir=state_dir,
        )
        _require(observed.get("status") == "active"
                 and observed.get("effective_readback") == "PASS"
                 and observed.get("artifact_id") == source.CURRENT.pilot_id,
                 "P0_OWNED_READBACK_CONFLICT")
        return {**result, "workspace": str(workspace)}
    except (EnableConflict, pilot.PilotDeploymentError, OSError):
        # The reconciler checks exact ownership before removing anything.
        pilot.disable_pilot(
            pilot_bundle_dir=bundle, native_artifact_dir=native,
            installed_version=source.CURRENT.version, config_dir=config_dir,
            data_dir=data_dir, state_dir=state_dir,
        )
        raise
