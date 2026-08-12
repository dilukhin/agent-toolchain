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


def can_add_routerai_provider(config: dict[str, Any] | None) -> bool:
    """Return True for a parsed config where RouterAI can be added additively.

    We intentionally accept only an existing ``provider`` object with no ``routerai``
    entry. This keeps foreign providers and all unrelated top-level settings intact,
    while malformed/conflicting ``provider.routerai`` shapes remain conflicts.
    """
    if config is None:
        return False
    providers = config.get("provider")
    return isinstance(providers, dict) and "routerai" not in providers


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


def _reconcile_additive_foreign_provider(*, destination: Path, desired_data: bytes,
                                         source_label: str, manifest: dict[str, Any],
                                         reporter: Reporter, check: bool,
                                         state_dir: Path) -> bool | None:
    """Add RouterAI to an unowned config that already contains other providers.

    Returns ``None`` when the special migration does not apply. The migration is
    deliberately additive: it preserves foreign providers, plugin/permission settings,
    and absent top-level model defaults. JSONC with comments/trailing commas remains a
    conflict because serializing it would destroy user formatting/comments.
    """
    if not destination.is_file():
        return None
    previous = manifest.get("managed_files", {}).get("OpenCode config")
    if previous is not None:
        return None

    existing_data = destination.read_bytes()
    existing, error, has_jsonc_features = parse_jsonc_object(existing_data)
    desired, desired_error, _ = parse_jsonc_object(desired_data)
    if error or desired_error or existing is None or desired is None:
        return None
    if not can_add_routerai_provider(existing):
        return None
    desired_router = routerai_provider(desired)
    if desired_router is None:
        return None

    if has_jsonc_features:
        reporter.add(
            "OpenCode config",
            STATE_CONFLICT,
            "existing config uses other providers but contains comments/trailing commas; preserved to avoid formatting loss",
        )
        return False

    providers = existing.get("provider")
    assert isinstance(providers, dict)
    merged = copy.deepcopy(existing)
    merged_providers = merged["provider"]
    assert isinstance(merged_providers, dict)
    merged_providers["routerai"] = copy.deepcopy(desired_router)
    if "$schema" not in merged and "$schema" in desired:
        merged["$schema"] = desired["$schema"]

    reporter.add(
        "OpenCode config",
        STATE_OUTDATED,
        "existing foreign providers/settings preserved; RouterAI provider can be added additively",
    )
    if check:
        return False

    backup = backup_file(destination, state_dir, "OpenCode config")
    merged_data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write(destination, merged_data)
    manifest["managed_files"]["OpenCode config"] = {
        "path": str(destination),
        "sha256": sha256_bytes(merged_data),
        "source": source_label,
        "mode": "merged-json",
    }
    reporter.add(
        "OpenCode config migration",
        STATE_OK,
        f"added only provider.routerai; foreign providers/settings preserved; backup: {backup}",
    )
    return True


def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    """Reconcile config while preserving generated idempotency and exact file refs."""
    desired_data = _preserve_exact_external_reference(destination, desired_data)
    additive = _reconcile_additive_foreign_provider(
        destination=destination,
        desired_data=desired_data,
        source_label=source_label,
        manifest=manifest,
        reporter=reporter,
        check=check,
        state_dir=state_dir,
    )
    if additive is not None:
        return additive

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
