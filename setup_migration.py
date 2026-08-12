"""Compatibility wrappers for migration-specific reconciliation rules."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from setup_lib import (
    Reporter,
    STATE_CONFLICT,
    STATE_OK,
    merge_routerai_config,
    parse_jsonc_object,
    reconcile_opencode_config as _reconcile_opencode_config,
    routerai_provider,
    sha256_bytes,
)

_FILE_REF_RE = re.compile(r"\{file:(.+)\}")


def _preserve_exact_external_reference(destination: Path, desired_data: bytes) -> bytes:
    """Keep the exact existing ``{file:...}`` string while merging managed fields.

    Path resolution is useful for existence checks and manifest metadata, but Windows
    can canonicalize an 8.3 path (for example ``RUNNER~1``) to a different textual
    long path. A working user config must not be rewritten merely because both forms
    point to the same credential file. If the textual reference already matches, keep
    the original desired bytes so a generated config remains byte-for-byte idempotent.
    """
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


def _format_sensitive_change(destination: Path, desired_data: bytes, previous: dict[str, Any] | None) -> bool:
    """Detect a managed JSONC update that would destroy comments/trailing commas."""
    if not destination.is_file() or not previous or previous.get("mode") != "merged-json":
        return False
    existing, error, has_jsonc_features = parse_jsonc_object(destination.read_bytes())
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None or not has_jsonc_features:
        return False
    merged, merge_error = merge_routerai_config(existing, desired)
    return merge_error is None and merged is not None and merged != existing


def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    """Reconcile config while preserving generated idempotency and exact file refs."""
    desired_data = _preserve_exact_external_reference(destination, desired_data)
    previous = manifest.get("managed_files", {}).get("OpenCode config")
    if _format_sensitive_change(destination, desired_data, previous):
        reporter.add(
            "OpenCode config",
            STATE_CONFLICT,
            "managed JSONC contains comments/trailing commas and needs semantic changes; preserved to avoid formatting loss",
        )
        return False
    if destination.is_file() and previous and previous.get("mode") != "merged-json":
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
