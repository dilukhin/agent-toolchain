"""Read-only external CLI update advisories and their disposable cache."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from setup_inventory import ExternalCliInventory, common_external_cli_inventory
from setup_lib import atomic_write, run

CACHE_SCHEMA = 1
DEFAULT_TTL = 24 * 60 * 60


def cache_path() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_UPDATE_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "cache" / "external-tool-updates.json"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "agent-toolchain" / "external-tool-updates.json"


def load_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != CACHE_SCHEMA or not isinstance(data.get("tools"), dict):
            return {"schema": CACHE_SCHEMA, "tools": {}}
        return data
    except (OSError, ValueError, TypeError):
        return {"schema": CACHE_SCHEMA, "tools": {}}


def cache_fresh(record: dict[str, Any], now: float | None = None, ttl: int = DEFAULT_TTL) -> bool:
    try:
        current = time.time() if now is None else now
        return current - float(record["checked_at"]) < ttl
    except (KeyError, TypeError, ValueError):
        return False


def _latest(item: ExternalCliInventory, timeout: float) -> tuple[str | None, str | None]:
    if not item.active or item.conflict:
        return None, "provider conflict; lookup suppressed"
    provider = item.active.provider
    if provider == "npm" and item.active.package:
        try:
            cp = run([shutil.which("npm") or "npm", "view", item.active.package, "version", "--json"], timeout=timeout)
        except (TimeoutError, subprocess.TimeoutExpired):
            return None, "npm lookup timed out"
        if cp.returncode == 0:
            try:
                return str(json.loads(cp.stdout)), None
            except (ValueError, TypeError):
                return None, "npm returned malformed version"
        return None, (cp.stderr or "npm lookup failed").strip()[-240:]
    if provider == "chocolatey" and shutil.which("choco"):
        try:
            cp = run(["choco", "outdated", "--limit-output", "--exact", item.spec.command], timeout=timeout)
        except (TimeoutError, subprocess.TimeoutExpired):
            return None, "choco lookup timed out"
        if cp.returncode == 0:
            for line in cp.stdout.splitlines():
                fields = line.split("|")
                if len(fields) >= 3 and fields[0].lower() == item.spec.command.lower():
                    return fields[2], None
            return item.active.version, None
        return None, (cp.stderr or "choco lookup failed").strip()[-240:]
    return None, "no safe provider-native lookup available"


def refresh(*, path: Path | None = None, timeout: float = 3.0) -> dict[str, Any]:
    # setup_lib.run is intentionally kept as the bounded adapter seam for tests and callers.
    result: dict[str, Any] = {"schema": CACHE_SCHEMA, "tools": {}}
    now = time.time()
    for name, inventory in common_external_cli_inventory().items():
        installed = inventory.active.version if inventory.active else None
        latest, error = _latest(inventory, timeout)
        result["tools"][name] = {
            "tool": name, "provider": inventory.active.provider if inventory.active else "unknown",
            "installed_version": installed, "latest_version": latest,
            "checked_at": now, "status": "ok" if not error else "error",
            "error": error, "advice": inventory.update_advice if not error else None,
        }
    atomic_write(path or cache_path(), (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return result


def advisory(inventory: ExternalCliInventory, record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    if record.get("status") != "ok" or not record.get("latest_version"):
        return "update advisory unavailable: " + str(record.get("error") or "unknown error")
    if record.get("latest_version") != record.get("installed_version"):
        if inventory.conflict:
            return f"{inventory.spec.display_name or inventory.spec.command}: provider conflict; automatic update advice suppressed"
        return f"{inventory.spec.display_name or inventory.spec.command}: update available {record['installed_version']} -> {record['latest_version']}; {record.get('advice', '')}"
    return None
