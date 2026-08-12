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
    return merge_error is None and merged is not None and merged != existing


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
            "existing config has another provider but contains comments/trailing commas; preserved to avoid formatting loss",
        )
        return False

    merged = copy.deepcopy(existing)
    merged["provider"]["routerai"] = copy.deepcopy(desired_router)
    if "$schema" not in merged and "$schema" in desired:
        merged["$schema"] = copy.deepcopy(desired["$schema"])

    merged_data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    reporter.add(
        "OpenCode config",
        STATE_OUTDATED,
        "existing providers preserved; RouterAI can be added as a sibling provider",
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
    reporter.add("OpenCode config migration", STATE_OK, f"existing providers preserved; backup: {backup}")
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
            "managed JSONC contains comments/trailing commas and needs semantic changes; preserved to avoid formatting loss",
        )
        return False
    if destination.is_file() and previous and previous.get("mode") not in {"merged-json", _SIBLING_MODE}:
        current = destination.read_bytes()
        if previous.get("path") == str(destination) and previous.get("sha256") == sha256_bytes(current):
            if current == desired_data:
                reporter.add("OpenCode config", STATE_OK, str(destination))
                return False
    return _reconcile_opencode_config(
        destination=destination,
        desired_data=desired_data,
        source_label=source_label,
        manifest=manifest,
        reporter=reporter,
        check=check,
        force=force,
        state_dir=state_dir,
    )
