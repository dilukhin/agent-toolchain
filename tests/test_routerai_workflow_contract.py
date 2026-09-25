from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / ".github" / "workflows" / "routerai_catalog.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "routerai_watchdog.yml"


class RouterAiWorkflowContractTests(unittest.TestCase):
    def test_existing_candidate_resumes_pr_and_validation_without_new_push(self) -> None:
        text = CATALOG.read_text(encoding="utf-8")
        self.assertIn("run-name: RouterAI catalog refresh contract-v1", text)
        self.assertEqual(text.count("if: steps.commit.outputs.candidate_required == '1'"), 1)
        self.assertNotIn("if: steps.commit.outputs.push_required == '1'", text)
        self.assertIn("Published RouterAI candidate read-back mismatch", text)
        self.assertIn("existing_run_id=", text)
        self.assertIn(r'.status == \"completed\" and .conclusion == \"success\"', text)
        self.assertIn(".databaseId > $before_max", text)

    def test_full_success_requires_status_publication(self) -> None:
        text = CATALOG.read_text(encoding="utf-8")
        self.assertIn("name: Confirm complete RouterAI refresh", text)
        self.assertIn('STATUS_RESULT: ${{ needs.publish-status.result }}', text)
        self.assertIn('ATTEMPT_STATUS: ${{ needs.refresh.outputs.attempt_status }}', text)
        self.assertIn('cmp "$status_out" "$RUNNER_TEMP/routerai-status-readback.json"', text)
        self.assertLess(text.index("Publish managed status branch"), text.index("Update RouterAI PR summary"))

    def test_watchdog_is_independent_and_narrowly_privileged(self) -> None:
        text = WATCHDOG.read_text(encoding="utf-8")
        self.assertIn("workflow_run:", text)
        self.assertIn("schedule:", text)
        self.assertIn("actions: read", text)
        self.assertIn("issues: write", text)
        self.assertNotIn("contents: write", text)
        self.assertIn("--paginate --slurp", text)
        self.assertIn("routerai_operational_watchdog.py", text)
        self.assertIn("Verify issue reconciliation by read-back", text)
        self.assertIn("verifying actual state by read-back", text)


if __name__ == "__main__":
    unittest.main()
