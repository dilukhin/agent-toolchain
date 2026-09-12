from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_yc_transitional_guard as ycsetup  # noqa: E402


def make_artifact(root: Path, *, runtime_adapter: bytes = b"# fixture\n") -> Path:
    temp = root / "artifact-temp"
    files = {
        "policy/yc/yc_policy.v1.json": b'{"schema":"yc-policy/v1","version":"1.0.0","provider":"yandex-cloud"}\n',
        "runtime/yc_transitional_adapter.py": runtime_adapter,
        "runtime/classifier_yc.py": b"# classifier fixture\n",
        "runtime/classifier_core.py": b"# core fixture\n",
        "runtime/normalized_operation_identity.py": b"# identity fixture\n",
    }
    for relative, data in files.items():
        path = temp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    records = [
        {"path": relative, "sha256": ycsetup._sha256_bytes(data), "size": len(data)}
        for relative, data in sorted(files.items())
    ]
    policy_record = next(x for x in records if x["path"] == "policy/yc/yc_policy.v1.json")
    manifest = {
        "schema": 1,
        "artifact_format": ycsetup.ARTIFACT_FORMAT,
        "artifact_id": "",
        "status": "transitional_ready",
        "owner": ycsetup.ARTIFACT_OWNER,
        "target": {"provider": "yandex-cloud", "platform": "windows", "mode": "transitional"},
        "policy": {
            "schema": "yc-policy/v1",
            "version": "1.0.0",
            "relative_path": "policy/yc/yc_policy.v1.json",
            "sha256": policy_record["sha256"],
        },
        "files": records,
        "constraints": {
            "canonical_policy_only": True,
            "setup_semantic_rewrite": False,
            "developer_checkout_dependency": False,
            "hard_deny_terminal": True,
            "unknown_or_opaque_not_allow": True,
            "runtime_path_resolution": False,
            "downstream_executable_identity_required": True,
            "caller_approval_flags_trusted": False,
            "real_executor_packaged": False,
            "transitional_exact_mutation_auto_allow": True,
            "full_authorization_binding_complete": False,
        },
        "transitional_auto_allow_mutations": [
            {
                "service": "compute",
                "resource": "instance",
                "action": "start",
                "resource_id": "epd42hrnss08t2440g90",
                "argv": ["yc", "compute", "instance", "start", "--id", "epd42hrnss08t2440g90"],
            },
            {
                "service": "compute",
                "resource": "instance",
                "action": "stop",
                "resource_id": "epd42hrnss08t2440g90",
                "argv": ["yc", "compute", "instance", "stop", "--id", "epd42hrnss08t2440g90"],
            },
        ],
        "artifact_path_segment": "",
    }
    manifest["artifact_id"] = ycsetup._artifact_id(manifest)
    manifest["artifact_path_segment"] = ycsetup._segment(manifest["artifact_id"])
    final = root / manifest["artifact_path_segment"]
    temp.rename(final)
    (final / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return final


class YcTransitionalDeploymentTests(unittest.TestCase):
    def test_pinned_source_and_artifact_identity_are_exact(self):
        self.assertEqual(ycsetup.SOURCE_REF, "7922d612f244882aae3d843a64393b1363b593d9")
        self.assertEqual(
            ycsetup.EXPECTED_ARTIFACT_ID,
            "sha256:149ca1892d90f493da59d527999bdafbfb16ff4d7d53b3989de4f0e439c5e0ef",
        )

    def fixture(self, root: Path):
        artifact = make_artifact(root)
        legacy = root / "legacy"
        legacy.mkdir()
        downstream = root / "vendor" / "yc.exe"
        downstream.parent.mkdir()
        downstream.write_bytes(b"synthetic-yc-executable")
        legacy_state = {
            "version": "0.1.1",
            "installed_at": "synthetic",
            "runtime": {"underlying_cli": str(downstream)},
        }
        (legacy / "state.json").write_text(json.dumps(legacy_state), encoding="utf-8")
        return artifact, legacy, downstream

    def test_legacy_resolver_is_narrow_and_does_not_search(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _artifact, legacy, downstream = self.fixture(root)
            self.assertEqual(ycsetup.resolve_legacy_downstream(legacy), downstream.resolve())
            state = json.loads((legacy / "state.json").read_text(encoding="utf-8"))
            state["another"] = str(root / "other" / "yc.exe")
            (legacy / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                ycsetup.resolve_legacy_downstream(legacy)
            self.assertEqual(ctx.exception.code, "LEGACY_DOWNSTREAM_NOT_UNIQUE")

    def test_apply_repeat_disable_preserves_legacy_guard(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data = root / "data"
            public_bin = root / "bin"
            state = root / "state"
            legacy_before = {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob("*") if p.is_file()}

            first = ycsetup.apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=data,
                bin_dir=public_bin,
                state_dir=state,
                require_effective_path=False,
            )
            self.assertTrue(first["changed"])
            self.assertEqual(first["effective_readback"], "PASS")
            entrypoint = public_bin / "yc.cmd"
            self.assertTrue(entrypoint.is_file())
            deployment_state = json.loads((state / "yc-transitional-guard.json").read_text(encoding="utf-8"))
            self.assertEqual(deployment_state["phase"], "active")

            second = ycsetup.apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=data,
                bin_dir=public_bin,
                state_dir=state,
                require_effective_path=False,
            )
            self.assertFalse(second["changed"])

            disabled = ycsetup.disable_guard(bin_dir=public_bin, state_dir=state)
            self.assertTrue(disabled["changed"])
            self.assertFalse(entrypoint.exists())
            self.assertEqual(
                {p.relative_to(legacy).as_posix(): p.read_bytes() for p in legacy.rglob("*") if p.is_file()},
                legacy_before,
            )
            self.assertTrue((data / "tools" / "yc-guard").is_dir())

    def test_unknown_public_entrypoint_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data = root / "data"
            public_bin = root / "bin"
            state = root / "state"
            public_bin.mkdir()
            entrypoint = public_bin / "yc.cmd"
            entrypoint.write_text("@echo off\r\necho user-owned\r\n", encoding="utf-8")
            before = entrypoint.read_bytes()
            with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                ycsetup.apply_guard(
                    artifact_dir=artifact,
                    legacy_guard_root=legacy,
                    entry_script_source=ROOT / "yc_transitional_entry.py",
                    data_dir=data,
                    bin_dir=public_bin,
                    state_dir=state,
                    require_effective_path=False,
                )
            self.assertEqual(ctx.exception.code, "YC_ENTRYPOINT_UNKNOWN_CONFLICT")
            self.assertEqual(entrypoint.read_bytes(), before)
            self.assertFalse((data / "tools" / "yc-guard").exists())

    def test_modified_owned_entrypoint_blocks_disable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data, public_bin, state = root / "data", root / "bin", root / "state"
            ycsetup.apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=data,
                bin_dir=public_bin,
                state_dir=state,
                require_effective_path=False,
            )
            entrypoint = public_bin / "yc.cmd"
            entrypoint.write_text("modified\n", encoding="utf-8")
            with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                ycsetup.disable_guard(bin_dir=public_bin, state_dir=state)
            self.assertEqual(ctx.exception.code, "YC_ENTRYPOINT_MODIFIED")
            self.assertTrue(entrypoint.exists())

    def test_artifact_tamper_fails_before_entrypoint_publish(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            (artifact / "runtime" / "classifier_core.py").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                ycsetup.apply_guard(
                    artifact_dir=artifact,
                    legacy_guard_root=legacy,
                    entry_script_source=ROOT / "yc_transitional_entry.py",
                    data_dir=root / "data",
                    bin_dir=root / "bin",
                    state_dir=root / "state",
                    require_effective_path=False,
                )
            self.assertEqual(ctx.exception.code, "YC_ARTIFACT_FILE_SIZE_MISMATCH")
            self.assertFalse((root / "bin" / "yc.cmd").exists())

    def test_path_shadow_conflict_is_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "legacy-bin"
            managed = root / "managed-bin"
            first.mkdir()
            managed.mkdir()
            path_env = os.pathsep.join((str(first), str(managed)))
            with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                ycsetup.preflight_effective_path(
                    managed,
                    current_yc=str(first / "yc.cmd"),
                    path_env=path_env,
                )
            self.assertEqual(ctx.exception.code, "YC_PATH_SHADOW_CONFLICT")


if __name__ == "__main__":
    unittest.main()
