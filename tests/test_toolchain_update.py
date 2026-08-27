from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import bootstrap_core
import toolchainctl
from setup_manifest import empty_manifest, save_manifest
from setup_lib import sha256_bytes


class ToolchainUpdateTests(unittest.TestCase):
    def test_update_command_parses_apply(self) -> None:
        args = toolchainctl.build_parser().parse_args(["update", "--apply"])
        self.assertEqual(args.command, "update")
        self.assertTrue(args.apply)

    def test_update_archive_rejects_path_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(toolchainctl.SelfUpdateError):
                toolchainctl._extract_update_archive(buffer.getvalue(), Path(temporary))

    def test_update_archive_rejects_windows_backslash_traversal(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("agent-toolchain-ref/..\\escape.txt", "bad")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(toolchainctl.SelfUpdateError):
                toolchainctl._extract_update_archive(buffer.getvalue(), Path(temporary))

    def test_self_update_payload_contract_matches_bootstrap(self) -> None:
        self.assertEqual(toolchainctl._CORE_REQUIRED_FILES, bootstrap_core.REQUIRED_FILES)
        self.assertEqual(toolchainctl._CORE_REQUIRED_TREES, bootstrap_core.REQUIRED_TREES)

    def test_update_archive_accepts_single_safe_root(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("agent-toolchain-ref/bootstrap_core.py", "print('ok')\n")
            archive.writestr("agent-toolchain-ref/toolchainctl.py", "print('ok')\n")
        with tempfile.TemporaryDirectory() as temporary:
            root = toolchainctl._extract_update_archive(buffer.getvalue(), Path(temporary))
            self.assertTrue((root / "bootstrap_core.py").is_file())

    def test_installed_core_fingerprint_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary) / "core"
            core.mkdir()
            for relative in toolchainctl._CORE_REQUIRED_FILES:
                path = core / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")
            for tree in toolchainctl._CORE_REQUIRED_TREES:
                path = core / tree / "fixture.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(tree + "\n", encoding="utf-8")
            fingerprint = toolchainctl._installed_core_fingerprint(core)
            marker = {"schema": 1, "owner": "agent-toolchain", "fingerprint": fingerprint}
            (core / ".agent-toolchain-managed-core.json").write_text(json.dumps(marker), encoding="utf-8")

            with mock.patch.object(toolchainctl, "__file__", str(core / "toolchainctl.py")):
                self.assertEqual(toolchainctl._owned_installed_core()["fingerprint"], fingerprint)
                (core / "config_data.json").write_text("tampered\n", encoding="utf-8")
                with self.assertRaises(toolchainctl.SelfUpdateError):
                    toolchainctl._owned_installed_core()

    def test_managed_routerai_label_is_updated_but_custom_label_is_preserved(self) -> None:
        desired = json.loads((Path(toolchainctl.__file__).resolve().parent / "config_data.json").read_text(encoding="utf-8"))
        qwen_target = desired["models"]["qwen/qwen3.6-plus"]["name"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config_dir = base / "config"
            state_dir = base / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            config = {
                "provider": {
                    "routerai": {
                        "models": {
                            "qwen/qwen3.6-plus": {"name": "Qwen 3.6 Plus"},
                            "openai/gpt-4o": {"name": "My custom GPT label"},
                        }
                    }
                }
            }
            config_path = config_dir / "opencode.jsonc"
            original = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            config_path.write_bytes(original)
            manifest = empty_manifest()
            manifest["managed_files"]["OpenCode config"] = {
                "path": str(config_path),
                "sha256": sha256_bytes(original),
                "source": "test",
                "mode": "merged-json",
            }
            save_manifest(state_dir / "manifest.json", manifest)

            with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": str(config_dir)}, clear=False):
                rc = toolchainctl._reconcile_routerai_model_labels(state_dir, check=False)
            self.assertEqual(rc, 0)
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            models = updated["provider"]["routerai"]["models"]
            self.assertEqual(models["qwen/qwen3.6-plus"]["name"], qwen_target)
            self.assertEqual(models["openai/gpt-4o"]["name"], "My custom GPT label")
            saved = json.loads((state_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                saved["managed_files"]["OpenCode config"]["sha256"],
                sha256_bytes(config_path.read_bytes()),
            )

    def test_routerai_label_check_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config_dir = base / "config"
            state_dir = base / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            config_path = config_dir / "opencode.jsonc"
            original = (
                '{"provider":{"routerai":{"models":{"qwen/qwen3.6-plus":{"name":"Qwen 3.6 Plus"}}}}}\n'
            ).encode("utf-8")
            config_path.write_bytes(original)
            manifest = empty_manifest()
            manifest["managed_files"]["OpenCode config"] = {
                "path": str(config_path),
                "sha256": sha256_bytes(original),
                "source": "test",
                "mode": "merged-json",
            }
            save_manifest(state_dir / "manifest.json", manifest)
            before_manifest = (state_dir / "manifest.json").read_bytes()
            with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": str(config_dir)}, clear=False):
                rc = toolchainctl._reconcile_routerai_model_labels(state_dir, check=True)
            self.assertEqual(rc, 0)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual((state_dir / "manifest.json").read_bytes(), before_manifest)


if __name__ == "__main__":
    unittest.main()
