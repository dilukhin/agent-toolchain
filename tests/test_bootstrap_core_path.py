from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap_core  # noqa: E402
import setup_manifest  # noqa: E402
import setup_path  # noqa: E402
from setup_lib import Reporter, STATE_CONFIGURED, STATE_INFO, STATE_OK, STATE_OUTDATED  # noqa: E402


class BootstrapCoreTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        for relative in bootstrap_core.REQUIRED_FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".py":
                path.write_text("# bootstrap fixture\n", encoding="utf-8")
            else:
                path.write_text("{}\n", encoding="utf-8")
        for tree in bootstrap_core.REQUIRED_TREES:
            path = source / tree / "fixture.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tree + "\n", encoding="utf-8")
        return source

    def test_apply_repeat_is_idempotent_and_changed_source_retains_one_backup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self._source(root)
            data = root / "data"
            bin_dir = root / "bin"
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            }
            with mock.patch.object(bootstrap_core, "SOURCE_ROOT", source), mock.patch.dict(os.environ, env, clear=False):
                first_out = io.StringIO()
                with contextlib.redirect_stdout(first_out):
                    self.assertEqual(bootstrap_core.main(), 0)
                self.assertIn("configured", first_out.getvalue())
                self.assertFalse(list(data.glob("core.previous.*")))

                marker_before = json.loads(
                    (data / "core" / bootstrap_core.CORE_MARKER).read_text(encoding="utf-8")
                )
                fingerprint_before = marker_before["fingerprint"]
                self.assertTrue(marker_before.get("payload"), "new managed core marker must record its exact payload")
                second_out = io.StringIO()
                with contextlib.redirect_stdout(second_out):
                    self.assertEqual(bootstrap_core.main(), 0)
                self.assertIn("up-to-date", second_out.getvalue())
                self.assertFalse(list(data.glob("core.previous.*")), "repeat bootstrap must not create a backup")

                (source / "config_data.json").write_text('{"changed": true}\n', encoding="utf-8")
                third_out = io.StringIO()
                with contextlib.redirect_stdout(third_out):
                    self.assertEqual(bootstrap_core.main(), 0)
                fingerprint_after = json.loads(
                    (data / "core" / bootstrap_core.CORE_MARKER).read_text(encoding="utf-8")
                )["fingerprint"]
                self.assertNotEqual(fingerprint_before, fingerprint_after)
                self.assertEqual(len(list(data.glob("core.previous.*"))), 1)

    def test_payload_marker_remains_verifiable_if_future_required_set_grows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self._source(root)
            data = root / "data"
            bin_dir = root / "bin"
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
            }
            with mock.patch.object(bootstrap_core, "SOURCE_ROOT", source), mock.patch.dict(os.environ, env, clear=False):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bootstrap_core.main(), 0)

            core = data / "core"
            original_required = bootstrap_core.REQUIRED_FILES
            with mock.patch.object(bootstrap_core, "REQUIRED_FILES", original_required + ("future-required.py",)):
                owned = bootstrap_core._owned_core(core)
            self.assertIsNotNone(owned)
            self.assertTrue(owned.get("payload"))

            (core / "config_data.json").write_text("tampered\n", encoding="utf-8")
            self.assertIsNone(bootstrap_core._owned_core(core))

    def test_foreign_toolchainctl_is_preserved_and_blocks_bootstrap_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self._source(root)
            data = root / "data"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            entry = bin_dir / ("toolchainctl.cmd" if os.name == "nt" else "toolchainctl")
            entry.write_bytes(b"user-owned\n")
            before = entry.read_bytes()
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
            }
            with mock.patch.object(bootstrap_core, "SOURCE_ROOT", source), mock.patch.dict(os.environ, env, clear=False):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(bootstrap_core.main(), 2)
            self.assertEqual(entry.read_bytes(), before)
            self.assertFalse((data / "core").exists(), "foreign entrypoint conflict must be detected before core mutation")


class PathOwnershipTests(unittest.TestCase):
    def test_windows_apply_appends_and_records_only_its_own_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            desired = Path(td) / "bin"
            manifest = setup_manifest.empty_manifest()
            writes: list[tuple[str, int]] = []
            with mock.patch.object(setup_path, "platform_name", return_value="windows"), \
                    mock.patch.object(setup_path, "public_bin_dir", return_value=desired), \
                    mock.patch.object(setup_path, "_read_user_path", return_value=(r"C:\Other;C:\Tools", 2)), \
                    mock.patch.object(setup_path, "_write_user_path", side_effect=lambda value, kind: writes.append((value, kind))):
                reporter = Reporter()
                changed = setup_path.reconcile_public_bin_path(manifest, reporter, check=False)
            self.assertTrue(changed)
            self.assertEqual(len(writes), 1)
            self.assertTrue(writes[0][0].endswith(";" + str(desired)))
            record = manifest["managed_path_entries"]["agent-toolchain-bin"]
            self.assertEqual(record["owner"], "agent-toolchain")
            self.assertEqual(record["path"], str(desired))
            self.assertEqual(reporter.results[-1].state, STATE_CONFIGURED)

    def test_windows_check_is_read_only_and_preexisting_entry_is_not_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            desired = Path(td) / "bin"
            manifest = setup_manifest.empty_manifest()
            with mock.patch.object(setup_path, "platform_name", return_value="windows"), \
                    mock.patch.object(setup_path, "public_bin_dir", return_value=desired), \
                    mock.patch.object(setup_path, "_read_user_path", return_value=(str(desired), 2)), \
                    mock.patch.object(setup_path, "_write_user_path") as write:
                reporter = Reporter()
                changed = setup_path.reconcile_public_bin_path(manifest, reporter, check=True)
            self.assertFalse(changed)
            write.assert_not_called()
            self.assertEqual(manifest["managed_path_entries"], {})
            self.assertEqual(reporter.results[-1].state, STATE_INFO)

    def test_linux_missing_path_is_advisory_and_not_owned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            desired = Path(td) / "bin"
            manifest = setup_manifest.empty_manifest()
            with mock.patch.object(setup_path, "platform_name", return_value="linux"), \
                    mock.patch.object(setup_path, "public_bin_dir", return_value=desired), \
                    mock.patch.dict(os.environ, {"PATH": ""}, clear=False):
                reporter = Reporter()
                changed = setup_path.reconcile_public_bin_path(manifest, reporter, check=False)
            self.assertFalse(changed)
            self.assertEqual(manifest["managed_path_entries"], {})
            self.assertEqual(reporter.results[-1].state, STATE_OUTDATED)


if __name__ == "__main__":
    unittest.main()
