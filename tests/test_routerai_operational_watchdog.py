from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "routerai_operational_watchdog.py"
SPEC = importlib.util.spec_from_file_location("routerai_operational_watchdog", SCRIPT)
assert SPEC and SPEC.loader
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def run(run_id: int, *, conclusion: str, at: str, title: str | None = None, attempt: int = 1):
    return {
        "id": run_id,
        "run_attempt": attempt,
        "display_title": title or watchdog.CONTRACT_RUN_NAME,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": at,
        "updated_at": at,
        "head_sha": f"{run_id:040x}"[-40:],
        "html_url": f"https://github.example/actions/runs/{run_id}",
    }


def status(run_id: int, state: str = "success"):
    return {
        "schema": watchdog.STATUS_SCHEMA,
        "owner": watchdog.STATUS_OWNER,
        "last_attempt": {"run_id": run_id, "status": state},
    }


class RouterAiOperationalWatchdogTests(unittest.TestCase):
    def test_healthy_recent_contract_success(self):
        runs = {"workflow_runs": [run(10, conclusion="success", at="2026-09-22T11:00:00Z")]}
        result = watchdog.evaluate(runs, status(10), now=NOW, stale_hours=72)
        self.assertTrue(result["healthy"])
        self.assertEqual(result["reasons"], [])

    def test_old_pre_contract_success_is_not_accepted(self):
        runs = {"workflow_runs": [
            run(9, conclusion="success", at="2026-09-22T11:00:00Z", title="Refresh RouterAI catalog")
        ]}
        result = watchdog.evaluate(runs, status(9), now=NOW, stale_hours=72)
        self.assertFalse(result["healthy"])
        codes = {item["code"] for item in result["reasons"]}
        self.assertIn("no-contract-run", codes)
        self.assertIn("no-full-success", codes)

    def test_latest_failure_alerts_even_when_previous_success_is_fresh(self):
        runs = {"workflow_runs": [
            run(11, conclusion="failure", at="2026-09-22T11:00:00Z"),
            run(10, conclusion="success", at="2026-09-22T10:00:00Z"),
        ]}
        result = watchdog.evaluate(runs, status(11, "failed"), now=NOW, stale_hours=72)
        self.assertFalse(result["healthy"])
        self.assertIn("latest-run-failed", {item["code"] for item in result["reasons"]})
        self.assertNotIn("stale-full-success", {item["code"] for item in result["reasons"]})

    def test_staleness_is_based_on_full_success_not_status_observed_at(self):
        runs = {"workflow_runs": [run(10, conclusion="success", at="2026-09-18T11:00:00Z")]}
        rich_status = status(10)
        rich_status["published"] = {"catalog_observed_at": "2026-09-22T11:30:00Z"}
        result = watchdog.evaluate(runs, rich_status, now=NOW, stale_hours=72)
        self.assertIn("stale-full-success", {item["code"] for item in result["reasons"]})

    def test_status_must_correspond_to_latest_completed_run(self):
        runs = {"workflow_runs": [run(12, conclusion="success", at="2026-09-22T11:00:00Z")]}
        result = watchdog.evaluate(runs, status(11), now=NOW, stale_hours=72)
        self.assertIn("status-out-of-sync", {item["code"] for item in result["reasons"]})

    def test_create_then_noop_is_idempotent(self):
        evaluation = watchdog.evaluate(
            {"workflow_runs": [run(12, conclusion="failure", at="2026-09-22T11:00:00Z")]},
            status(12, "failed"), now=NOW, stale_hours=72,
        )
        first = watchdog.reconcile(evaluation, [])
        self.assertEqual(first["action"], "create")
        issue = {
            "number": 90,
            "state": "open",
            "title": first["title"],
            "body": first["body"],
            "user": {"login": watchdog.EXPECTED_ISSUE_AUTHOR},
        }
        second = watchdog.reconcile(evaluation, [issue])
        self.assertEqual(second["action"], "noop")

    def test_recovery_closes_owned_open_issue(self):
        evaluation = watchdog.evaluate(
            {"workflow_runs": [run(13, conclusion="success", at="2026-09-22T11:00:00Z")]},
            status(13), now=NOW, stale_hours=72,
        )
        issue = {
            "number": 90,
            "state": "open",
            "title": watchdog.ISSUE_TITLE,
            "body": watchdog.ISSUE_MARKER + "\nold failure\n",
            "user": {"login": watchdog.EXPECTED_ISSUE_AUTHOR},
        }
        result = watchdog.reconcile(evaluation, [issue])
        self.assertEqual(result, {"action": "close", "number": 90})

    def test_closed_owned_issue_reopens_on_new_failure(self):
        evaluation = watchdog.evaluate(
            {"workflow_runs": [run(14, conclusion="failure", at="2026-09-22T11:00:00Z")]},
            status(14, "failed"), now=NOW, stale_hours=72,
        )
        issue = {
            "number": 90,
            "state": "closed",
            "title": watchdog.ISSUE_TITLE,
            "body": watchdog.ISSUE_MARKER + "\nold failure\n",
            "user": {"login": watchdog.EXPECTED_ISSUE_AUTHOR},
        }
        result = watchdog.reconcile(evaluation, [issue])
        self.assertEqual(result["action"], "reopen")
        self.assertIn(watchdog.ISSUE_MARKER, result["body"])

    def test_foreign_issue_with_owned_marker_fails_closed(self):
        evaluation = {"healthy": False, "reasons": [], "latest_run": None, "last_full_success": None, "stale_hours": 72}
        issue = {
            "number": 91,
            "state": "open",
            "title": watchdog.ISSUE_TITLE,
            "body": watchdog.ISSUE_MARKER,
            "user": {"login": "someone-else"},
        }
        result = watchdog.reconcile(evaluation, [issue])
        self.assertEqual(result["action"], "conflict")

    def test_multiple_owned_issues_fail_closed(self):
        evaluation = {"healthy": False, "reasons": [], "latest_run": None, "last_full_success": None, "stale_hours": 72}
        issues = [
            {"number": 1, "state": "open", "body": watchdog.ISSUE_MARKER, "user": {"login": watchdog.EXPECTED_ISSUE_AUTHOR}},
            {"number": 2, "state": "closed", "body": watchdog.ISSUE_MARKER, "user": {"login": watchdog.EXPECTED_ISSUE_AUTHOR}},
        ]
        result = watchdog.reconcile(evaluation, issues)
        self.assertEqual(result["action"], "conflict")


if __name__ == "__main__":
    unittest.main()
