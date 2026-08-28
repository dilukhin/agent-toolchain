from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import setup_core  # noqa: E402
import setup_core_adapter  # noqa: E402
import toolchainctl  # noqa: E402
from setup_manifest import empty_manifest, save_manifest  # noqa: E402
from setup_tools import GENERIC_PROFILE, ProfileConfigError, load_effective_config  # noqa: E402


class ProfileConfigTests(unittest.TestCase):
    def test_generic_base_has_no_author_specific_policy(self) -> None:
        config = load_effective_config(ROOT)
        self.assertNotIn("routerai", config)
        self.assertNotIn("models", config)
        self.assertNotIn("config_defaults", config)
        self.assertNotIn("helper_tools", config)
        self.assertEqual(config["managed_environment"]["tools"], {})
        text = (ROOT / "config_data.json").read_text(encoding="utf-8")
        self.assertNotIn("~/projects", text)
        self.assertNotIn("127.0.0.1:1080", text)
        self.assertNotIn("deepseek-v4-flash-free", text)

    def test_author_profile_restores_pre_split_policy(self) -> None:
        config = load_effective_config(ROOT, profile="dilukhin")
        self.assertTrue(config["routerai"]["enabled"])
        self.assertEqual(config["config_defaults"]["model"], "opencode/deepseek-v4-flash-free")
        self.assertEqual(set(config["managed_environment"]["tools"]), {"ssh_relay", "agent-safe"})
        platform = "windows" if os.name == "nt" else "linux"
        self.assertIn("projects_dir", config["platform_specific"][platform])

    def test_local_override_is_last_and_null_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "local.json"
            override.write_text(
                json.dumps(
                    {
                        "profile_schema": 1,
                        "config_defaults": {"model": "local/model"},
                        "managed_environment": {"tools": {"agent-safe": None}},
                        "machine": {"proxy_url": "socks5://127.0.0.1:9999"},
                    }
                ),
                encoding="utf-8",
            )
            config = load_effective_config(ROOT, profile="dilukhin", local_override=override)
        self.assertEqual(config["config_defaults"]["model"], "local/model")
        self.assertNotIn("agent-safe", config["managed_environment"]["tools"])
        self.assertEqual(config["machine"]["proxy_url"], "socks5://127.0.0.1:9999")

    def test_repository_profile_rejects_unsafe_name(self) -> None:
        with self.assertRaises(ProfileConfigError):
            load_effective_config(ROOT, profile="../private")

    def test_toolchain_installs_existing_runtime_adapter(self) -> None:
        self.assertIs(setup_core.main, setup_core_adapter._toolchain_main)


class ProfileSelectionTests(unittest.TestCase):
    def _args(self, profile: str | None = None, local_config: str | None = None):
        return type("Args", (), {"profile": profile, "local_config": local_config})()

    def test_empty_state_selects_generic_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            before = list(Path(td).rglob("*"))
            selected, local, inferred = toolchainctl._resolve_configuration(self._args(), state)
            after = list(Path(td).rglob("*"))
        self.assertEqual(selected, GENERIC_PROFILE)
        self.assertIsNone(local)
        self.assertFalse(inferred)
        self.assertEqual(before, after)

    def test_legacy_author_manifest_is_inferred_conservatively(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            manifest = empty_manifest()
            manifest["managed_files"]["OpenCode config"] = {
                "path": "/example/opencode.jsonc",
                "sha256": "0" * 64,
                "source": "opencode_setup:managed-merge:templates/opencode.jsonc",
            }
            save_manifest(state / "manifest.json", manifest)
            selected, _local, inferred = toolchainctl._resolve_configuration(self._args(), state)
        self.assertEqual(selected, "dilukhin")
        self.assertTrue(inferred)

    def test_unknown_managed_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            manifest = empty_manifest()
            manifest["managed_files"]["unknown"] = {
                "path": "/example/unknown",
                "sha256": "0" * 64,
                "source": "other-product:unknown",
            }
            save_manifest(state / "manifest.json", manifest)
            with self.assertRaises(toolchainctl.StateMigrationError):
                toolchainctl._resolve_configuration(self._args(), state)

    def test_recorded_author_profile_cannot_silently_switch_to_generic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            manifest = empty_manifest()
            manifest["configuration_profile"] = "dilukhin"
            save_manifest(state / "manifest.json", manifest)
            with self.assertRaises(toolchainctl.StateMigrationError):
                toolchainctl._resolve_configuration(self._args(profile="generic"), state)

    def test_recorded_generic_can_explicitly_opt_into_author_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            manifest = empty_manifest()
            manifest["configuration_profile"] = "generic"
            save_manifest(state / "manifest.json", manifest)
            selected, _local, inferred = toolchainctl._resolve_configuration(self._args(profile="dilukhin"), state)
        self.assertEqual(selected, "dilukhin")
        self.assertFalse(inferred)


if __name__ == "__main__":
    unittest.main()
