"""Compatibility wrappers for migration-specific reconciliation rules."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from setup_lib import (
    Reporter,
    STATE_CONFLICT,
    STATE_OK,
    STATE_OUTDATED,
    atomic_write,
    backup_file,
    merge_routerai_config,
    parse_jsonc_object,
    reconcile_opencode_config as _reconcile_opencode_config,
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


def _preserve_sibling_provider_policy(destination: Path, desired_data: bytes,
                                      previous: dict[str, Any] | None) -> bytes:
    """Do not introduce top-level model defaults after sibling-provider adoption."""
    if not destination.is_file() or not previous or previous.get("mode") != _SIBLING_MODE:
        return desired_data
    existing, error, _ = parse_jsonc_object(destination.read_bytes())
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None:
        return desired_data
    for key in ("model", "small_model"):
        if key not in existing:
            desired.pop(key, None)
    return (json.dumps(desired, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _format_sensitive_change(destination: Path, desired_data: bytes, previous: dict[str, Any] | None) -> bool:
    if not destination.is_file() or not previous or previous.get("mode") not in {"merged-json", _SIBLING_MODE}:
        return False
    existing, error, has_jsonc_features = parse_jsonc_object(destination.read_bytes())
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None or not has_jsonc_features:
        return False
    merged, merge_error = merge_routerai_config(existing, desired)
    if merge_error is not None or merged is None:
        return False
    if "autoupdate" in desired:
        merged["autoupdate"] = copy.deepcopy(desired["autoupdate"])
    return merged != existing


def _reconcile_missing_routerai(*, destination: Path, desired_data: bytes, source_label: str,
                                manifest: dict[str, Any], reporter: Reporter, check: bool,
                                state_dir: Path) -> bool | None:
    if not destination.is_file():
        return None
    previous = manifest.get("managed_files", {}).get("OpenCode config")
    if previous is not None:
        return None

    current_data = destination.read_bytes()
    existing, error, has_jsonc_features = parse_jsonc_object(current_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None:
        return None

    providers = existing.get("provider")
    desired_router = routerai_provider(desired)
    if not isinstance(providers, dict) or "routerai" in providers or desired_router is None:
        return None

    if has_jsonc_features:
        reporter.add(
            "OpenCode config",
            STATE_CONFLICT,
            "существующий config содержит другой provider и JSONC-комментарии/trailing commas; файл сохранён без потери форматирования",
        )
        return False

    merged = copy.deepcopy(existing)
    merged["provider"]["routerai"] = copy.deepcopy(desired_router)
    if "$schema" not in merged and "$schema" in desired:
        merged["$schema"] = copy.deepcopy(desired["$schema"])
    if "autoupdate" in desired:
        merged["autoupdate"] = copy.deepcopy(desired["autoupdate"])

    merged_data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    reporter.add(
        "OpenCode config",
        STATE_OUTDATED,
        "существующие providers сохранены; RouterAI будет добавлен соседним provider, autoupdate переведён в notify",
    )
    if check:
        return False

    backup = backup_file(destination, state_dir, "OpenCode config")
    atomic_write(destination, merged_data)
    manifest["managed_files"]["OpenCode config"] = {
        "path": str(destination),
        "sha256": sha256_bytes(merged_data),
        "source": source_label,
        "mode": _SIBLING_MODE,
    }
    reporter.add("OpenCode config migration", STATE_OK, f"providers сохранены; backup: {backup}")
    return True


def _reconcile_managed_autoupdate(*, destination: Path, desired_data: bytes, source_label: str,
                                  manifest: dict[str, Any], reporter: Reporter, check: bool,
                                  force: bool, state_dir: Path) -> bool | None:
    """Reconcile autoupdate together with RouterAI merge when the managed value differs."""
    if not destination.is_file():
        return None
    current_data = destination.read_bytes()
    existing, error, has_jsonc_features = parse_jsonc_object(current_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None or "autoupdate" not in desired:
        return None
    if existing.get("autoupdate") == desired.get("autoupdate"):
        return None

    component = "OpenCode config"
    managed = manifest["managed_files"]
    previous = managed.get(component)
    current_hash = sha256_bytes(current_data)
    if previous and previous.get("path") != str(destination):
        reporter.add(component, STATE_CONFLICT, "manifest указывает на другой путь")
        return False
    if previous and current_hash != previous.get("sha256") and not force:
        reporter.add(component, STATE_CONFLICT, "управляемый config изменён локально; файл сохранён")
        return False
    if previous and current_hash != previous.get("sha256") and check:
        reporter.add(component, STATE_CONFLICT, "локальное изменение требует отдельного подтверждённого merge; --force сейчас не применяется")
        return False

    merged, merge_error = merge_routerai_config(existing, desired)
    if merge_error or merged is None:
        return None
    merged["autoupdate"] = copy.deepcopy(desired["autoupdate"])
    if merged == existing:
        return None
    if has_jsonc_features:
        reporter.add(component, STATE_CONFLICT,
                     "для смены autoupdate нужен semantic change JSONC с комментариями/trailing commas; файл сохранён")
        return False

    reporter.add(component, STATE_OUTDATED,
                 f"встроенный auto-update будет переведён в режим уведомления: autoupdate={desired['autoupdate']!r}")
    if check:
        return False

    backup = backup_file(destination, state_dir, component)
    merged_data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(destination, merged_data)
    mode = previous.get("mode") if previous and previous.get("mode") in {"merged-json", _SIBLING_MODE} else "merged-json"
    managed[component] = {
        "path": str(destination),
        "sha256": sha256_bytes(merged_data),
        "source": source_label,
        "mode": mode,
    }
    reporter.add("OpenCode autoupdate policy", STATE_OK,
                 f"режим notify применён; дальнейшее обновление выполняется владельцем installation; backup: {backup}")
    return True


def _restore_sibling_mode(manifest: dict[str, Any], previous: dict[str, Any] | None, check: bool) -> bool:
    """Keep sibling adoption semantics across no-op reconciliations."""
    if check or not previous or previous.get("mode") != _SIBLING_MODE:
        return False
    current = manifest.get("managed_files", {}).get("OpenCode config")
    if not isinstance(current, dict) or current.get("mode") == _SIBLING_MODE:
        return False
    current["mode"] = _SIBLING_MODE
    return True


def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    previous = manifest.get("managed_files", {}).get("OpenCode config")
    desired_data = _preserve_exact_external_reference(destination, desired_data)
    desired_data = _preserve_sibling_provider_policy(destination, desired_data, previous)

    missing_routerai = _reconcile_missing_routerai(
        destination=destination,
        desired_data=desired_data,
        source_label=source_label,
        manifest=manifest,
        reporter=reporter,
        check=check,
        state_dir=state_dir,
    )
    if missing_routerai is not None:
        return missing_routerai

    if _format_sensitive_change(destination, desired_data, previous):
        reporter.add(
            "OpenCode config",
            STATE_CONFLICT,
            "управляемый JSONC содержит комментарии/trailing commas и требует semantic change; файл сохранён без потери форматирования",
        )
        return False

    autoupdate = _reconcile_managed_autoupdate(
        destination=destination,
        desired_data=desired_data,
        source_label=source_label,
        manifest=manifest,
        reporter=reporter,
        check=check,
        force=force,
        state_dir=state_dir,
    )
    if autoupdate is not None:
        return autoupdate

    if destination.is_file() and previous and previous.get("mode") not in {"merged-json", _SIBLING_MODE}:
        current = destination.read_bytes()
        if previous.get("path") == str(destination) and previous.get("sha256") == sha256_bytes(current):
            if current == desired_data:
                reporter.add("OpenCode config", STATE_OK, str(destination))
                return False

    changed = _reconcile_opencode_config(
        destination=destination,
        desired_data=desired_data,
        source_label=source_label,
        manifest=manifest,
        reporter=reporter,
        check=check,
        force=force,
        state_dir=state_dir,
    )
    restored_mode = _restore_sibling_mode(manifest, previous, check)
    return changed or restored_mode
