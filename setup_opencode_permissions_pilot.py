"""Fail-closed public facade for OpenCode Permissions P0 managed deployment.

The transaction core is kept in ``setup_opencode_permissions_pilot_impl``.  This
facade owns preflight rules that must run before the core is allowed to create
runtime/state/config/plugin files.  Normal ``toolchainctl apply`` still does not
import or invoke this module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import setup_opencode_permissions_pilot_impl as _impl

PILOT_OWNER = _impl.PILOT_OWNER
PILOT_FORMAT = _impl.PILOT_FORMAT
NATIVE_FORMAT = _impl.NATIVE_FORMAT
PILOT_DOMAIN = _impl.PILOT_DOMAIN
NATIVE_DOMAIN = _impl.NATIVE_DOMAIN
STATE_SCHEMA = _impl.STATE_SCHEMA
STATE_OWNER = _impl.STATE_OWNER
STATE_COMPONENT = _impl.STATE_COMPONENT
PLUGIN_NAME = _impl.PLUGIN_NAME
PilotDeploymentError = _impl.PilotDeploymentError

# These helpers remain available for the synthetic artifact builders/tests.  They
# are not production mutation entrypoints.
_sha256_bytes = _impl._sha256_bytes
_sha256_file = _impl._sha256_file
_canonical_json = _impl._canonical_json
_pretty_json = _impl._pretty_json
_pilot_artifact_id = _impl._pilot_artifact_id
_native_artifact_id = _impl._native_artifact_id
_artifact_segment = _impl._artifact_segment
_validate_permission_tree = _impl._validate_permission_tree

# Recovery tests inject an interruption here.  The facade synchronizes the hook
# into the core immediately before every mutating operation.
_atomic_write = _impl._atomic_write


def validate_artifacts(**kwargs: Any) -> dict[str, Any]:
    return _impl.validate_artifacts(**kwargs)


def _validate_existing_recovery_permission(*, config_dir: Path, state_dir: Path) -> None:
    """Reject arbitrary recovery payloads before the first MP-1 mutation.

    An already-active/prepared deployment has a content-bound state record whose
    previous permission value was accepted during its initial preflight.  For a
    fresh deployment, only an OpenCode permission action tree may be copied into
    recovery state; unrelated JSON (including accidental secret-like payloads)
    is never persisted there.
    """
    state_path = _impl._state_path(state_dir)
    if _impl._load_state(state_path) is not None:
        return
    config, _existed, _raw = _impl._parse_config(_impl._config_path(config_dir))
    if "permission" not in config:
        return
    try:
        _impl._validate_permission_tree(config["permission"])
    except PilotDeploymentError as exc:
        raise PilotDeploymentError("RECOVERY_PERMISSION_INVALID", exc.code) from exc


def inspect_pilot(**kwargs: Any) -> dict[str, Any]:
    return _impl.inspect_pilot(**kwargs)


def apply_pilot(
    *,
    pilot_bundle_dir: Path,
    native_artifact_dir: Path,
    installed_version: str,
    config_dir: Path,
    data_dir: Path,
    state_dir: Path,
    installed_platform: str = "linux",
) -> dict[str, Any]:
    # Artifact/version validation first: unsupported targets must not inspect or
    # mutate managed destinations as if they were deployable.
    _impl.validate_artifacts(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        installed_platform=installed_platform,
    )
    _validate_existing_recovery_permission(config_dir=Path(config_dir), state_dir=Path(state_dir))
    _impl._atomic_write = _atomic_write
    return _impl.apply_pilot(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        installed_platform=installed_platform,
    )


def disable_pilot(
    *,
    pilot_bundle_dir: Path,
    native_artifact_dir: Path,
    installed_version: str,
    config_dir: Path,
    data_dir: Path,
    state_dir: Path,
    installed_platform: str = "linux",
) -> dict[str, Any]:
    _impl._atomic_write = _atomic_write
    return _impl.disable_pilot(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        installed_platform=installed_platform,
    )
