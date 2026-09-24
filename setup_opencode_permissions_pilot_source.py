"""Fetch and retain one pinned P0 source pair for the explicit MP-3 switch."""
from __future__ import annotations

import os
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import setup_opencode_permissions_pilot as pilot


@dataclass(frozen=True)
class SourcePin:
    commit: str
    version: str
    pilot_id: str
    native_id: str
    pilot_files: tuple[str, ...]


CURRENT = SourcePin(
    commit="7922d612f244882aae3d843a64393b1363b593d9",
    version="1.18.29",
    pilot_id="sha256:21582d375823f499a2792824993a7a7510301c8fce2711cd01a25caececdaf88",
    native_id="sha256:b38090e07008fb174607aa2a924cfef1dd26d03bdb339a379b1a770397a8ad84",
    pilot_files=(
        "manifest.json",
        "bridge.js",
        "profile.json",
        "runtime/classifier_analyzers.py",
        "runtime/classifier_core.py",
        "runtime/normalized_operation_identity.py",
        "runtime/opencode_p0_adapter.py",
    ),
)
MAX_FILE_BYTES = 256 * 1024


class SourceConflict(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _segment(artifact_id: str) -> str:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_id):
        raise SourceConflict("SOURCE_PIN_INVALID")
    return "sha256-" + artifact_id.split(":", 1)[1]


def _validate(root: Path, pin: SourcePin) -> tuple[Path, Path]:
    bundle = root / _segment(pin.pilot_id)
    native = root / _segment(pin.native_id)
    try:
        result = pilot.validate_artifacts(
            pilot_bundle_dir=bundle, native_artifact_dir=native,
            installed_version=pin.version, installed_platform="linux",
        )
    except pilot.PilotDeploymentError as exc:
        raise SourceConflict("SOURCE_ARTIFACT_INVALID") from exc
    if result["pilot_artifact_id"] != pin.pilot_id or result["native_artifact_id"] != pin.native_id:
        raise SourceConflict("SOURCE_PIN_MISMATCH")
    return bundle, native


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-toolchain-mp3-source"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise SourceConflict("SOURCE_FILE_TOO_LARGE")
    return data


def materialize_source(
    *,
    cache_root: Path,
    installed_version: str,
    pin: SourcePin = CURRENT,
    fetch=_fetch,
) -> tuple[Path, Path]:
    """No OpenCode config/state mutation; existing unknown cache is preserved."""
    if installed_version != pin.version:
        raise SourceConflict("INSTALLED_VERSION_MISMATCH")
    if not re.fullmatch(r"[0-9a-f]{40}", pin.commit):
        raise SourceConflict("SOURCE_PIN_INVALID")
    pilot_segment = _segment(pin.pilot_id)
    native_segment = _segment(pin.native_id)
    root = Path(cache_root)
    if root.is_symlink():
        raise SourceConflict("SOURCE_CACHE_CONFLICT")
    destination = root / pin.commit
    if destination.exists() or destination.is_symlink():
        if not destination.is_dir() or destination.is_symlink():
            raise SourceConflict("SOURCE_CACHE_CONFLICT")
        return _validate(destination, pin)

    if root.exists() and not root.is_dir():
        raise SourceConflict("SOURCE_CACHE_CONFLICT")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise SourceConflict("SOURCE_CACHE_CONFLICT")
    with tempfile.TemporaryDirectory(prefix=".p0-source-", dir=root) as staging:
        staged = Path(staging)
        base = f"https://raw.githubusercontent.com/dilukhin/opencode_permissions/{pin.commit}"
        try:
            for relative in pin.pilot_files:
                if not relative or any(part in ("", ".", "..") for part in relative.split("/")):
                    raise SourceConflict("SOURCE_PIN_INVALID")
                target = staged / pilot_segment / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fetch(f"{base}/dist/pilot/{pilot_segment}/{relative}"))
            for relative in ("manifest.json", "permission.jsonc"):
                target = staged / native_segment / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fetch(f"{base}/dist/opencode/{native_segment}/{relative}"))
            _validate(staged, pin)
            try:
                os.replace(staged, destination)
            except OSError as exc:
                if not destination.is_dir() or destination.is_symlink():
                    raise SourceConflict("SOURCE_CACHE_PUBLISH_CONFLICT") from exc
                return _validate(destination, pin)
            return destination / pilot_segment, destination / native_segment
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise SourceConflict("SOURCE_FETCH_FAILED") from exc
