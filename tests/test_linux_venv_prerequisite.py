from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_managed_tools as managed  # noqa: E402
import toolchainctl  # noqa: E402
import setup_manifest  # noqa: E402
from setup_lib import Reporter, STATE_CONFLICT, STATE_FAILED  # noqa: E402
from setup_tools import parse_tool_spec  # noqa: E402


class ManagedVenvPrerequisiteTests(unittest.TestCase):
    def _spec(self):
        spec, error = parse_tool_spec("fixture", {
            "source": "git",
            "repo": "https://example.invalid/fixture.git",
            "ref": "1" * 40,
            "project_directory": "fixture",
            "runtime": "python-venv",
            "update_policy": "pinned-tested",
            "entrypoints": ["fixture"],
            "health_contract": [{"argv": ["fixture", "--help"]}],
            "platforms": ["windows", "linux"],
        })
        self.assertIsNone(error)
        assert spec is not None
        return spec

    def test_check_reports_missing_venv_prerequisite_without_mutation(self) -> None:
        spec = self._spec()
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            manifest = setup_manifest.empty_manifest()
            with mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_DATA_DIR": str(data)}, clear=False), \
                    mock.patch.object(managed, "_venv_prerequisite", return_value=(False, "3.12")):
                reporter = Reporter()
                changed = managed.reconcile_python_tool(
                    spec, sys.executable, reporter,
                    check=True, skip_install=False, manifest=manifest,
                )
            self.assertFalse(changed)
            self.assertFalse(data.exists())
            runtime = [item for item in reporter.results if item.component == "fixture runtime"][-1]
            self.assertEqual(runtime.state, STATE_CONFLICT)
            self.assertIn("venv/ensurepip", runtime.detail)
            self.assertIn("MANUAL ACTION REQUIRED", runtime.detail)
            self.assertIn("python3-venv", runtime.detail)
            self.assertIn("--install-needed", runtime.detail)

    def test_apply_stops_before_runtime_directory_when_venv_prerequisite_is_missing(self) -> None:
        spec = self._spec()
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            manifest = setup_manifest.empty_manifest()
            with mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_DATA_DIR": str(data)}, clear=False), \
                    mock.patch.object(managed, "_venv_prerequisite", return_value=(False, "3.12")):
                reporter = Reporter()
                changed = managed.reconcile_python_tool(
                    spec, sys.executable, reporter,
                    check=False, skip_install=False, manifest=manifest,
                )
            self.assertFalse(changed)
            self.assertFalse(data.exists(), "prerequisite failure must happen before runtime mkdir/staging")
            runtime = [item for item in reporter.results if item.component == "fixture runtime"][-1]
            self.assertEqual(runtime.state, STATE_FAILED)
            self.assertIn("venv/ensurepip", runtime.detail)
            self.assertIn("toolchainctl apply", runtime.detail)

    def test_apply_parser_accepts_explicit_install_needed_opt_in(self) -> None:
        args = toolchainctl.build_parser().parse_args(["apply", "--install-needed"])
        self.assertEqual(args.command, "apply")
        self.assertTrue(args.install_needed)

    def test_check_parser_does_not_accept_install_needed_mutation_flag(self) -> None:
        with self.assertRaises(SystemExit):
            toolchainctl.build_parser().parse_args(["check", "--install-needed"])

    def test_prepare_apt_command_prompts_once_with_sudo_v_then_uses_noninteractive_apt(self) -> None:
        def which(name: str) -> str | None:
            return {
                "apt-get": "/usr/bin/apt-get",
                "sudo": "/usr/bin/sudo",
            }.get(name)

        with mock.patch.object(toolchainctl.os, "geteuid", return_value=1000, create=True), \
                mock.patch.object(toolchainctl.shutil, "which", side_effect=which), \
                mock.patch.object(
                    toolchainctl.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(["sudo", "-v"], 0),
                ) as run:
            command, error = toolchainctl._prepare_apt_command()

        self.assertIsNone(error)
        self.assertEqual(command, ["/usr/bin/sudo", "-n", "/usr/bin/apt-get"])
        run.assert_called_once_with(["/usr/bin/sudo", "-v"], check=False)

    def test_prepare_apt_command_true_root_uses_apt_directly_without_sudo(self) -> None:
        with mock.patch.object(toolchainctl.os, "geteuid", return_value=0, create=True), \
                mock.patch.object(
                    toolchainctl.shutil,
                    "which",
                    side_effect=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
                ), \
                mock.patch.object(toolchainctl.subprocess, "run") as run:
            command, error = toolchainctl._prepare_apt_command()

        self.assertIsNone(error)
        self.assertEqual(command, ["/usr/bin/apt-get"])
        run.assert_not_called()

    def test_install_needed_rejects_running_whole_toolchain_through_sudo(self) -> None:
        with mock.patch.dict(os.environ, {"SUDO_USER": "dima"}, clear=False), \
                mock.patch.object(toolchainctl.os, "geteuid", return_value=0, create=True), \
                mock.patch.object(toolchainctl, "_prepare_apt_command") as prepare, \
                mock.patch.object(toolchainctl, "_install_opencode_v2") as install_opencode:
            rc = toolchainctl._install_needed_prerequisites()

        self.assertEqual(rc, 2)
        prepare.assert_not_called()
        install_opencode.assert_not_called()

    def test_install_needed_does_not_request_sudo_when_system_packages_are_not_needed(self) -> None:
        with mock.patch.object(toolchainctl, "_running_via_sudo_as_root", return_value=False), \
                mock.patch.object(toolchainctl, "_debian_family_linux", return_value=True), \
                mock.patch.object(toolchainctl, "_dpkg_package_installed", return_value=True), \
                mock.patch.object(toolchainctl, "_python_venv_available", return_value=True), \
                mock.patch.object(toolchainctl, "_go_122_available", return_value=(True, "go version go1.22.0 linux/amd64")), \
                mock.patch.object(toolchainctl, "_install_opencode_v2", return_value=(True, "OpenCode already available")), \
                mock.patch.object(toolchainctl, "_prepare_apt_command") as prepare:
            rc = toolchainctl._install_needed_prerequisites()

        self.assertEqual(rc, 0)
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
