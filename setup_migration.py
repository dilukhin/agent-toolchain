"""Compatibility wrappers for migration-specific reconciliation rules."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from setup_lib import (
    Reporter,
    STATE_CONFIGURED,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUTDATED,
    atomic_write,
    backup_file,
    merge_routerai_config,
    parse_jsonc_object,
    routerai_provider,
    sha256_bytes,
)

_FILE_REF_RE = re.compile(r"\{file:(.+)\}")
_SIBLING_MODE = "merged-json-sibling-provider"


def _preserve_exact_external_reference(destination: Path, desired_data: bytes) -> bytes:
    if not destination.is_file():
        return desired_data
    existing, error, _ = parse_jsonc_object(destination.read_bytes())
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None:
        return desired_data
    existing_router = routerai_provider(existing)
    desired_router = routerai_provider(desired)
    if existing_router is None or desired_router is None:
        return desired_data
    existing_options = existing_router.get("options")
    desired_options = desired_router.get("options")
    if not isinstance(existing_options, dict) or not isinstance(desired_options, dict):
        return desired_data
    current_ref = existing_options.get("apiKey")
    target_ref = desired_options.get("apiKey")
    if not isinstance(current_ref, str) or _FILE_REF_RE.fullmatch(current_ref.strip()) is None:
        return desired_data
    if target_ref == current_ref:
        return desired_data
    desired_options["apiKey"] = current_ref
    return (json.dumps(desired, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


OPENCODE_SEMANTIC_MODE = "semantic-paths-v1"
_LEGACY_MANAGED_MODES = frozenset({None, "merged-json", _SIBLING_MODE})
_MANAGED_PATHS_FIELD = "managed_paths"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _json_pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _json_pointer_parts(pointer: str) -> tuple[str, ...] | None:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return None
    parts: list[str] = []
    for raw in pointer[1:].split("/"):
        value = raw.replace("~1", "/").replace("~0", "~")
        parts.append(value)
    return tuple(parts)


def _path_value(root: dict[str, Any], parts: tuple[str, ...]) -> tuple[bool, Any]:
    current: Any = root
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _set_path_value(root: dict[str, Any], parts: tuple[str, ...], value: Any) -> str | None:
    if not parts:
        return "managed path cannot target the config root"
    current: dict[str, Any] = root
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        child = current[part]
        if not isinstance(child, dict):
            return f"cannot set {_json_pointer(parts)} because {part!r} is not an object"
        current = child
    current[parts[-1]] = copy.deepcopy(value)
    return None


def _semantic_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def _managed_target_items(desired: dict[str, Any]) -> tuple[dict[str, tuple[tuple[str, ...], Any]], str | None]:
    result: dict[str, tuple[tuple[str, ...], Any]] = {}

    def add(parts: tuple[str, ...]) -> None:
        exists, value = _path_value(desired, parts)
        if exists:
            result[_json_pointer(parts)] = (parts, copy.deepcopy(value))

    for parts in (
        ("autoupdate",),
        ("provider", "routerai", "npm"),
        ("provider", "routerai", "name"),
        ("provider", "routerai", "options", "baseURL"),
        ("model",),
        ("small_model",),
    ):
        add(parts)

    agents = desired.get("agent")
    if agents is not None and not isinstance(agents, dict):
        return {}, "template agent value is not an object"
    if isinstance(agents, dict):
        for name, spec in agents.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                return {}, "template agent entries must be objects"
            if "model" in spec:
                add(("agent", name, "model"))
    return result, None


def _routing_pointers(targets: dict[str, tuple[tuple[str, ...], Any]]) -> set[str]:
    return {
        pointer
        for pointer, (parts, _value) in targets.items()
        if parts in {("model",), ("small_model",)} or (len(parts) == 3 and parts[0] == "agent" and parts[2] == "model")
    }


def _semantic_owned_pointers(previous: dict[str, Any] | None) -> set[str]:
    if not previous or previous.get("mode") != OPENCODE_SEMANTIC_MODE:
        return set()
    managed = previous.get(_MANAGED_PATHS_FIELD)
    return set(managed) if isinstance(managed, dict) else set()


def inspect_managed_opencode_paths(
    existing: dict[str, Any],
    previous: dict[str, Any],
) -> tuple[list[str], str | None]:
    """Return semantic managed-path drift without using the whole-file hash."""
    if previous.get("mode") != OPENCODE_SEMANTIC_MODE:
        return [], None
    managed = previous.get(_MANAGED_PATHS_FIELD)
    if not isinstance(managed, dict):
        return [], f"{_MANAGED_PATHS_FIELD} is missing or not an object"
    drift: list[str] = []
    for pointer, expected_hash in sorted(managed.items()):
        if not isinstance(pointer, str) or not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
            return [], f"invalid managed path evidence: {pointer!r}"
        parts = _json_pointer_parts(pointer)
        if parts is None:
            return [], f"invalid managed JSON pointer: {pointer!r}"
        exists, value = _path_value(existing, parts)
        if not exists or _semantic_hash(value) != expected_hash:
            drift.append(pointer)
    return drift, None


def _apply_routing_target(
    merged: dict[str, Any],
    existing: dict[str, Any],
    desired: dict[str, Any],
    previous: dict[str, Any] | None,
) -> str | None:
    targets, target_error = _managed_target_items(desired)
    if target_error:
        return target_error
    routing = _routing_pointers(targets)
    mode = previous.get("mode") if previous else None
    owned = _semantic_owned_pointers(previous)
    legacy_owned = bool(previous) and mode in _LEGACY_MANAGED_MODES

    for pointer in sorted(routing):
        parts, desired_value = targets[pointer]
        exists, _current_value = _path_value(existing, parts)
        enforce = legacy_owned or pointer in owned or not exists
        if not enforce:
            continue
        error = _set_path_value(merged, parts, desired_value)
        if error:
            return error
    return None


def preview_opencode_config_target(
    *,
    destination: Path,
    desired_data: bytes,
    previous: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the exact semantic target used by reconciliation without mutation."""
    desired_data = _preserve_exact_external_reference(destination, desired_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if desired_error or desired is None:
        return None, desired_error or "template cannot be parsed"
    if not destination.is_file():
        return desired, None

    existing, error, has_jsonc_features = parse_jsonc_object(destination.read_bytes())
    if error or existing is None:
        return None, error or "existing config cannot be parsed"

    providers = existing.get("provider")
    existing_router = routerai_provider(existing)
    desired_router = routerai_provider(desired)
    if existing_router is None and desired_router is not None:
        provider_missing = "provider" not in existing
        if not provider_missing and not isinstance(providers, dict):
            return None, "existing provider value is not an object"
        if has_jsonc_features:
            return None, (
                "existing config without RouterAI has comments/trailing commas; "
                "reconciliation preserves formatting and does not apply the semantic target"
            )
        merged = copy.deepcopy(existing)
        merged_providers = merged.setdefault("provider", {})
        if not isinstance(merged_providers, dict):
            return None, "existing provider value is not an object"
        merged_providers["routerai"] = copy.deepcopy(desired_router)
        if "$schema" not in merged and "$schema" in desired:
            merged["$schema"] = copy.deepcopy(desired["$schema"])
    else:
        merged, merge_error = merge_routerai_config(existing, desired)
        if merge_error or merged is None:
            return None, merge_error or "existing config is not safely mergeable"

    if "autoupdate" in desired:
        merged["autoupdate"] = copy.deepcopy(desired["autoupdate"])

    routing_error = _apply_routing_target(merged, existing, desired, previous)
    if routing_error:
        return None, routing_error
    return merged, None


def _ownership_plan(
    *,
    existing: dict[str, Any],
    target: dict[str, Any],
    desired: dict[str, Any],
    previous: dict[str, Any] | None,
    current_hash: str,
    force: bool,
) -> tuple[set[str] | None, list[str], str | None]:
    targets, target_error = _managed_target_items(desired)
    if target_error:
        return None, [], target_error
    routing = _routing_pointers(targets)
    external_routes: list[str] = []

    if previous is None:
        owned: set[str] = set()
        for pointer, (parts, desired_value) in targets.items():
            exists, current_value = _path_value(existing, parts)
            target_exists, _target_value = _path_value(target, parts)
            if not target_exists:
                continue
            if not exists:
                owned.add(pointer)
                continue
            if pointer in routing:
                if current_value != desired_value:
                    external_routes.append(pointer)
                else:
                    # Matching values may be adopted only by explicit apply; check
                    # reports the pending metadata transition without writing.
                    owned.add(pointer)
                continue
            # Preserve the pre-#75 safe-merge contract for stable non-routing
            # OpenCode fields (autoupdate and fixed RouterAI provider metadata).
            # Routing is intentionally stricter: pre-existing model choices remain external.
            owned.add(pointer)
        return owned, external_routes, None

    mode = previous.get("mode")
    if mode in _LEGACY_MANAGED_MODES:
        recorded = previous.get("sha256")
        if not isinstance(recorded, str) or recorded != current_hash:
            recorded_label = recorded if isinstance(recorded, str) else "missing"
            return None, [], (
                "legacy whole-file ownership hash mismatch: "
                f"recorded_sha256={recorded_label}; current_sha256={current_hash}; "
                "automatic path-level migration is blocked and --force does not adopt unknown drift"
            )
        return set(targets), external_routes, None

    if mode != OPENCODE_SEMANTIC_MODE:
        return None, [], f"unsupported OpenCode ownership mode: {mode!r}"

    drift, drift_error = inspect_managed_opencode_paths(existing, previous)
    if drift_error:
        return None, [], drift_error
    if drift and not force:
        return None, [], (
            "managed OpenCode paths were modified: " + ", ".join(drift) + "; "
            "review with toolchainctl diff opencode-config before an explicit --force repair"
        )

    managed = previous.get(_MANAGED_PATHS_FIELD)
    assert isinstance(managed, dict)
    owned = set(managed)
    unknown_owned = sorted(pointer for pointer in owned if pointer not in targets)
    if unknown_owned:
        return None, [], (
            "managed OpenCode policy no longer defines recorded paths: " + ", ".join(unknown_owned)
        )

    for pointer, (parts, desired_value) in targets.items():
        if pointer in owned:
            continue
        exists, current_value = _path_value(existing, parts)
        target_exists, target_value = _path_value(target, parts)
        if not target_exists:
            continue
        if not exists:
            owned.add(pointer)
            continue
        if pointer in routing:
            if current_value != desired_value:
                external_routes.append(pointer)
            else:
                owned.add(pointer)
            continue
        if current_value != target_value:
            return None, [], (
                f"unowned managed field {pointer} would be overwritten; existing value is preserved"
            )
        owned.add(pointer)
    return owned, external_routes, None


def _semantic_record(
    *,
    destination: Path,
    source_label: str,
    data: bytes,
    target: dict[str, Any],
    desired: dict[str, Any],
    owned: set[str],
) -> dict[str, Any]:
    targets, target_error = _managed_target_items(desired)
    if target_error:
        raise ValueError(target_error)
    managed_paths: dict[str, str] = {}
    for pointer in sorted(owned):
        item = targets.get(pointer)
        if item is None:
            raise ValueError(f"managed path has no current policy target: {pointer}")
        parts, _desired_value = item
        exists, value = _path_value(target, parts)
        if not exists:
            raise ValueError(f"managed target path is missing after reconciliation: {pointer}")
        managed_paths[pointer] = _semantic_hash(value)
    return {
        "path": str(destination),
        "sha256": sha256_bytes(data),
        "source": source_label,
        "mode": OPENCODE_SEMANTIC_MODE,
        _MANAGED_PATHS_FIELD: managed_paths,
    }


def _semantic_metadata_changed(previous: dict[str, Any], new_record: dict[str, Any]) -> bool:
    if previous.get("mode") != OPENCODE_SEMANTIC_MODE:
        return True
    for key in ("path", "source", "mode", _MANAGED_PATHS_FIELD):
        if previous.get(key) != new_record.get(key):
            return True
    return False


def adopt_legacy_opencode_config(
    *,
    destination: Path,
    desired_data: bytes,
    source_label: str,
    manifest: dict[str, Any],
    reporter: Reporter,
    expected_current_sha: str,
    state_dir: Path,
) -> bool:
    """Explicitly adopt a reviewed legacy-drift payload into semantic path ownership."""
    component = "OpenCode config"
    managed = manifest.get("managed_files")
    if not isinstance(managed, dict):
        reporter.add(component, STATE_CONFLICT, "manifest managed_files is not an object")
        return False
    previous = managed.get(component)
    if not isinstance(previous, dict):
        reporter.add(component, STATE_CONFLICT, "explicit adoption requires an existing legacy OpenCode ownership record")
        return False
    if previous.get("mode") not in _LEGACY_MANAGED_MODES:
        reporter.add(
            component,
            STATE_CONFLICT,
            f"explicit adoption is only valid for legacy whole-file ownership; current mode={previous.get('mode')!r}",
        )
        return False
    if previous.get("path") != str(destination):
        reporter.add(
            component,
            STATE_CONFLICT,
            f"manifest points to a different destination: recorded={previous.get('path')}; desired={destination}",
        )
        return False
    if _SHA256_RE.fullmatch(expected_current_sha) is None:
        reporter.add(component, STATE_CONFLICT, "expected current sha256 must be exactly 64 lowercase hex characters")
        return False
    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current_data = destination.read_bytes()
    current_hash = sha256_bytes(current_data)
    if current_hash != expected_current_sha:
        reporter.add(
            component,
            STATE_CONFLICT,
            f"explicit adoption hash mismatch: expected_sha256={expected_current_sha}; current_sha256={current_hash}; file preserved",
        )
        return False

    recorded = previous.get("sha256")
    if not isinstance(recorded, str) or _SHA256_RE.fullmatch(recorded) is None:
        reporter.add(component, STATE_CONFLICT, "legacy whole-file ownership does not contain a valid recorded sha256")
        return False
    if recorded == current_hash:
        reporter.add(
            component,
            STATE_CONFLICT,
            "legacy whole-file hash already matches current config; use ordinary toolchainctl apply instead of adoption",
        )
        return False

    desired_data = _preserve_exact_external_reference(destination, desired_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if desired_error or desired is None:
        reporter.add(component, STATE_CONFLICT, desired_error or "template cannot be parsed")
        return False
    existing, parse_error, has_jsonc_features = parse_jsonc_object(current_data)
    if parse_error or existing is None:
        reporter.add(component, STATE_CONFLICT, parse_error or "existing config cannot be parsed")
        return False

    target, target_error = preview_opencode_config_target(
        destination=destination,
        desired_data=desired_data,
        previous=None,
    )
    if target_error or target is None:
        reporter.add(component, STATE_CONFLICT, target_error or "explicit adoption target cannot be built")
        return False

    owned, external_routes, ownership_error = _ownership_plan(
        existing=existing,
        target=target,
        desired=desired,
        previous=None,
        current_hash=current_hash,
        force=False,
    )
    if ownership_error or owned is None:
        reporter.add(component, STATE_CONFLICT, ownership_error or "semantic ownership plan cannot be built")
        return False

    semantic_change = target != existing
    if semantic_change and has_jsonc_features:
        reporter.add(
            component,
            STATE_CONFLICT,
            "explicit adoption needs semantic JSONC changes but current config contains comments/trailing commas; "
            "file preserved to avoid formatting loss",
        )
        return False

    target_data = (
        (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if semantic_change
        else current_data
    )
    try:
        new_record = _semantic_record(
            destination=destination,
            source_label=source_label,
            data=target_data,
            target=target,
            desired=desired,
            owned=owned,
        )
    except ValueError as exc:
        reporter.add(component, STATE_CONFLICT, str(exc))
        return False

    try:
        backup = backup_file(destination, state_dir, component)
        if semantic_change:
            atomic_write(destination, target_data)
        managed[component] = new_record
    except OSError as exc:
        reporter.add(component, STATE_FAILED, f"explicit adoption failed while persisting OpenCode config: {exc}")
        return False

    detail = (
        f"legacy drift explicitly adopted from exact current sha256={current_hash}; "
        f"semantic ownership записан для {len(owned)} path(s); backup: {backup}"
    )
    if semantic_change:
        detail += "; managed target applied"
    if external_routes:
        detail += f"; внешние routing override сохранены: {len(external_routes)}"
    reporter.add(component, STATE_CONFIGURED, detail)
    return True

def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    component = "OpenCode config"
    managed = manifest["managed_files"]
    previous = managed.get(component)
    if previous is not None and not isinstance(previous, dict):
        reporter.add(component, STATE_CONFLICT, "manifest record is not an object")
        return False
    if isinstance(previous, dict) and previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest указывает на другой путь")
        return False

    desired_data = _preserve_exact_external_reference(destination, desired_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if desired_error or desired is None:
        reporter.add(component, STATE_CONFLICT, desired_error or "template cannot be parsed")
        return False
    desired_targets, target_schema_error = _managed_target_items(desired)
    if target_schema_error:
        reporter.add(component, STATE_CONFLICT, target_schema_error)
        return False

    if not destination.exists():
        if check:
            reporter.add(
                component,
                STATE_MISSING,
                "global OpenCode config отсутствует; обычный apply создаст managed routing и RouterAI provider",
            )
            return False
        target = desired
        payload = (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            record = _semantic_record(
                destination=destination,
                source_label=source_label,
                data=payload,
                target=target,
                desired=desired,
                owned=set(desired_targets),
            )
        except ValueError as exc:
            reporter.add(component, STATE_CONFLICT, str(exc))
            return False
        try:
            atomic_write(destination, payload)
            managed[component] = record
        except OSError as exc:
            reporter.add(component, STATE_FAILED, f"не удалось создать managed OpenCode config: {exc}")
            return False
        reporter.add(component, STATE_CONFIGURED, f"создан managed config: {destination}")
        return True

    if not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"destination is not a regular file: {destination}")
        return False

    current_data = destination.read_bytes()
    current_hash = sha256_bytes(current_data)
    existing, parse_error, has_jsonc_features = parse_jsonc_object(current_data)
    if parse_error or existing is None:
        reporter.add(component, STATE_CONFLICT, parse_error or "existing config cannot be parsed")
        return False

    target, target_error = preview_opencode_config_target(
        destination=destination,
        desired_data=desired_data,
        previous=previous,
    )
    if target_error or target is None:
        reporter.add(component, STATE_CONFLICT, target_error or "managed target cannot be built")
        return False

    owned, external_routes, ownership_error = _ownership_plan(
        existing=existing,
        target=target,
        desired=desired,
        previous=previous,
        current_hash=current_hash,
        force=force,
    )
    if ownership_error or owned is None:
        reporter.add(component, STATE_CONFLICT, ownership_error or "managed ownership cannot be proven")
        return False

    semantic_change = target != existing
    if semantic_change and has_jsonc_features:
        reporter.add(
            component,
            STATE_CONFLICT,
            "управляемый JSONC содержит comments/trailing commas и требует semantic change; "
            "файл сохранён без потери форматирования",
        )
        return False

    target_data = (
        (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if semantic_change
        else current_data
    )
    try:
        new_record = _semantic_record(
            destination=destination,
            source_label=source_label,
            data=target_data,
            target=target,
            desired=desired,
            owned=owned,
        )
    except ValueError as exc:
        reporter.add(component, STATE_CONFLICT, str(exc))
        return False

    metadata_change = (
        (isinstance(previous, dict) and _semantic_metadata_changed(previous, new_record))
        or (previous is None and bool(owned))
    )
    legacy_migration = isinstance(previous, dict) and previous.get("mode") in _LEGACY_MANAGED_MODES

    if check:
        if semantic_change or metadata_change:
            detail = "обычный apply выполнит безопасный semantic merge"
            if legacy_migration:
                detail += " и мигрирует whole-file ownership в semantic path ownership"
            if external_routes:
                detail += f"; внешние routing override сохранены: {len(external_routes)}"
            reporter.add(component, STATE_OUTDATED, detail)
        else:
            detail = "managed semantic paths актуальны; user settings сохранены"
            if external_routes:
                detail += f"; внешние routing override сохранены: {len(external_routes)}"
            reporter.add(component, STATE_OK, detail)
        return False

    if not semantic_change and not metadata_change:
        detail = "managed semantic paths актуальны; user settings сохранены"
        if external_routes:
            detail += f"; внешние routing override сохранены: {len(external_routes)}"
        reporter.add(component, STATE_OK, detail)
        return False

    backup: Path | None = None
    try:
        if semantic_change:
            backup = backup_file(destination, state_dir, component)
            atomic_write(destination, target_data)
        managed[component] = new_record
    except OSError as exc:
        reporter.add(component, STATE_FAILED, f"не удалось сохранить managed OpenCode config: {exc}")
        return False

    detail = "semantic ownership записан; user settings сохранены"
    if legacy_migration:
        detail = "whole-file ownership мигрирован в semantic path ownership; user settings сохранены"
    if semantic_change and backup is not None:
        detail += f"; backup: {backup}"
    if external_routes:
        detail += f"; внешние routing override сохранены: {len(external_routes)}"
    reporter.add(component, STATE_CONFIGURED, detail)
    return True
