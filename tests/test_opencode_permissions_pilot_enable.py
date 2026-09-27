import tempfile
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup_opencode_permissions_pilot_enable as enable


class EnableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.config = self.workspace / ".opencode"
        self.data = self.root / "data"
        self.state = self.root / "state"
        self.cache = self.state / "cache"
        self.pair = (self.root / "pilot", self.root / "native")
        self.native_permission = {"*": {"*": "ask"}, "read": {"*": "allow"}}
        self.validation = {
            "pilot_artifact_id": enable.source.CURRENT.pilot_id,
            "native_artifact_id": enable.source.CURRENT.native_id,
            "permission": self.native_permission,
        }
        self.paths = mock.patch.object(enable.control, "canonical_paths",
                                       return_value=(self.config, self.data, self.state, self.cache))
        self.status = mock.patch.object(enable.control, "status", return_value={"status": "disabled"})
        self.fetch = mock.patch.object(enable.source, "materialize_source", return_value=self.pair)
        self.artifacts = mock.patch.object(enable.pilot, "validate_artifacts", return_value=self.validation)
        self.apply = mock.patch.object(enable.pilot, "apply_pilot",
                                       return_value={"status": "active", "changed": True})
        self.disable = mock.patch.object(enable.pilot, "disable_pilot")
        self.version = mock.patch.object(enable, "_version", return_value=enable.source.CURRENT.version)
        self.binary = mock.patch.object(enable.shutil, "which", return_value="/usr/bin/opencode")
        self.env = mock.patch.object(enable, "_preflight_environment")
        self.calls = [item.start() for item in
                      (self.paths, self.status, self.fetch, self.artifacts,
                       self.apply, self.disable, self.version, self.binary, self.env)]
        for item in (self.paths, self.status, self.fetch, self.artifacts,
                     self.apply, self.disable, self.version, self.binary, self.env):
            self.addCleanup(item.stop)

    def test_competing_agent_override_prevents_download_and_mutation(self):
        with mock.patch.object(enable, "_resolved_config", return_value={
            "agent": {"build": {"permission": {"bash": "ask"}}}
        }), self.assertRaisesRegex(enable.EnableConflict, "P0_COMPETING_AGENT_PERMISSION"):
            enable.enable(workspace=self.workspace)
        self.calls[2].assert_not_called()
        self.calls[4].assert_not_called()

    def test_effective_readback_failure_rolls_back_owned_deployment(self):
        configs = [{}, {"permission": {"bash": {"*": "allow"}}, "plugin": ["opencode-permissions-p0.js"]}]
        with mock.patch.object(enable, "_resolved_config", side_effect=configs), self.assertRaisesRegex(
            enable.EnableConflict, "P0_EFFECTIVE_PERMISSION_CONFLICT"
        ):
            enable.enable(workspace=self.workspace)
        self.calls[4].assert_called_once()
        self.calls[5].assert_called_once()

    def test_project_scoped_enable_and_repeat_noop(self):
        configs = [{}, {"permission": self.native_permission, "plugin": ["opencode-permissions-p0.js"]}]
        with mock.patch.object(enable, "_resolved_config", side_effect=configs):
            result = enable.enable(workspace=self.workspace)
        self.assertEqual(result["workspace"], str(self.workspace))
        self.assertEqual(self.calls[4].call_args.kwargs["config_dir"], self.config)
        self.assertTrue(self.calls[4].call_args.kwargs["ensure_schema"])
        self.assertEqual(self.calls[4].call_args.kwargs["state_dir"], self.state)
        self.calls[5].assert_not_called()
        self.calls[1].return_value = {"status": "active", "artifact_id": enable.source.CURRENT.pilot_id}
        again = enable.enable(workspace=self.workspace)
        self.assertFalse(again["changed"])
        self.calls[4].assert_called_once()


if __name__ == "__main__":
    unittest.main()
