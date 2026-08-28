"""Declarative ToolSpec model, profile overlays, and validation."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOOL_SPEC_SCHEMA = 1
PROFILE_SCHEMA = 1
GENERIC_PROFILE = "generic"
VALID_SOURCES = {"git", "builtin"}
VALID_UPDATE_POLICIES = {"latest", "pinned-tested", "bundled-with-setup"}
VALID_RUNTIMES = {"python-venv", "python", "go-binary", "binary", "external"}
VALID_PLATFORMS = {"windows", "linux"}
_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PROFILE_METADATA = {"profile_schema", "name", "description"}
_SECRET_KEYS = {"api_key", "apikey", "authorization", "password", "private_key", "secret", "token"}


class ProfileConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HealthCheckSpec:
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    source: str
    runtime: str
    update_policy: str
    entrypoints: tuple[str, ...]
    health_contract: tuple[HealthCheckSpec, ...]
    platforms: tuple[str, ...]
    repo: str | None = None
    ref: str | None = None
    project_directory: str | None = None


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileConfigError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileConfigError(f"{label} {path} must contain a JSON object")
    return value


def _merge_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if value is None:
            result.pop(key, None)
            continue
        previous = result.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            result[key] = _merge_overlay(previous, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _profile_payload(value: dict[str, Any], *, expected_name: str | None, label: str) -> dict[str, Any]:
    schema = value.get("profile_schema")
    if schema is not None and schema != PROFILE_SCHEMA:
        raise ProfileConfigError(f"{label} has unsupported profile_schema {schema!r}")
    if expected_name is not None:
        if schema != PROFILE_SCHEMA:
            raise ProfileConfigError(f"profile {expected_name!r} must declare profile_schema {PROFILE_SCHEMA}")
        declared = value.get("name")
        if declared != expected_name:
            raise ProfileConfigError(f"profile file declares name {declared!r}, expected {expected_name!r}")
    return {key: copy.deepcopy(item) for key, item in value.items() if key not in _PROFILE_METADATA}


def _find_secret_key(value: Any, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            path = f"{prefix}.{key}" if prefix else str(key)
            if normalized in _SECRET_KEYS:
                return path
            found = _find_secret_key(item, path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_secret_key(item, f"{prefix}[{index}]")
            if found:
                return found
    return None


def load_effective_config(
    repo_root: Path,
    *,
    profile: str = GENERIC_PROFILE,
    local_override: Path | None = None,
) -> dict[str, Any]:
    """Load deterministic desired state: public base -> named profile -> local override."""
    root = repo_root.resolve()
    base = _load_json_object(root / "config_data.json", "base configuration")
    selected = profile.strip() if isinstance(profile, str) and profile.strip() else GENERIC_PROFILE
    if selected != GENERIC_PROFILE:
        if _PROFILE_RE.fullmatch(selected) is None:
            raise ProfileConfigError(f"unsafe profile name: {selected!r}")
        profile_path = root / "templates" / "profiles" / f"{selected}.json"
        profile_data = _load_json_object(profile_path, "profile")
        payload = _profile_payload(profile_data, expected_name=selected, label=f"profile {selected!r}")
        secret_key = _find_secret_key(payload)
        if secret_key:
            raise ProfileConfigError(
                f"repository profile {selected!r} contains a secret-like key {secret_key!r}; keep secrets in external local state"
            )
        base = _merge_overlay(base, payload)

    if local_override is not None:
        local_path = local_override.expanduser().resolve()
        if local_path.is_symlink() or not local_path.is_file():
            raise ProfileConfigError(f"local override must be an existing regular non-symlink file: {local_path}")
        local_data = _load_json_object(local_path, "local override")
        payload = _profile_payload(local_data, expected_name=None, label="local override")
        base = _merge_overlay(base, payload)
    return base


def resolve_repo_relative_path(repo_root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProfileConfigError(f"{label} must be a non-empty repository-relative path")
    relative = Path(value)
    if relative.is_absolute():
        raise ProfileConfigError(f"{label} must be repository-relative: {value!r}")
    root = repo_root.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ProfileConfigError(f"{label} escapes repository root: {value!r}")
    return target


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_string_list(value: Any, field: str, *, allow_empty: bool = False) -> tuple[tuple[str, ...] | None, str | None]:
    if not isinstance(value, list):
        return None, f"ToolSpec field {field!r} must be an array"
    if not allow_empty and not value:
        return None, f"ToolSpec field {field!r} must not be empty"
    if not all(_nonempty_string(item) for item in value):
        return None, f"ToolSpec field {field!r} must contain non-empty strings"
    normalized = tuple(item.strip() for item in value)
    if len(normalized) != len(set(normalized)):
        return None, f"ToolSpec field {field!r} contains duplicates"
    return normalized, None


def _parse_health(value: Any) -> tuple[tuple[HealthCheckSpec, ...] | None, str | None]:
    if not isinstance(value, list) or not value:
        return None, "ToolSpec field 'health_contract' must be a non-empty array"
    checks: list[HealthCheckSpec] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return None, f"health_contract[{index}] must be an object"
        argv, error = _parse_string_list(item.get("argv"), f"health_contract[{index}].argv")
        if error:
            return None, error
        assert argv is not None
        checks.append(HealthCheckSpec(argv=argv))
    return tuple(checks), None


def parse_tool_spec(name: str, raw: Any) -> tuple[ToolSpec | None, str | None]:
    if not _nonempty_string(name):
        return None, "ToolSpec name must be a non-empty string"
    if not isinstance(raw, dict):
        return None, f"ToolSpec {name!r} must be an object"

    source = raw.get("source")
    runtime = raw.get("runtime")
    update_policy = raw.get("update_policy")
    if source not in VALID_SOURCES:
        return None, f"ToolSpec {name!r}: unsupported source {source!r}"
    if runtime not in VALID_RUNTIMES:
        return None, f"ToolSpec {name!r}: unsupported runtime {runtime!r}"
    if update_policy not in VALID_UPDATE_POLICIES:
        return None, f"ToolSpec {name!r}: unsupported update_policy {update_policy!r}"

    entrypoints, error = _parse_string_list(raw.get("entrypoints"), "entrypoints")
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    health, error = _parse_health(raw.get("health_contract"))
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    platforms, error = _parse_string_list(raw.get("platforms", ["windows", "linux"]), "platforms")
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    assert entrypoints is not None and health is not None and platforms is not None
    if not set(platforms).issubset(VALID_PLATFORMS):
        unknown = sorted(set(platforms) - VALID_PLATFORMS)
        return None, f"ToolSpec {name!r}: unsupported platforms: {', '.join(unknown)}"

    repo = raw.get("repo")
    ref = raw.get("ref")
    project_directory = raw.get("project_directory")
    if source == "git" and not _nonempty_string(repo):
        return None, f"ToolSpec {name!r}: git source requires repo"
    if update_policy == "pinned-tested" and not _nonempty_string(ref):
        return None, f"ToolSpec {name!r}: pinned-tested requires an explicit ref"
    if source == "git" and not _nonempty_string(project_directory):
        return None, f"ToolSpec {name!r}: git source requires project_directory"
    if source == "builtin" and any(value is not None for value in (repo, ref, project_directory)):
        return None, f"ToolSpec {name!r}: builtin source must not define repo/ref/project_directory"

    return ToolSpec(
        name=name,
        source=source,
        runtime=runtime,
        update_policy=update_policy,
        entrypoints=entrypoints,
        health_contract=health,
        platforms=platforms,
        repo=repo.strip() if _nonempty_string(repo) else None,
        ref=ref.strip() if _nonempty_string(ref) else None,
        project_directory=project_directory.strip() if _nonempty_string(project_directory) else None,
    ), None


def parse_tool_specs(managed_environment: Any) -> tuple[dict[str, ToolSpec], str | None]:
    if not isinstance(managed_environment, dict):
        return {}, "managed_environment must be an object"
    schema = managed_environment.get("tool_spec_schema")
    if schema != TOOL_SPEC_SCHEMA:
        return {}, f"unsupported ToolSpec schema: {schema!r}"
    raw_tools = managed_environment.get("tools")
    if not isinstance(raw_tools, dict):
        return {}, "managed_environment.tools must be an object"

    parsed: dict[str, ToolSpec] = {}
    entrypoint_owner: dict[str, str] = {}
    for name in sorted(raw_tools):
        spec, error = parse_tool_spec(name, raw_tools[name])
        if error:
            return {}, error
        assert spec is not None
        for entrypoint in spec.entrypoints:
            previous = entrypoint_owner.get(entrypoint)
            if previous is not None:
                return {}, f"entrypoint {entrypoint!r} is declared by both {previous!r} and {name!r}"
            entrypoint_owner[entrypoint] = name
        parsed[name] = spec
    return parsed, None
