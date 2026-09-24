from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_opencode_permissions_pilot_metrics import MetricsConflict, active_artifact, read_metrics

ARTIFACT = "sha256:" + "a" * 64
NATIVE = "sha256:" + "b" * 64
COUNTERS = {
    "native_ask": 2, "classifier_allow": 1, "classifier_deny": 0,
    "residual_ask": 1, "classifier_error/fail_closed": 0, "binding_reject": 0,
}


class MetricsTests(unittest.TestCase):
    def directory(self, home: Path) -> Path:
        path = home / ".local/state/opencode_permissions/p0-metrics" / ("sha256-" + "a" * 64)
        path.mkdir(parents=True)
        path.chmod(0o700)
        return path

    def snapshot(self, directory: Path, name: str = "process-1.json", **changes):
        data = {
            "schema": "opencode-permissions-p0-metrics/v1",
            "scope": "ask_path",
            "opencode_version": "1.18.29",
            "compatibility_profile": "exact-test",
            "native_policy_artifact_id": NATIVE,
            "pilot_artifact_id": ARTIFACT,
            "classifier_profile": "p0-test",
            "counters": COUNTERS,
            "reasons": {"unsupported": 1},
            "families": {"grep": 1},
            "updated_at": "2026-09-24T00:00:00Z",
        }
        data.update(changes)
        path = directory / name
        path.write_text(json.dumps(data), encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_missing_is_read_only_and_zero_denominator_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            self.assertEqual(read_metrics(home=home, artifact_id=ARTIFACT)["status"], "no_data")
            self.assertFalse((home / ".local").exists())
            directory = self.directory(home)
            self.snapshot(directory, counters={**COUNTERS, "native_ask": 0,
                                               "classifier_allow": 0, "residual_ask": 0})
            result = read_metrics(home=home, artifact_id=ARTIFACT, expected_version="1.18.29")
            self.assertIsNone(result["prompt_reduction_ratio"])

    def test_aggregate_current_snapshots_without_raw_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            directory = self.directory(Path(td))
            self.snapshot(directory)
            self.snapshot(directory, name="process-2.json")
            result = read_metrics(home=Path(td), artifact_id=ARTIFACT,
                                  expected_version="1.18.29", expected_native_id=NATIVE)
            self.assertEqual(result["snapshot_count"], 2)
            self.assertEqual(result["counters"]["native_ask"], 4)
            self.assertEqual(result["prompt_reduction_ratio"], 0.5)
            self.assertEqual(result["families"], {"grep": 2})
            self.assertNotIn(str(directory), json.dumps(result))

    def test_mixed_corrupt_symlink_and_wrong_mode_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            directory = self.directory(Path(td))
            first = self.snapshot(directory)
            second = self.snapshot(directory, name="process-2.json", opencode_version="1.18.30")
            with self.assertRaisesRegex(MetricsConflict, "METRICS_MIXED_PROFILE"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)
            second.unlink()
            second.symlink_to(first)
            with self.assertRaisesRegex(MetricsConflict, "METRICS_FILE_CONFLICT"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)
            second.unlink()
            first.write_text("{bad json", encoding="utf-8")
            with self.assertRaisesRegex(MetricsConflict, "METRICS_FILE_INVALID"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)
            first.chmod(0o644)
            with self.assertRaisesRegex(MetricsConflict, "METRICS_FILE_MODE"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)

    def test_unknown_fields_and_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            directory = self.directory(Path(td))
            path = self.snapshot(directory, argv=["secret"])
            with self.assertRaisesRegex(MetricsConflict, "METRICS_SCHEMA_INVALID"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)
            path.write_text('{"schema":1,"schema":2}', encoding="utf-8")
            with self.assertRaisesRegex(MetricsConflict, "METRICS_DUPLICATE_KEY"):
                read_metrics(home=Path(td), artifact_id=ARTIFACT)

    def test_owned_state_lookup_is_read_only_and_exact(self):
        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td) / "state"
            self.assertIsNone(active_artifact(state_dir))
            self.assertFalse(state_dir.exists())
            state_dir.mkdir()
            path = state_dir / "opencode-permissions-pilot.json"
            path.write_text(json.dumps({
                "schema": 1, "owner": "agent-toolchain",
                "component": "opencode-permissions-pilot", "phase": "prepared",
                "artifact_id": ARTIFACT, "native_artifact_id": NATIVE,
                "opencode_version": "1.18.29",
            }), encoding="utf-8")
            self.assertEqual(active_artifact(state_dir), (ARTIFACT, "1.18.29", NATIVE))
            path.write_text('{"schema":1,"owner":"unknown"}', encoding="utf-8")
            with self.assertRaisesRegex(MetricsConflict, "METRICS_STATE_INVALID"):
                active_artifact(state_dir)


if __name__ == "__main__":
    unittest.main()
