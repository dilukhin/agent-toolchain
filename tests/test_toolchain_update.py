from __future__ import annotations

import contextlib
import hashlib
import http.client
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

    def test_urlopen_retries_incomplete_read_and_returns_complete_payload(self) -> None:
        incomplete = mock.Mock()
        incomplete.headers.get.return_value = None
        incomplete.read.side_effect = http.client.IncompleteRead(b"partial", 2)
        complete = mock.Mock()
        complete.headers.get.return_value = None
        complete.read.return_value = b"complete"

        with mock.patch.object(
            toolchainctl.urllib.request,
            "urlopen",
            side_effect=[contextlib.nullcontext(incomplete), contextlib.nullcontext(complete)],
        ) as urlopen:
            self.assertEqual(toolchainctl._urlopen_bytes("https://example.invalid/archive", max_bytes=1024), b"complete")
        self.assertEqual(urlopen.call_count, 2)

    def test_urlopen_reports_repeated_incomplete_read_without_traceback_leak(self) -> None:
        first = mock.Mock()
        first.headers.get.return_value = None
        first.read.side_effect = http.client.IncompleteRead(b"partial-one", 2)
        second = mock.Mock()
        second.headers.get.return_value = None
        second.read.side_effect = http.client.IncompleteRead(b"partial-two", 2)

        with mock.patch.object(
            toolchainctl.urllib.request,
            "urlopen",
            side_effect=[contextlib.nullcontext(first), contextlib.nullcontext(second)],
        ) as urlopen:
            with self.assertRaisesRegex(toolchainctl.SelfUpdateError, "after 2 attempts"):
                toolchainctl._urlopen_bytes("https://example.invalid/archive", max_bytes=1024)
        self.assertEqual(urlopen.call_count, 2)

    def test_diff_command_parses_opencode_config(self) -> None:
        args = toolchainctl.build_parser().parse_args(["diff", "opencode-config"])
        self.assertEqual(args.command, "diff")
        self.assertEqual(args.component, "opencode-config")

    def test_opencode_diff_redacts_sensitive_values_and_reports_paths(self) -> None:
        before = {
            "provider": {
                "routerai": {
                    "options": {"apiKey": "super-secret", "baseURL": "https://old.invalid"},
                }
            }
        }
        after = {
            "provider": {
                "routerai": {
                    "options": {"apiKey": "another-secret", "baseURL": "https://new.invalid"},
                }
            }
        }
        redacted_before = toolchainctl._redact_sensitive_config(before)
        redacted_after = toolchainctl._redact_sensitive_config(after)
        self.assertEqual(redacted_before["provider"]["routerai"]["options"]["apiKey"], "<redacted>")
        self.assertEqual(redacted_after["provider"]["routerai"]["options"]["apiKey"], "<redacted>")
        changes = toolchainctl._changed_json_paths(before, after)
        self.assertEqual(
            changes,
            ["$.provider.routerai.options.apiKey", "$.provider.routerai.options.baseURL"],
        )

    def test_opencode_diff_uses_sibling_provider_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            config_dir = (base / "config").resolve()
            config_dir.mkdir()
            credential = config_dir / "routerai-api-key.txt"
            credential.write_text("secret\n", encoding="utf-8")
            config_path = config_dir / "opencode.jsonc"
            existing = {
                "provider": {
                    "routerai": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "RouterAI",
                        "options": {
                            "baseURL": "https://routerai.ru/api/v1",
                            "apiKey": "{file:" + str(credential) + "}",
                        },
                        "models": {},
                    }
                },
                "autoupdate": "notify",
            }
            config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            manifest = empty_manifest()
            manifest["managed_files"]["OpenCode config"] = {
                "path": str(config_path),
                "sha256": sha256_bytes(config_path.read_bytes()),
                "source": "test",
                "mode": "merged-json-sibling-provider",
            }

            target, error = toolchainctl._opencode_diff_target(
                existing,
                manifest,
                config_dir,
                config_path,
            )

        self.assertIsNone(error)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target["model"], "openai/gpt-5.6-terra")
        self.assertEqual(target["small_model"], "openai/gpt-5.6-luna")
        self.assertEqual(target["agent"]["general"]["model"], "openai/gpt-5.6-terra")
        self.assertEqual(target["agent"]["explore"]["model"], "openai/gpt-5.6-luna")

    def test_failed_bootstrap_access_blocks_update_apply(self) -> None:
        with mock.patch.object(toolchainctl, "_owned_installed_core", return_value={"fingerprint": "a" * 64}), \
                mock.patch.object(toolchainctl, "_resolve_update_sha", return_value="b" * 40), \
                mock.patch.object(toolchainctl, "_urlopen_bytes", return_value=b"fixture"), \
                mock.patch.object(toolchainctl, "_extract_update_archive", return_value=Path("fixture")), \
                mock.patch.object(toolchainctl.subprocess, "run", return_value=mock.Mock(returncode=2)) as run, \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(toolchainctl._run_self_update(apply_after=True), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(run.call_count, 1, "apply must not follow failed bootstrap validation")

    def test_version_command_reports_installed_source_ref(self) -> None:
        source_ref = "39dea792ee2923a8853ba5fa416fde7be24a7db6"
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary) / "core"
            core.mkdir()
            tool = core / "toolchainctl.py"
            tool.write_text("# managed core\n", encoding="utf-8")
            marker = {
                "schema": 1,
                "owner": "agent-toolchain",
                "fingerprint": "a" * 64,
                "source_ref": source_ref,
            }
            (core / toolchainctl.CORE_MARKER).write_text(json.dumps(marker), encoding="utf-8")
            with mock.patch.object(toolchainctl, "__file__", str(tool)):
                expected = "toolchainctl 0.1.0.39dea792"
                self.assertEqual(toolchainctl._version_text(), expected)
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as exited:
                    toolchainctl.build_parser().parse_args(["--version"])
                self.assertEqual(exited.exception.code, 0)
                self.assertEqual(stdout.getvalue(), expected + "\n")

    def test_version_distinguishes_bootstrap_without_source_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary) / "core"
            core.mkdir()
            tool = core / "toolchainctl.py"
            tool.write_text("# managed core\n", encoding="utf-8")
            marker = {
                "schema": 1,
                "owner": "agent-toolchain",
                "fingerprint": "b" * 64,
            }
            (core / toolchainctl.CORE_MARKER).write_text(json.dumps(marker), encoding="utf-8")
            with mock.patch.object(toolchainctl, "__file__", str(tool)):
                self.assertEqual(toolchainctl._version_text(), "toolchainctl 0.1.0.local.bbbbbbbb")

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

    def test_payload_marker_survives_future_required_set_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            core = Path(temporary) / "core"
            core.mkdir()
            path = core / "toolchainctl.py"
            content = b"# managed current toolchain\n"
            path.write_bytes(content)
            relative = "toolchainctl.py"
            digest = hashlib.sha256()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
            fingerprint = digest.hexdigest()
            marker = {
                "schema": 1,
                "owner": "agent-toolchain",
                "fingerprint": fingerprint,
                "payload": [{"path": relative, "sha256": hashlib.sha256(content).hexdigest()}],
            }
            (core / ".agent-toolchain-managed-core.json").write_text(json.dumps(marker), encoding="utf-8")

            with mock.patch.object(toolchainctl, "__file__", str(path)), mock.patch.object(
                toolchainctl, "_CORE_REQUIRED_FILES", ("future-required.py",)
            ):
                self.assertEqual(toolchainctl._owned_installed_core()["fingerprint"], fingerprint)

            path.write_text("tampered\n", encoding="utf-8")
            with mock.patch.object(toolchainctl, "__file__", str(path)):
                with self.assertRaises(toolchainctl.SelfUpdateError):
                    toolchainctl._owned_installed_core()

    def test_managed_routerai_label_is_updated_but_custom_label_is_preserved(self) -> None:
        desired = json.loads((Path(toolchainctl.__file__).resolve().parent / "config_data.json").read_text(encoding="utf-8"))
        qwen_target = desired["models"]["qwen/qwen3.6-plus"]["name"]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            config_dir = (base / "config").resolve()
            state_dir = (base / "state").resolve()
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
            base = Path(temporary).resolve()
            config_dir = (base / "config").resolve()
            state_dir = (base / "state").resolve()
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
