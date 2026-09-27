"""Read-only P0 status and owner-aware rollback for an explicit MP-3 pilot.

This module never enables P0. The recorded source must remain available in the
local verified cache so disabling also works after an OpenCode version change.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import setup_opencode_permissions_pilot as pilot
import setup_opencode_permissions_pilot_metrics as metrics
from toolchain_state import canonical_state_dir

CACHE_NAME = "opencode-permissions-p0-source"
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class ControlConflict(ValueError):
    pass


def canonical_paths() -> tuple[Path, Path, Path, Path]:
    # The first MP-3 CLI profile covers only default OpenCode config/data
    # locations. Do not report a false "disabled" state when OpenCode or the
    # toolchain is redirected through environment-provided locations.
    _require(not any(os.environ.get(name) for name in
                     ("OPENCODE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME")),
             "P0_CUSTOM_PATH_UNSUPPORTED")
    home = Path.home()
    state = canonical_state_dir()
    return (home / ".config" / "opencode", home / ".local" / "share" / "opencode",
            state, state / CACHE_NAME)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ControlConflict(code)


def _cache_pair(cache_root: Path, artifact: str, native: str, version: str) -> tuple[Path, Path]:
    _require(cache_root.is_dir() and not cache_root.is_symlink(), "P0_SOURCE_CACHE_MISSING")
    segment = "sha256-" + artifact.split(":", 1)[1]
    native_segment = "sha256-" + native.split(":", 1)[1]
    matches: list[tuple[Path, Path]] = []
    for commit in cache_root.iterdir():
        # A foreign cache entry is left untouched. Only content with the exact
        # recorded identity may be considered as rollback evidence.
        if not _COMMIT.fullmatch(commit.name):
            continue
        _require(commit.is_dir() and not commit.is_symlink(), "P0_SOURCE_CACHE_CONFLICT")
        bundle = commit / segment
        if not bundle.exists() and not bundle.is_symlink():
            continue
        native_dir = commit / native_segment
        try:
            validation = pilot.validate_artifacts(
                pilot_bundle_dir=bundle, native_artifact_dir=native_dir,
                installed_version=version, installed_platform="linux",
            )
        except pilot.PilotDeploymentError as exc:
            raise ControlConflict("P0_SOURCE_CACHE_INVALID") from exc
        _require(validation["pilot_artifact_id"] == artifact
                 and validation["native_artifact_id"] == native,
                 "P0_SOURCE_IDENTITY_MISMATCH")
        matches.append((bundle, native_dir))
    _require(len(matches) == 1, "P0_SOURCE_CACHE_MISSING_OR_AMBIGUOUS")
    return matches[0]


def _snapshot(*, config_dir: Path, data_dir: Path, state_dir: Path,
              cache_root: Path) -> tuple[dict[str, object], tuple[Path, Path] | None]:
    _require(not state_dir.is_symlink() and not config_dir.is_symlink()
             and not data_dir.is_symlink() and not cache_root.is_symlink(),
             "P0_PATH_CONFLICT")
    try:
        owned = metrics.active_artifact(state_dir)
    except metrics.MetricsConflict as exc:
        raise ControlConflict("P0_STATE_INVALID") from exc
    if owned is None:
        _require(not (config_dir / "plugins").is_symlink(), "P0_PATH_CONFLICT")
        plugin_path = config_dir / "plugins" / pilot.PLUGIN_NAME
        _require(not plugin_path.exists() and not plugin_path.is_symlink(),
                 "P0_UNKNOWN_PLUGIN_WITHOUT_STATE")
        return {"status": "disabled"}, None

    artifact, version, native = owned
    bundle, native_dir = _cache_pair(cache_root, artifact, native, version)
    try:
        observed = pilot.inspect_pilot(
            pilot_bundle_dir=bundle, native_artifact_dir=native_dir,
            installed_version=version, config_dir=config_dir,
            data_dir=data_dir, state_dir=state_dir,
        )
    except pilot.PilotDeploymentError as exc:
        raise ControlConflict("P0_OWNERSHIP_CONFLICT") from exc
    _require(observed.get("status") in {"active", "prepared"}
             and observed.get("artifact_id") == artifact,
             "P0_STATUS_CONFLICT")
    if observed["status"] == "prepared":
        binding = observed.get("binding")
        _require(isinstance(binding, dict)
                 and (binding.get("config_before") or binding.get("config_after")),
                 "P0_PREPARED_CONFIG_CONFLICT")
        plugin_path = config_dir / "plugins" / pilot.PLUGIN_NAME
        _require(not plugin_path.exists() and not plugin_path.is_symlink()
                 or binding.get("plugin") is True,
                 "P0_PREPARED_PLUGIN_CONFLICT")
    return {"status": observed["status"], "artifact_id": artifact,
            "native_artifact_id": native, "opencode_version_at_enable": version}, (bundle, native_dir)


def status(*, config_dir: Path, data_dir: Path, state_dir: Path,
           cache_root: Path) -> dict[str, object]:
    """No directory creation, download, or OpenCode invocation."""
    observed, _pair = _snapshot(config_dir=config_dir, data_dir=data_dir,
                                state_dir=state_dir, cache_root=cache_root)
    return observed


def disable(*, config_dir: Path, data_dir: Path, state_dir: Path,
            cache_root: Path) -> dict[str, object]:
    observed, pair = _snapshot(config_dir=config_dir, data_dir=data_dir,
                               state_dir=state_dir, cache_root=cache_root)
    if pair is None:
        return {"status": "disabled", "changed": False}
    try:
        # The reconciler repeats ownership checks immediately before mutation.
        result = pilot.disable_pilot(
            pilot_bundle_dir=pair[0], native_artifact_dir=pair[1],
            installed_version=str(observed["opencode_version_at_enable"]),
            config_dir=config_dir, data_dir=data_dir, state_dir=state_dir,
        )
    except pilot.PilotDeploymentError as exc:
        raise ControlConflict("P0_DISABLE_CONFLICT") from exc
    return {"status": result["status"], "changed": result["changed"]}
