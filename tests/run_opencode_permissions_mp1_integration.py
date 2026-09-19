#!/usr/bin/env python3
"""MP-1 exact-artifact integration against a pinned opencode_permissions commit."""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_opencode_permissions_pilot as pilot  # noqa: E402

SOURCE_COMMIT = "eb808259607490aab2971c557501f7222bd46646"
VERSION = "1.18.29"
PILOT_SEGMENT = "sha256-fe0587a7c2dea7756bc1e697aa17a79f0c52be45bd55ac145efd7b473badce42"
NATIVE_SEGMENT = "sha256-b38090e07008fb174607aa2a924cfef1dd26d03bdb339a379b1a770397a8ad84"
RAW_ROOT = f"https://raw.githubusercontent.com/dilukhin/opencode_permissions/{SOURCE_COMMIT}"


def download(relative: str, destination: Path) -> None:
    request = urllib.request.Request(
        f"{RAW_ROOT}/{relative}",
        headers={"User-Agent": "agent-toolchain-mp1-integration"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def materialize_exact_artifacts(root: Path) -> tuple[Path, Path]:
    bundle = root / PILOT_SEGMENT
    native = root / NATIVE_SEGMENT

    pilot_manifest_rel = f"dist/pilot/{PILOT_SEGMENT}/manifest.json"
    download(pilot_manifest_rel, bundle / "manifest.json")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        download(f"dist/pilot/{PILOT_SEGMENT}/{item['path']}", bundle / item["path"])

    native_manifest_rel = f"dist/opencode/{NATIVE_SEGMENT}/manifest.json"
    download(native_manifest_rel, native / "manifest.json")
    native_manifest = json.loads((native / "manifest.json").read_text(encoding="utf-8"))
    relative = native_manifest["output"]["relative_path"]
    download(f"dist/opencode/{NATIVE_SEGMENT}/{relative}", native / relative)
    return bundle, native


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bundle, native = materialize_exact_artifacts(root / "source")

        validation = pilot.validate_artifacts(
            pilot_bundle_dir=bundle,
            native_artifact_dir=native,
            installed_version=VERSION,
        )
        assert validation["pilot_artifact_id"] == "sha256:" + PILOT_SEGMENT.removeprefix("sha256-")
        assert validation["native_artifact_id"] == "sha256:" + NATIVE_SEGMENT.removeprefix("sha256-")

        managed = root / "managed"
        config = managed / "config"
        data = managed / "data"
        state = managed / "state"
        config.mkdir(parents=True)
        original = {
            "model": "synthetic/mp1",
            "permission": {"bash": {"legacy synthetic rule": "ask"}},
        }
        (config / "opencode.jsonc").write_bytes(pilot._pretty_json(original))

        first = pilot.apply_pilot(
            pilot_bundle_dir=bundle,
            native_artifact_dir=native,
            installed_version=VERSION,
            config_dir=config,
            data_dir=data,
            state_dir=state,
        )
        assert first["effective_readback"] == "PASS"
        assert first["changed"] is True
        runtime = Path(first["runtime_dir"])
        assert runtime.name == PILOT_SEGMENT
        assert runtime != bundle
        assert (runtime / "bridge.js").read_bytes() == (bundle / "bridge.js").read_bytes()

        repeat = pilot.apply_pilot(
            pilot_bundle_dir=bundle,
            native_artifact_dir=native,
            installed_version=VERSION,
            config_dir=config,
            data_dir=data,
            state_dir=state,
        )
        assert repeat["changed"] is False
        assert repeat["effective_readback"] == "PASS"

        disabled = pilot.disable_pilot(
            pilot_bundle_dir=bundle,
            native_artifact_dir=native,
            installed_version=VERSION,
            config_dir=config,
            data_dir=data,
            state_dir=state,
        )
        assert disabled["changed"] is True
        assert disabled["runtime_cached"] is True
        restored = json.loads((config / "opencode.jsonc").read_text(encoding="utf-8"))
        assert restored == original
        assert not (config / "plugins" / pilot.PLUGIN_NAME).exists()

        mismatch_root = root / "mismatch"
        try:
            pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.30",
                config_dir=mismatch_root / "config",
                data_dir=mismatch_root / "data",
                state_dir=mismatch_root / "state",
            )
        except pilot.PilotDeploymentError as exc:
            assert exc.code == "INSTALLED_VERSION_MISMATCH"
        else:
            raise AssertionError("version mismatch unexpectedly deployed")
        assert not mismatch_root.exists()

        conflict_root = root / "unknown-plugin"
        unknown = conflict_root / "config" / "plugins" / pilot.PLUGIN_NAME
        unknown.parent.mkdir(parents=True)
        unknown.write_text("user-owned plugin\n", encoding="utf-8")
        before = unknown.read_bytes()
        try:
            pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version=VERSION,
                config_dir=conflict_root / "config",
                data_dir=conflict_root / "data",
                state_dir=conflict_root / "state",
            )
        except pilot.PilotDeploymentError as exc:
            assert exc.code == "UNKNOWN_PLUGIN_CONFLICT"
        else:
            raise AssertionError("unknown plugin unexpectedly overwritten")
        assert unknown.read_bytes() == before
        assert not (conflict_root / "data").exists()
        assert not (conflict_root / "state").exists()

    print(
        "MP1_EXACT_ARTIFACT_INTEGRATION_PASS "
        f"opencode={VERSION} pilot={PILOT_SEGMENT} native={NATIVE_SEGMENT} source={SOURCE_COMMIT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
