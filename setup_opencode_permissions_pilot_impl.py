"""Synthetic managed deployment for the OpenCode Permissions P0 pilot.

This module is intentionally not wired into normal ``toolchainctl apply``.
It provides the MP-1 owner-aware deployment primitive used by disposable
fixtures before any user opt-in deployment is allowed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

PILOT_OWNER = "dilukhin/opencode_permissions"
PILOT_FORMAT = "opencode-permissions-pilot-artifact/v1"
NATIVE_FORMAT = "opencode-permission-artifact/v1"
PILOT_DOMAIN = b"opencode_permissions.pilot_artifact.v1\n"
NATIVE_DOMAIN = b"opencode_permissions.artifact.v1\n"
STATE_SCHEMA = 1
STATE_OWNER = "agent-toolchain"
STATE_COMPONENT = "opencode-permissions-pilot"
PLUGIN_NAME = "opencode-permissions-p0.js"


class PilotDeploymentError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _require(condition: bool, code: str, detail: str | None = None) -> None:
    if not condition:
        raise PilotDeploymentError(code, detail)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_json(path: Path, code: str) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), code, str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PilotDeploymentError(code, str(exc)) from exc
    _require(isinstance(data, dict), code, "root must be an object")
    return data


def _safe_relative(value: Any, code: str) -> Path:
    _require(isinstance(value, str) and value, code)
    rel = Path(value)
    _require(not rel.is_absolute() and ".." not in rel.parts, code, value)
    return rel


def _pilot_identity_core(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_format": manifest.get("artifact_format"),
        "owner": manifest.get("owner"),
        "target": manifest.get("target"),
        "native_policy_artifact_id": manifest.get("native_policy_artifact_id"),
        "classifier_profile": manifest.get("classifier_profile"),
        "files": manifest.get("files"),
        "constraints": manifest.get("constraints"),
    }


def _pilot_artifact_id(manifest: dict[str, Any]) -> str:
    payload = _canonical_json(_pilot_identity_core(manifest))
    return "sha256:" + hashlib.sha256(PILOT_DOMAIN + payload).hexdigest()


def _native_identity_core(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_format": manifest.get("artifact_format"),
        "owner": manifest.get("owner"),
        "target": manifest.get("target"),
        "policy_source_sha256": manifest.get("policy_source", {}).get("sha256"),
        "renderer": manifest.get("renderer"),
        "output_sha256": manifest.get("output", {}).get("sha256"),
    }


def _native_artifact_id(manifest: dict[str, Any]) -> str:
    payload = _canonical_json(_native_identity_core(manifest))
    return "sha256:" + hashlib.sha256(NATIVE_DOMAIN + payload).hexdigest()


def _artifact_segment(artifact_id: Any, code: str) -> str:
    _require(
        isinstance(artifact_id, str)
        and artifact_id.startswith("sha256:")
        and len(artifact_id) == len("sha256:") + 64
        and all(ch in "0123456789abcdef" for ch in artifact_id.split(":", 1)[1]),
        code,
    )
    return "sha256-" + artifact_id.split(":", 1)[1]


def _collect_files(root: Path) -> set[str]:
    _require(root.is_dir() and not root.is_symlink(), "ARTIFACT_DIRECTORY_INVALID", str(root))
    result: set[str] = set()
    for path in root.rglob("*"):
        _require(not path.is_symlink(), "ARTIFACT_SYMLINK_FORBIDDEN", str(path))
        if path.is_file():
            result.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise PilotDeploymentError("ARTIFACT_SPECIAL_FILE_FORBIDDEN", str(path))
    return result


def _validate_permission_tree(value: Any) -> None:
    _require(isinstance(value, dict) and value, "NATIVE_PERMISSION_INVALID")
    for permission, rules in value.items():
        _require(isinstance(permission, str) and permission, "NATIVE_PERMISSION_INVALID")
        if isinstance(rules, str):
            _require(rules in {"allow", "ask", "deny"}, "NATIVE_PERMISSION_ACTION_INVALID")
            continue
        _require(isinstance(rules, dict) and rules, "NATIVE_PERMISSION_RULESET_INVALID", permission)
        for pattern, action in rules.items():
            _require(isinstance(pattern, str) and pattern, "NATIVE_PERMISSION_PATTERN_INVALID")
            _require(action in {"allow", "ask", "deny"}, "NATIVE_PERMISSION_ACTION_INVALID")


def validate_artifacts(
    *,
    pilot_bundle_dir: Path,
    native_artifact_dir: Path,
    installed_version: str,
    installed_platform: str = "linux",
) -> dict[str, Any]:
    pilot_bundle_dir = Path(pilot_bundle_dir).resolve()
    native_artifact_dir = Path(native_artifact_dir).resolve()

    pilot = _load_json(pilot_bundle_dir / "manifest.json", "PILOT_MANIFEST_INVALID")
    _require(pilot.get("schema") == 1, "PILOT_MANIFEST_SCHEMA_UNSUPPORTED")
    _require(pilot.get("artifact_format") == PILOT_FORMAT, "PILOT_FORMAT_UNSUPPORTED")
    _require(pilot.get("status") == "mp0_ready", "PILOT_NOT_READY")
    _require(pilot.get("owner") == PILOT_OWNER, "PILOT_OWNER_MISMATCH")

    target = pilot.get("target")
    _require(isinstance(target, dict), "PILOT_TARGET_INVALID")
    _require(target.get("product") == "opencode", "PILOT_TARGET_PRODUCT_MISMATCH")
    _require(target.get("exact_version") == installed_version, "INSTALLED_VERSION_MISMATCH")
    _require(target.get("platform") == installed_platform, "INSTALLED_PLATFORM_MISMATCH")
    _require(installed_platform == "linux", "PILOT_PLATFORM_UNSUPPORTED")

    constraints = pilot.get("constraints")
    _require(isinstance(constraints, dict), "PILOT_CONSTRAINTS_INVALID")
    required_constraints = {
        "exact_version_only": True,
        "requires_deployable_profile": True,
        "nearest_version_fallback": False,
        "setup_semantic_rewrite": False,
        "managed_global_plugin_required": True,
        "developer_checkout_dependency": False,
        "auditor_enabled": False,
        "workspace_trust_enabled": False,
        "state_changing_classifier_enabled": False,
        "classifier_error_result": "ASK_USER",
        "competing_effective_layer_result": "CONFLICT",
        "runtime_bundle_digest_check": True,
    }
    for key, expected in required_constraints.items():
        _require(constraints.get(key) == expected, "PILOT_CONSTRAINT_MISMATCH", key)

    expected_pilot_id = _pilot_artifact_id(pilot)
    _require(pilot.get("artifact_id") == expected_pilot_id, "PILOT_ARTIFACT_ID_MISMATCH")
    expected_pilot_segment = _artifact_segment(expected_pilot_id, "PILOT_ARTIFACT_ID_INVALID")
    _require(pilot.get("artifact_path_segment") == expected_pilot_segment, "PILOT_PATH_SEGMENT_MISMATCH")
    _require(pilot_bundle_dir.name == expected_pilot_segment, "PILOT_DIRECTORY_MISMATCH")

    files = pilot.get("files")
    _require(isinstance(files, list) and files, "PILOT_FILES_INVALID")
    expected_files = {"manifest.json"}
    seen: set[str] = set()
    for item in files:
        _require(isinstance(item, dict), "PILOT_FILE_ENTRY_INVALID")
        rel = _safe_relative(item.get("path"), "PILOT_FILE_PATH_INVALID")
        rel_text = rel.as_posix()
        _require(rel_text not in seen, "PILOT_FILE_DUPLICATE", rel_text)
        seen.add(rel_text)
        expected_files.add(rel_text)
        full = pilot_bundle_dir / rel
        _require(full.is_file() and not full.is_symlink(), "PILOT_FILE_MISSING", rel_text)
        _require(_sha256_file(full) == item.get("sha256"), "PILOT_FILE_DIGEST_MISMATCH", rel_text)
        _require(full.stat().st_size == item.get("size"), "PILOT_FILE_SIZE_MISMATCH", rel_text)
    _require(_collect_files(pilot_bundle_dir) == expected_files, "PILOT_FILESET_MISMATCH")

    native = _load_json(native_artifact_dir / "manifest.json", "NATIVE_MANIFEST_INVALID")
    _require(native.get("schema") == 1, "NATIVE_MANIFEST_SCHEMA_UNSUPPORTED")
    _require(native.get("artifact_format") == NATIVE_FORMAT, "NATIVE_FORMAT_UNSUPPORTED")
    _require(native.get("status") == "deployable", "NATIVE_ARTIFACT_NOT_DEPLOYABLE")
    _require(native.get("owner") == PILOT_OWNER, "NATIVE_OWNER_MISMATCH")
    native_target = native.get("target")
    _require(isinstance(native_target, dict), "NATIVE_TARGET_INVALID")
    for key in ("product", "exact_version", "platform", "compatibility_profile_id"):
        _require(native_target.get(key) == target.get(key), "NATIVE_TARGET_MISMATCH", key)

    native_id = native.get("artifact_id")
    _require(native_id == pilot.get("native_policy_artifact_id"), "NATIVE_ARTIFACT_PIN_MISMATCH")
    _require(_native_artifact_id(native) == native_id, "NATIVE_ARTIFACT_ID_MISMATCH")
    native_segment = _artifact_segment(native_id, "NATIVE_ARTIFACT_ID_INVALID")
    _require(native.get("artifact_path_segment") == native_segment, "NATIVE_PATH_SEGMENT_MISMATCH")
    _require(native_artifact_dir.name == native_segment, "NATIVE_DIRECTORY_MISMATCH")
    native_constraints = native.get("constraints")
    _require(isinstance(native_constraints, dict), "NATIVE_CONSTRAINTS_INVALID")
    for key, expected in {
        "exact_version_only": True,
        "requires_deployable_profile": True,
        "nearest_version_fallback": False,
        "setup_semantic_rewrite": False,
        "effective_readback_required": True,
        "competing_effective_layer_result": "CONFLICT",
    }.items():
        _require(native_constraints.get(key) == expected, "NATIVE_CONSTRAINT_MISMATCH", key)

    output = native.get("output")
    _require(isinstance(output, dict), "NATIVE_OUTPUT_INVALID")
    output_rel = _safe_relative(output.get("relative_path"), "NATIVE_OUTPUT_PATH_INVALID")
    _require(output_rel == Path("permission.jsonc"), "NATIVE_OUTPUT_PATH_NONCANONICAL")
    permission_path = native_artifact_dir / output_rel
    _require(permission_path.is_file() and not permission_path.is_symlink(), "NATIVE_OUTPUT_MISSING")
    _require(_sha256_file(permission_path) == output.get("sha256"), "NATIVE_OUTPUT_DIGEST_MISMATCH")
    _require(_collect_files(native_artifact_dir) == {"manifest.json", "permission.jsonc"}, "NATIVE_FILESET_MISMATCH")

    permission_doc = _load_json(permission_path, "NATIVE_PERMISSION_JSON_INVALID")
    _require(set(permission_doc) == {"permission"}, "NATIVE_PERMISSION_TOPLEVEL_INVALID")
    _validate_permission_tree(permission_doc["permission"])

    classifier = pilot.get("classifier_profile")
    _require(isinstance(classifier, dict), "PILOT_CLASSIFIER_PROFILE_INVALID")
    profile_rel = _safe_relative(classifier.get("relative_path"), "PILOT_PROFILE_PATH_INVALID")
    profile_path = pilot_bundle_dir / profile_rel
    _require(_sha256_file(profile_path) == classifier.get("sha256"), "PILOT_PROFILE_DIGEST_MISMATCH")
    profile = _load_json(profile_path, "PILOT_PROFILE_INVALID")
    _require(profile.get("classifier_profile_id") == classifier.get("id"), "PILOT_PROFILE_ID_MISMATCH")
    _require(profile.get("target", {}).get("opencode_version") == installed_version, "PILOT_PROFILE_VERSION_MISMATCH")
    _require(
        profile.get("target", {}).get("compatibility_profile_id") == target.get("compatibility_profile_id"),
        "PILOT_PROFILE_COMPATIBILITY_MISMATCH",
    )
    _require(
        profile.get("target", {}).get("native_policy_artifact_id") == native_id,
        "PILOT_PROFILE_NATIVE_ARTIFACT_MISMATCH",
    )

    return {
        "pilot_manifest": pilot,
        "native_manifest": native,
        "permission": copy.deepcopy(permission_doc["permission"]),
        "pilot_artifact_id": expected_pilot_id,
        "native_artifact_id": native_id,
        "artifact_path_segment": expected_pilot_segment,
    }


def _runtime_dir(data_dir: Path, segment: str) -> Path:
    return Path(data_dir).resolve() / "opencode_permissions" / segment


def _plugin_path(config_dir: Path) -> Path:
    return Path(config_dir).resolve() / "plugins" / PLUGIN_NAME


def _config_path(config_dir: Path) -> Path:
    return Path(config_dir).resolve() / "opencode.jsonc"


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir).resolve() / "opencode-permissions-pilot.json"


def _runtime_matches(runtime_dir: Path, source_dir: Path) -> bool:
    if not runtime_dir.is_dir() or runtime_dir.is_symlink():
        return False
    try:
        source_files = _collect_files(source_dir)
        if _collect_files(runtime_dir) != source_files:
            return False
        for relative in source_files:
            if (runtime_dir / relative).read_bytes() != (source_dir / relative).read_bytes():
                return False
    except (OSError, PilotDeploymentError):
        return False
    return True


def _publish_runtime(source_dir: Path, runtime_dir: Path) -> bool:
    if runtime_dir.exists() or runtime_dir.is_symlink():
        _require(_runtime_matches(runtime_dir, source_dir), "RUNTIME_OWNERSHIP_CONFLICT", str(runtime_dir))
        return False
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = runtime_dir.with_name(runtime_dir.name + f".staging-{os.getpid()}")
    _require(not staging.exists() and not staging.is_symlink(), "RUNTIME_STAGING_CONFLICT", str(staging))
    try:
        shutil.copytree(source_dir, staging, symlinks=False)
        _require(_runtime_matches(staging, source_dir), "RUNTIME_STAGING_VERIFICATION_FAILED")
        os.replace(staging, runtime_dir)
    finally:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
    return True


def _loader_bytes(runtime_dir: Path) -> bytes:
    bridge = (runtime_dir / "bridge.js").resolve()
    uri = bridge.as_uri()
    return f'export {{ OpenCodePermissionsP0 }} from {json.dumps(uri)}\n'.encode("utf-8")


def _parse_config(path: Path) -> tuple[dict[str, Any], bool, bytes | None]:
    if not path.exists():
        return {}, False, None
    _require(path.is_file() and not path.is_symlink(), "CONFIG_PATH_CONFLICT", str(path))
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PilotDeploymentError(
            "CONFIG_NOT_SAFE_FOR_PILOT_MERGE",
            "MP-1 only mutates plain JSON; JSONC/comment-preserving merge remains owned by the main reconciler: " + str(exc),
        ) from exc
    _require(isinstance(value, dict), "CONFIG_ROOT_INVALID")
    return value, True, raw


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    state = _load_json(path, "PILOT_STATE_INVALID")
    _require(state.get("schema") == STATE_SCHEMA, "PILOT_STATE_SCHEMA_UNSUPPORTED")
    _require(state.get("owner") == STATE_OWNER, "PILOT_STATE_OWNER_MISMATCH")
    _require(state.get("component") == STATE_COMPONENT, "PILOT_STATE_COMPONENT_MISMATCH")
    _require(state.get("phase") in {"prepared", "active"}, "PILOT_STATE_PHASE_INVALID")
    return state


def _state_bytes(state: dict[str, Any]) -> bytes:
    return _pretty_json(state)


def _verify_state_binding(
    state: dict[str, Any],
    *,
    validation: dict[str, Any],
    runtime_dir: Path,
    config_path: Path,
    plugin_path: Path,
) -> dict[str, bool]:
    _require(state.get("artifact_id") == validation["pilot_artifact_id"], "ACTIVE_ARTIFACT_MISMATCH")
    _require(state.get("native_artifact_id") == validation["native_artifact_id"], "ACTIVE_NATIVE_ARTIFACT_MISMATCH")
    _require(state.get("opencode_version") == validation["pilot_manifest"]["target"]["exact_version"], "ACTIVE_VERSION_MISMATCH")
    _require(state.get("runtime_dir") == str(runtime_dir), "ACTIVE_RUNTIME_PATH_MISMATCH")
    _require(state.get("config_path") == str(config_path), "ACTIVE_CONFIG_PATH_MISMATCH")
    _require(state.get("plugin_path") == str(plugin_path), "ACTIVE_PLUGIN_PATH_MISMATCH")

    config_matches_before = False
    config_matches_after = False
    if config_path.is_file() and not config_path.is_symlink():
        digest = _sha256_file(config_path)
        config_matches_before = digest == state.get("config_sha256_before")
        config_matches_after = digest == state.get("config_sha256_after")
    elif not config_path.exists() and state.get("config_existed_before") is False:
        config_matches_before = True

    plugin_matches = False
    if plugin_path.is_file() and not plugin_path.is_symlink():
        plugin_matches = _sha256_file(plugin_path) == state.get("plugin_sha256")

    return {
        "config_before": config_matches_before,
        "config_after": config_matches_after,
        "plugin": plugin_matches,
    }


def inspect_pilot(
    *,
    pilot_bundle_dir: Path,
    native_artifact_dir: Path,
    installed_version: str,
    config_dir: Path,
    data_dir: Path,
    state_dir: Path,
    installed_platform: str = "linux",
) -> dict[str, Any]:
    validation = validate_artifacts(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        installed_platform=installed_platform,
    )
    runtime_dir = _runtime_dir(data_dir, validation["artifact_path_segment"])
    plugin_path = _plugin_path(config_dir)
    config_path = _config_path(config_dir)
    state_path = _state_path(state_dir)
    state = _load_state(state_path)
    if state is None:
        _require(not plugin_path.exists() and not plugin_path.is_symlink(), "UNKNOWN_PLUGIN_WITHOUT_STATE")
        return {
            "status": "disabled",
            "artifact_id": validation["pilot_artifact_id"],
            "runtime_cached": _runtime_matches(runtime_dir, Path(pilot_bundle_dir).resolve()),
        }

    binding = _verify_state_binding(
        state,
        validation=validation,
        runtime_dir=runtime_dir,
        config_path=config_path,
        plugin_path=plugin_path,
    )
    _require(_runtime_matches(runtime_dir, Path(pilot_bundle_dir).resolve()), "RUNTIME_READBACK_FAILED")
    if state["phase"] == "active":
        _require(binding["config_after"], "CONFIG_EFFECTIVE_READBACK_FAILED")
        _require(binding["plugin"], "PLUGIN_EFFECTIVE_READBACK_FAILED")
        config = _load_json(config_path, "CONFIG_EFFECTIVE_READBACK_FAILED")
        _require(config.get("permission") == validation["permission"], "PERMISSION_EFFECTIVE_READBACK_FAILED")
        _require(plugin_path.read_bytes() == _loader_bytes(runtime_dir), "PLUGIN_TARGET_READBACK_FAILED")
        return {
            "status": "active",
            "artifact_id": validation["pilot_artifact_id"],
            "native_artifact_id": validation["native_artifact_id"],
            "runtime_dir": str(runtime_dir),
            "plugin_path": str(plugin_path),
            "effective_readback": "PASS",
        }
    return {
        "status": "prepared",
        "artifact_id": validation["pilot_artifact_id"],
        "binding": binding,
    }


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
    validation = validate_artifacts(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        installed_platform=installed_platform,
    )
    source_dir = Path(pilot_bundle_dir).resolve()
    runtime_dir = _runtime_dir(data_dir, validation["artifact_path_segment"])
    plugin_path = _plugin_path(config_dir)
    config_path = _config_path(config_dir)
    state_path = _state_path(state_dir)
    state = _load_state(state_path)

    if state is None:
        _require(not plugin_path.exists() and not plugin_path.is_symlink(), "UNKNOWN_PLUGIN_CONFLICT", str(plugin_path))
        config, existed, raw_before = _parse_config(config_path)
        previous_present = "permission" in config
        previous_permission = copy.deepcopy(config.get("permission"))
        desired_config = copy.deepcopy(config)
        desired_config["permission"] = copy.deepcopy(validation["permission"])
        config_after = _pretty_json(desired_config)
        loader = _loader_bytes(runtime_dir)

        # All content/ownership preflight is complete before the first mutation.
        runtime_changed = _publish_runtime(source_dir, runtime_dir)
        state = {
            "schema": STATE_SCHEMA,
            "owner": STATE_OWNER,
            "component": STATE_COMPONENT,
            "phase": "prepared",
            "artifact_id": validation["pilot_artifact_id"],
            "native_artifact_id": validation["native_artifact_id"],
            "opencode_version": installed_version,
            "runtime_dir": str(runtime_dir),
            "config_path": str(config_path),
            "plugin_path": str(plugin_path),
            "config_existed_before": existed,
            "config_sha256_before": _sha256_bytes(raw_before) if raw_before is not None else None,
            "previous_permission_present": previous_present,
            "previous_permission": previous_permission if previous_present else None,
            "config_sha256_after": _sha256_bytes(config_after),
            "plugin_sha256": _sha256_bytes(loader),
        }
        _atomic_write(state_path, _state_bytes(state))
    else:
        binding = _verify_state_binding(
            state,
            validation=validation,
            runtime_dir=runtime_dir,
            config_path=config_path,
            plugin_path=plugin_path,
        )
        _require(_runtime_matches(runtime_dir, source_dir), "RUNTIME_OWNERSHIP_CONFLICT", str(runtime_dir))
        if state["phase"] == "active":
            _require(binding["config_after"], "ACTIVE_CONFIG_DRIFT")
            _require(binding["plugin"], "ACTIVE_PLUGIN_DRIFT")
            result = inspect_pilot(
                pilot_bundle_dir=source_dir,
                native_artifact_dir=native_artifact_dir,
                installed_version=installed_version,
                config_dir=config_dir,
                data_dir=data_dir,
                state_dir=state_dir,
                installed_platform=installed_platform,
            )
            result["changed"] = False
            return result
        _require(
            binding["config_before"] or binding["config_after"],
            "PREPARED_CONFIG_DRIFT",
        )
        _require(not plugin_path.exists() or binding["plugin"], "PREPARED_PLUGIN_DRIFT")
        runtime_changed = False

    # Resume-safe publication from the prepared state.
    config, existed_now, raw_now = _parse_config(config_path)
    digest_now = _sha256_bytes(raw_now) if raw_now is not None else None
    if digest_now != state["config_sha256_after"]:
        before_ok = (
            (state["config_existed_before"] and digest_now == state["config_sha256_before"])
            or (not state["config_existed_before"] and not existed_now)
        )
        _require(before_ok, "PREPARED_CONFIG_DRIFT")
        desired_config = copy.deepcopy(config)
        desired_config["permission"] = copy.deepcopy(validation["permission"])
        config_after = _pretty_json(desired_config)
        _require(_sha256_bytes(config_after) == state["config_sha256_after"], "PREPARED_CONFIG_RECONSTRUCTION_MISMATCH")
        _atomic_write(config_path, config_after)

    loader = _loader_bytes(runtime_dir)
    if plugin_path.exists() or plugin_path.is_symlink():
        _require(plugin_path.is_file() and not plugin_path.is_symlink(), "PLUGIN_PATH_CONFLICT")
        _require(_sha256_file(plugin_path) == state["plugin_sha256"], "PREPARED_PLUGIN_DRIFT")
    else:
        _atomic_write(plugin_path, loader)

    state["phase"] = "active"
    _atomic_write(state_path, _state_bytes(state))
    result = inspect_pilot(
        pilot_bundle_dir=source_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        installed_platform=installed_platform,
    )
    result["changed"] = True
    result["runtime_changed"] = runtime_changed
    return result


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
    validation = validate_artifacts(
        pilot_bundle_dir=pilot_bundle_dir,
        native_artifact_dir=native_artifact_dir,
        installed_version=installed_version,
        installed_platform=installed_platform,
    )
    runtime_dir = _runtime_dir(data_dir, validation["artifact_path_segment"])
    plugin_path = _plugin_path(config_dir)
    config_path = _config_path(config_dir)
    state_path = _state_path(state_dir)
    state = _load_state(state_path)

    if state is None:
        _require(not plugin_path.exists() and not plugin_path.is_symlink(), "UNKNOWN_PLUGIN_WITHOUT_STATE")
        return {
            "status": "disabled",
            "changed": False,
            "runtime_cached": _runtime_matches(runtime_dir, Path(pilot_bundle_dir).resolve()),
        }

    binding = _verify_state_binding(
        state,
        validation=validation,
        runtime_dir=runtime_dir,
        config_path=config_path,
        plugin_path=plugin_path,
    )
    _require(_runtime_matches(runtime_dir, Path(pilot_bundle_dir).resolve()), "RUNTIME_READBACK_FAILED")

    plugin_exists = plugin_path.exists() or plugin_path.is_symlink()
    if plugin_exists:
        _require(plugin_path.is_file() and not plugin_path.is_symlink(), "PLUGIN_OWNERSHIP_CONFLICT")
        _require(binding["plugin"], "PLUGIN_OWNERSHIP_CONFLICT")
    if state["phase"] == "active":
        _require(binding["config_after"], "CONFIG_ROLLBACK_CONFLICT")
    else:
        _require(binding["config_before"] or binding["config_after"], "CONFIG_ROLLBACK_CONFLICT")

    # Preflight is complete. Remove execution hook first, then restore native policy.
    if plugin_exists:
        plugin_path.unlink()

    if binding["config_after"]:
        current = _load_json(config_path, "CONFIG_ROLLBACK_CONFLICT")
        if state.get("previous_permission_present"):
            current["permission"] = copy.deepcopy(state.get("previous_permission"))
        else:
            current.pop("permission", None)

        if state.get("config_existed_before"):
            restored = _pretty_json(current)
            _atomic_write(config_path, restored)
        else:
            _require(not current, "CONFIG_ROLLBACK_CREATED_FILE_HAS_OTHER_FIELDS")
            config_path.unlink()
    elif not binding["config_before"]:
        raise PilotDeploymentError("CONFIG_ROLLBACK_CONFLICT")

    state_path.unlink()
    return {
        "status": "disabled",
        "changed": True,
        "runtime_cached": _runtime_matches(runtime_dir, Path(pilot_bundle_dir).resolve()),
    }
