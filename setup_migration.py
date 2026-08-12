"""Compatibility wrappers for migration-specific reconciliation rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from setup_lib import (
    Reporter,
    STATE_OK,
    reconcile_opencode_config as _reconcile_opencode_config,
    sha256_bytes,
)


def reconcile_opencode_config(*, destination: Path, desired_data: bytes, source_label: str,
                              manifest: dict[str, Any], reporter: Reporter, check: bool,
                              force: bool, state_dir: Path) -> bool:
    """Keep exact generated configs in ordinary file-ownership mode on repeat runs.

    The general migration reconciler upgrades compatible pre-existing user configs to
    ``merged-json`` ownership. A config created by opencode_setup itself is already an
    exact managed file and must remain byte-for-byte stable instead of changing only
    manifest metadata on the second run.
    """
    previous = manifest.get("managed_files", {}).get("OpenCode config")
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
