from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_apply_promotes_owned_bin_only_for_exact_legacy_shadow(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data = root / "data"
            public_bin = root / "managed-bin"
            state = root / "state"
            path_manifest = {
                "managed_path_entries": {
                    "agent-toolchain-bin": {
                        "owner": "agent-toolchain",
                        "scope": "user",
                        "path": str(public_bin),
                    }
                }
            }
            legacy_yc = legacy / "bin" / "yc.cmd"
            managed_yc = public_bin / "yc.cmd"

            shadow = ycsetup.YcGuardDeploymentError("YC_PATH_SHADOW_CONFLICT", str(legacy / "bin"))
            with mock.patch.object(ycsetup, "preflight_effective_path", side_effect=[shadow, {"ok": True}]), \
                    mock.patch.object(ycsetup.shutil, "which", side_effect=[str(legacy_yc), str(legacy_yc), str(managed_yc)]), \
                    mock.patch.object(ycsetup.setup_path, "promote_owned_public_bin_before", return_value=True) as promote:
                result = ycsetup.apply_guard(
                    artifact_dir=artifact,
                    legacy_guard_root=legacy,
                    entry_script_source=ROOT / "yc_transitional_entry.py",
                    data_dir=data,
                    bin_dir=public_bin,
                    state_dir=state,
                    require_effective_path=True,
                    path_manifest=path_manifest,
                )

            self.assertTrue(result["changed"])
            self.assertTrue(result["path_promoted"])
            self.assertEqual(result["effective_readback"], "PASS")
            promote.assert_called_once_with(path_manifest, (legacy / "bin").resolve())

    def test_apply_does_not_promote_unrelated_shadow(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            public_bin = root / "managed-bin"
            other = root / "other" / "yc.cmd"
            path_manifest = {
                "managed_path_entries": {
                    "agent-toolchain-bin": {
                        "owner": "agent-toolchain",
                        "scope": "user",
                        "path": str(public_bin),
                    }
                }
            }
            shadow = ycsetup.YcGuardDeploymentError("YC_PATH_SHADOW_CONFLICT", str(other.parent))
            with mock.patch.object(ycsetup, "preflight_effective_path", side_effect=shadow), \
                    mock.patch.object(ycsetup.shutil, "which", return_value=str(other)), \
                    mock.patch.object(ycsetup.setup_path, "promote_owned_public_bin_before") as promote:
                with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                    ycsetup.apply_guard(
                        artifact_dir=artifact,
                        legacy_guard_root=legacy,
                        entry_script_source=ROOT / "yc_transitional_entry.py",
                        data_dir=root / "data",
                        bin_dir=public_bin,
                        state_dir=root / "state",
                        require_effective_path=True,
                        path_manifest=path_manifest,
                    )
            self.assertEqual(ctx.exception.code, "YC_PATH_SHADOW_NOT_LEGACY_GUARD")
            promote.assert_not_called()

    def test_apply_maps_unprovable_path_promotion_to_fail_closed_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            public_bin = root / "managed-bin"
            path_manifest = {"managed_path_entries": {}}
            legacy_yc = legacy / "bin" / "yc.cmd"
            shadow = ycsetup.YcGuardDeploymentError("YC_PATH_SHADOW_CONFLICT", str(legacy / "bin"))
            with mock.patch.object(ycsetup, "preflight_effective_path", side_effect=shadow), \
                    mock.patch.object(ycsetup.shutil, "which", return_value=str(legacy_yc)), \
                    mock.patch.object(
                        ycsetup.setup_path,
                        "promote_owned_public_bin_before",
                        side_effect=ycsetup.setup_path.PathOwnershipError("unowned"),
                    ):
                with self.assertRaises(ycsetup.YcGuardDeploymentError) as ctx:
                    ycsetup.apply_guard(
                        artifact_dir=artifact,
                        legacy_guard_root=legacy,
                        entry_script_source=ROOT / "yc_transitional_entry.py",
                        data_dir=root / "data",
                        bin_dir=public_bin,
                        state_dir=root / "state",
                        require_effective_path=True,
                        path_manifest=path_manifest,
                    )
            self.assertEqual(ctx.exception.code, "YC_PATH_PROMOTION_UNSAFE")

    def test_check_active_guard_fails_closed_on_effective_legacy_shadow_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data, public_bin, state = root / "data", root / "managed-bin", root / "state"
            ycsetup.apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=data,
                bin_dir=public_bin,
                state_dir=state,
                require_effective_path=False,
            )
            legacy_bin = legacy / "bin"
            legacy_bin.mkdir()
            legacy_yc = legacy_bin / "yc.cmd"
            legacy_yc.write_text("@echo off\n", encoding="utf-8")
            entrypoint = public_bin / "yc.cmd"
            state_path = state / "yc-transitional-guard.json"
            before = (state_path.read_bytes(), entrypoint.read_bytes())
            path_env = os.pathsep.join((str(legacy_bin), str(public_bin)))

            with mock.patch.dict(os.environ, {"PATH": path_env}), \
                    mock.patch.object(ycsetup.shutil, "which", return_value=str(legacy_yc)), \
                    mock.patch.object(ycsetup, "default_legacy_guard_root", return_value=legacy), \
                    mock.patch.object(ycsetup, "default_data_dir", return_value=data), \
                    mock.patch.object(ycsetup, "default_bin_dir", return_value=public_bin), \
                    mock.patch.object(ycsetup, "default_state_dir", return_value=state), \
                    mock.patch("builtins.print"):
                rc = ycsetup.run_cli(mock.Mock(yc_guard_command="check"))

            self.assertEqual(rc, 2)
            self.assertEqual((state_path.read_bytes(), entrypoint.read_bytes()), before)

    def test_inspect_active_guard_confirms_effective_managed_entrypoint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artifact, legacy, _downstream = self.fixture(root)
            data, public_bin, state = root / "data", root / "managed-bin", root / "state"
            ycsetup.apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=data,
                bin_dir=public_bin,
                state_dir=state,
                require_effective_path=False,
            )
            legacy_bin = legacy / "bin"
            legacy_bin.mkdir()
            managed_yc = public_bin / "yc.cmd"
            path_env = os.pathsep.join((str(public_bin), str(legacy_bin)))

            with mock.patch.dict(os.environ, {"PATH": path_env}), \
                    mock.patch.object(ycsetup.shutil, "which", return_value=str(managed_yc)):
                result = ycsetup.inspect_guard(
                    legacy_guard_root=legacy,
                    data_dir=data,
                    bin_dir=public_bin,
                    state_dir=state,
                )

            self.assertTrue(result["current_yc_owned"])
            self.assertTrue(result["effective_yc_owned"])
            self.assertEqual(Path(result["effective_yc"]).resolve(), managed_yc.resolve())

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
