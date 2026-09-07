from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for entry in (ROOT, TESTS):
    sys.path.insert(0, str(entry))

import setup_opencode_permissions_pilot as pilot  # noqa: E402
import test_opencode_permissions_pilot_deployment as deployment_tests  # noqa: E402


class PilotRecoveryGuardTests(unittest.TestCase):
    def test_invalid_previous_permission_is_rejected_before_any_mutation(self):
        helper = deployment_tests.PilotDeploymentTests(methodName="runTest")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle, native, _ = helper.make_artifacts(root)
            config = root / "managed" / "config"
            data = root / "managed" / "data"
            state = root / "managed" / "state"
            config.mkdir(parents=True)
            config_path = config / "opencode.jsonc"
            original = pilot._pretty_json(
                {
                    "model": "synthetic/model",
                    "permission": {
                        "bash": {
                            "pwd": {
                                "unexpected": "arbitrary recovery payload"
                            }
                        }
                    },
                }
            )
            config_path.write_bytes(original)

            with self.assertRaises(pilot.PilotDeploymentError) as ctx:
                pilot.apply_pilot(
                    pilot_bundle_dir=bundle,
                    native_artifact_dir=native,
                    installed_version="1.18.29",
                    config_dir=config,
                    data_dir=data,
                    state_dir=state,
                )

            self.assertEqual(ctx.exception.code, "RECOVERY_PERMISSION_INVALID")
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse(data.exists())
            self.assertFalse(state.exists())
            self.assertFalse((config / "plugins" / pilot.PLUGIN_NAME).exists())


if __name__ == "__main__":
    unittest.main()
