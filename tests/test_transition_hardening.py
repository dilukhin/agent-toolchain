from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap_core  # noqa: E402
import setup_managed_tools as managed  # noqa: E402
import setup_manifest  # noqa: E402
import toolchainctl  # noqa: E402
from setup_lib import Reporter  # noqa: E402
from setup_tools import parse_tool_spec  # noqa: E402


class StateOwnershipHardeningTests(unittest.TestCase):
    def test_existing_new_state_without_manifest_is_rejected_read_only_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            new = root / "new"
            legacy = root / "legacy"
            new.mkdir()
            unknown = new / "unknown.txt"
            unknown.write_text("preserve", encoding="utf-8")
            env = {
                "AGENT_TOOLCHAIN_STATE_DIR": str(new),
                "OPENCODE_SETUP_STATE_DIR": str(legacy),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                for check in (True, False):
                    with self.subTest(check=check):
                        with self.assertRaises(toolchainctl.StateMigrationError):
                            toolchainctl.prepare_state(check=check)
            self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve")
            self.assertFalse(legacy.exists())


class BootstrapIntegrityHardeningTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        for relative in bootstrap_core.REQUIRED_FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n" if path.suffix == ".py" else "{}\n", encoding="utf-8")
        for tree in bootstrap_core.REQUIRED_TREES:
            path = source / tree / "fixture.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tree + "\n", encoding="utf-8")
        return source

    def test_modified_owned_core_payload_is_preserved_and_blocks_republish(self) -> None:
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
            with mock.patch.object(bootstrap_core, "SOURCE_ROOT", source), mock.patch.dict(
                os.environ, env, clear=False
            ):
                self.assertEqual(bootstrap_core.main(), 0)
                installed = data / "core" / "config_data.json"
                installed.write_text('{"locally_modified": true}\n', encoding="utf-8")
                before = installed.read_bytes()
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(bootstrap_core.main(), 2)

            self.assertEqual(installed.read_bytes(), before)
            self.assertFalse(list(data.glob("core.previous.*")))
            self.assertIn("modified or unowned core directory", err.getvalue())


class FinalPathVenvHardeningTests(unittest.TestCase):
    REF = "2" * 40

    def _spec(self):
        spec, error = parse_tool_spec(
            "fixture-tool",
            {
                "source": "git",
                "repo": "https://example.invalid/fixture-tool.git",
                "ref": self.REF,
                "project_directory": "fixture-tool",
                "runtime": "python-venv",
                "update_policy": "pinned-tested",
                "entrypoints": ["fixture-tool"],
                "health_contract": [{"argv": ["fixture-tool", "--version"]}],
                "platforms": ["windows", "linux"],
            },
        )
        self.assertIsNone(error)
        assert spec is not None
        return spec

    def test_venv_is_created_at_final_immutable_release_path(self) -> None:
        spec = self._spec()
        commands: list[list[str]] = []
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            bin_dir = root / "bin"
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            }

            def fake_run(cmd: list[str], cwd=None, env=None):
                commands.append(cmd)
                if cmd[1:4] == ["-B", "-m", "venv"]:
                    venv = Path(cmd[-1])
                    python = managed._venv_python(venv)
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.write_bytes(b"python")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if len(cmd) >= 4 and cmd[1:4] == ["-m", "pip", "install"]:
                    venv = Path(cmd[0]).parent.parent
                    entry = managed._venv_command(venv, "fixture-tool")
                    entry.parent.mkdir(parents=True, exist_ok=True)
                    entry.write_bytes(b"tool")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if cmd[-1:] == ["--version"]:
                    return subprocess.CompletedProcess(cmd, 0, "fixture-tool 1\n", "")
                raise AssertionError(f"unexpected command: {cmd}")

            def fake_which(name: str):
                if name == "git":
                    return "/fake/git"
                if name == "fixture-tool":
                    return str(managed._public_entrypoint(spec, name))
                return None

            manifest = setup_manifest.empty_manifest()
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                managed, "_venv_prerequisite", return_value=(True, "3.12")
            ), mock.patch.object(managed, "run", side_effect=fake_run), mock.patch.object(
                managed.shutil, "which", side_effect=fake_which
            ):
                reporter = Reporter()
                changed = managed.reconcile_python_tool(
                    spec,
                    sys.executable,
                    reporter,
                    check=False,
                    skip_install=False,
                    manifest=manifest,
                )

            self.assertTrue(changed)
            release = data / "tools" / spec.name / "releases" / self.REF
            venv_commands = [cmd for cmd in commands if cmd[1:4] == ["-B", "-m", "venv"]]
            self.assertEqual(len(venv_commands), 1)
            self.assertEqual(Path(venv_commands[0][-1]), release / "venv")
            self.assertNotIn(".tmp-", str(venv_commands[0][-1]))
            self.assertTrue((release / managed._RUNTIME_MARKER).is_file())


if __name__ == "__main__":
    unittest.main()
