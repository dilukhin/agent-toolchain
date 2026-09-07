from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_opencode_permissions_pilot as pilot  # noqa: E402


class PilotDeploymentTests(unittest.TestCase):
    def make_artifacts(self, root: Path, version: str = "1.18.29"):
        native_tmp = root / "native-tmp"
        native_tmp.mkdir()
        permission = {
            "permission": {
                "*": {"*": "ask"},
                "read": {"*": "allow", ".env": "deny"},
                "bash": {"pwd": "allow", "rm *": "deny"},
            }
        }
        permission_bytes = pilot._pretty_json(permission)
        (native_tmp / "permission.jsonc").write_bytes(permission_bytes)
        native_manifest = {
            "schema": 1,
            "artifact_format": pilot.NATIVE_FORMAT,
            "artifact_id": "",
            "status": "deployable",
            "owner": pilot.PILOT_OWNER,
            "target": {
                "product": "opencode",
                "exact_version": version,
                "platform": "linux",
                "compatibility_profile_id": "opencode-test-profile",
            },
            "policy_source": {
                "path": "policy/native/rules.v1.json",
                "sha256": "1" * 64,
            },
            "renderer": {"id": "opencode-v1-permission-renderer", "version": 1},
            "output": {
                "relative_path": "permission.jsonc",
                "sha256": pilot._sha256_bytes(permission_bytes),
            },
            "constraints": {
                "exact_version_only": True,
                "requires_deployable_profile": True,
                "nearest_version_fallback": False,
                "setup_semantic_rewrite": False,
                "effective_readback_required": True,
                "competing_effective_layer_result": "CONFLICT",
            },
            "artifact_path_segment": "",
        }
        native_manifest["artifact_id"] = pilot._native_artifact_id(native_manifest)
        native_manifest["artifact_path_segment"] = pilot._artifact_segment(
            native_manifest["artifact_id"], "x"
        )
        native_dir = root / native_manifest["artifact_path_segment"]
        native_tmp.rename(native_dir)
        (native_dir / "manifest.json").write_bytes(pilot._pretty_json(native_manifest))

        bundle_tmp = root / "bundle-tmp"
        (bundle_tmp / "runtime").mkdir(parents=True)
        bridge = b'export const OpenCodePermissionsP0 = async () => ({})\n'
        profile = {
            "classifier_profile_id": "p0-test",
            "target": {
                "opencode_version": version,
                "compatibility_profile_id": "opencode-test-profile",
                "native_policy_artifact_id": native_manifest["artifact_id"],
            },
        }
        files = {
            "bridge.js": bridge,
            "profile.json": pilot._pretty_json(profile),
            "runtime/opencode_p0_adapter.py": b'print("fixture")\n',
        }
        for relative, data in files.items():
            path = bundle_tmp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        pilot_manifest = {
            "schema": 1,
            "artifact_format": pilot.PILOT_FORMAT,
            "artifact_id": "",
            "status": "mp0_ready",
            "owner": pilot.PILOT_OWNER,
            "target": {
                "product": "opencode",
                "exact_version": version,
                "platform": "linux",
                "compatibility_profile_id": "opencode-test-profile",
                "compatibility_family_id": "sha256:" + "2" * 64,
            },
            "native_policy_artifact_id": native_manifest["artifact_id"],
            "classifier_profile": {
                "id": "p0-test",
                "relative_path": "profile.json",
                "sha256": pilot._sha256_bytes(files["profile.json"]),
            },
            "files": [
                {
                    "path": relative,
                    "sha256": pilot._sha256_bytes(data),
                    "size": len(data),
                }
                for relative, data in sorted(files.items())
            ],
            "constraints": {
                "exact_version_only": True,
                "requires_deployable_profile": True,
                "nearest_version_fallback": False,
                "setup_semantic_rewrite": False,
                "managed_global_plugin_required": True,
                "developer_checkout_dependency": False,
                "auditor_enabled": False,
                "workspace_trust_enabled": False,
                "state_changing_classifier_enabled": False,
                "classifier_error_result": "ASK_USER",
                "competing_effective_layer_result": "CONFLICT",
                "runtime_bundle_digest_check": True,
            },
            "artifact_path_segment": "",
        }
        pilot_manifest["artifact_id"] = pilot._pilot_artifact_id(pilot_manifest)
        pilot_manifest["artifact_path_segment"] = pilot._artifact_segment(
            pilot_manifest["artifact_id"], "x"
        )
        bundle_dir = root / pilot_manifest["artifact_path_segment"]
        bundle_tmp.rename(bundle_dir)
        (bundle_dir / "manifest.json").write_bytes(pilot._pretty_json(pilot_manifest))
        return bundle_dir, native_dir, permission["permission"]

    def paths(self, root: Path):
        return root / "config", root / "data", root / "state"

    def test_apply_readback_repeat_noop_and_disable_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, desired_permission = self.make_artifacts(root)
            config, data, state = self.paths(root)
            config.mkdir()
            original = {
                "model": "synthetic/model",
                "permission": {"bash": {"legacy": "ask"}},
            }
            (config / "opencode.jsonc").write_bytes(pilot._pretty_json(original))

            first = pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.29",
                config_dir=config,
                data_dir=data,
                state_dir=state,
            )
            self.assertEqual(first["effective_readback"], "PASS")
            self.assertTrue(first["changed"])
            applied = json.loads((config / "opencode.jsonc").read_text())
            self.assertEqual(applied["model"], original["model"])
            self.assertEqual(applied["permission"], desired_permission)

            second = pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.29",
                config_dir=config,
                data_dir=data,
                state_dir=state,
            )
            self.assertFalse(second["changed"])

            disabled = pilot.disable_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.29",
                config_dir=config,
                data_dir=data,
                state_dir=state,
            )
            self.assertTrue(disabled["changed"])
            restored = json.loads((config / "opencode.jsonc").read_text())
            self.assertEqual(restored, original)
            self.assertTrue(disabled["runtime_cached"])
            self.assertFalse((config / "plugins" / pilot.PLUGIN_NAME).exists())

    def test_version_mismatch_is_fail_closed_before_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = self.make_artifacts(root)
            config, data, state = self.paths(root)
            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.apply_pilot(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.30",
                    config_dir=config,
                    data_dir=data,
                    state_dir=state,
                )
            self.assertEqual(ctx.exception.code, "INSTALLED_VERSION_MISMATCH")
            self.assertFalse(config.exists())
            self.assertFalse(data.exists())
            self.assertFalse(state.exists())

    def test_unknown_plugin_conflicts_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = self.make_artifacts(root)
            config, data, state = self.paths(root)
            plugin_path = config / "plugins" / pilot.PLUGIN_NAME
            plugin_path.parent.mkdir(parents=True)
            plugin_path.write_text("user plugin\n", encoding="utf-8")
            before = plugin_path.read_bytes()
            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.apply_pilot(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.29",
                    config_dir=config,
                    data_dir=data,
                    state_dir=state,
                )
            self.assertEqual(ctx.exception.code, "UNKNOWN_PLUGIN_CONFLICT")
            self.assertEqual(plugin_path.read_bytes(), before)
            self.assertFalse(data.exists())
            self.assertFalse(state.exists())

    def test_modified_plugin_blocks_disable_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = self.make_artifacts(root)
            config, data, state = self.paths(root)
            config.mkdir()
            original = {"model": "synthetic/model"}
            (config / "opencode.jsonc").write_bytes(pilot._pretty_json(original))
            pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.29",
                config_dir=config,
                data_dir=data,
                state_dir=state,
            )
            plugin_path = config / "plugins" / pilot.PLUGIN_NAME
            plugin_path.write_text("modified\n", encoding="utf-8")
            config_before = (config / "opencode.jsonc").read_bytes()

            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.disable_pilot(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.29",
                    config_dir=config,
                    data_dir=data,
                    state_dir=state,
                )
            self.assertEqual(ctx.exception.code, "PLUGIN_OWNERSHIP_CONFLICT")
            self.assertTrue(plugin_path.exists())
            self.assertEqual((config / "opencode.jsonc").read_bytes(), config_before)
            self.assertTrue((state / "opencode-permissions-pilot.json").exists())

    def test_config_drift_blocks_disable_before_plugin_removal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = self.make_artifacts(root)
            config, data, state = self.paths(root)
            config.mkdir()
            (config / "opencode.jsonc").write_text('{"model":"before"}\n', encoding="utf-8")
            pilot.apply_pilot(
                pilot_bundle_dir=bundle,
                native_artifact_dir=native,
                installed_version="1.18.29",
                config_dir=config,
                data_dir=data,
                state_dir=state,
            )
            config_path = config / "opencode.jsonc"
            current = json.loads(config_path.read_text())
            current["model"] = "changed-after-apply"
            config_path.write_bytes(pilot._pretty_json(current))
            plugin_path = config / "plugins" / pilot.PLUGIN_NAME
            plugin_before = plugin_path.read_bytes()

            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.disable_pilot(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.29",
                    config_dir=config,
                    data_dir=data,
                    state_dir=state,
                )
            self.assertEqual(ctx.exception.code, "CONFIG_ROLLBACK_CONFLICT")
            self.assertEqual(plugin_path.read_bytes(), plugin_before)

    def test_artifact_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = self.make_artifacts(root)
            (bundle / "bridge.js").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.validate_artifacts(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.29",
                )
            self.assertEqual(ctx.exception.code, "PILOT_FILE_DIGEST_MISMATCH")


if __name__ == "__main__":
    unittest.main()
