from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_managed_tools as managed  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
