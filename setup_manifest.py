"""Ownership manifest schema, validation and nondestructive migration."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = 2
LEGACY_MANIFEST_SCHEMA = 1


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".opencode-setup.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def empty_manifest() -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "managed_files": {},
        "credentials": {},
        "managed_tools": {},
        "managed_path_entries": {},
    }


def _validate_mapping(data: dict[str, Any], key: str, *, required: bool) -> str | None:
    if key not in data:
        return f"manifest field {key!r} is missing" if required else None
    if not isinstance(data[key], dict):
        return f"manifest field {key!r} must be an object"
    return None


def _validate_v1(data: dict[str, Any]) -> str | None:
    error = _validate_mapping(data, "managed_files", required=True)
    if error:
        return error
    if "credentials" in data and not isinstance(data["credentials"], dict):
        return "manifest field 'credentials' must be an object"
    for reserved in ("managed_tools", "managed_path_entries"):
        if reserved in data and not isinstance(data[reserved], dict):
            return f"legacy manifest field {reserved!r} has unsupported type"
    return None


def _validate_v2(data: dict[str, Any]) -> str | None:
    for key in ("managed_files", "credentials", "managed_tools", "managed_path_entries"):
        error = _validate_mapping(data, key, required=True)
        if error:
            return error
    return None


def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Return a v2 copy without mutating the caller's legacy manifest."""
    migrated = copy.deepcopy(data)
    migrated["schema"] = MANIFEST_SCHEMA
    migrated.setdefault("credentials", {})
    migrated.setdefault("managed_tools", {})
    migrated.setdefault("managed_path_entries", {})
    return migrated


def load_manifest(path: Path) -> tuple[dict[str, Any], str | None, bool]:
    """Load manifest and migrate v1 in memory.

    Returns ``(manifest, error, migration_pending)``. Loading never writes files, so
    callers can preserve strict read-only check semantics and decide when to persist a
    successful migration.
    """
    if not path.exists():
        return empty_manifest(), None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return empty_manifest(), f"manifest is unreadable: {exc}", False
    if not isinstance(data, dict):
        return empty_manifest(), "manifest root must be an object", False

    schema = data.get("schema")
    if schema == MANIFEST_SCHEMA:
        error = _validate_v2(data)
        return (data, error, False) if error else (data, None, False)
    if schema == LEGACY_MANIFEST_SCHEMA:
        error = _validate_v1(data)
        if error:
            return empty_manifest(), error, False
        migrated = migrate_v1_to_v2(data)
        error = _validate_v2(migrated)
        return (empty_manifest(), error, False) if error else (migrated, None, True)
    return empty_manifest(), f"manifest schema is unsupported: {schema!r}", False


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    error = _validate_v2(manifest)
    if error:
        raise ValueError(error)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, payload.encode("utf-8"))
