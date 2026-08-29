from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_routerai_refresh_status.py"
SPEC = importlib.util.spec_from_file_location("update_routerai_refresh_status", SCRIPT)
assert SPEC and SPEC.loader
status_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status_mod)


class RouterAiRefreshStatusTests(unittest.TestCase):
    def _previous(self) -> dict:
        return {
            "_managed_notice": status_mod.MANAGED_NOTICE,
            "schema": status_mod.SCHEMA,
            "owner": status_mod.OWNER,
            "updated_at": "2026-08-29T03:00:00Z",
            "published": {
                "catalog_observed_at": "2026-08-28T15:22:09Z",
                "main_sha": "a" * 40,
            },
            "last_successful_check": {
                "at": "2026-08-29T03:00:00Z",
                "catalog_changed": False,
            },
            "last_attempt": {
                "at": "2026-08-29T03:00:00Z",
                "trigger": "schedule",
                "status": "success",
                "phase": "complete",
                "error": None,
            },
            "candidate": None,
        }

    def _build(self, **overrides):
        args = dict(
            previous=self._previous(),
            attempt_at="2026-08-30T03:00:00Z",
            trigger="schedule",
            attempt_status="success",
            phase="complete",
            error_code=None,
            error_summary=None,
            source_check_at="2026-08-30T03:00:00Z",
            catalog_changed=False,
            published_catalog_observed_at="2026-08-28T15:22:09Z",
            published_main_sha="b" * 40,
            candidate_catalog_observed_at=None,
            candidate_sha=None,
            candidate_pr=None,
            candidate_validation="none",
        )
        args.update(overrides)
        return status_mod.build_status(**args)

    def test_failure_preserves_previous_successful_check(self) -> None:
        result = self._build(
            attempt_status="failed",
            phase="fetch-routerai",
            error_code="routerai-refresh",
            error_summary="RouterAI не ответил.",
            source_check_at=None,
            catalog_changed=None,
        )
        self.assertEqual(result["last_successful_check"]["at"], "2026-08-29T03:00:00Z")
        self.assertEqual(result["last_attempt"]["status"], "failed")
        self.assertIn("RouterAI не ответил", result["last_attempt"]["error"]["summary"])

    def test_successful_source_check_updates_freshness_even_if_validation_fails(self) -> None:
        result = self._build(
            trigger="workflow_dispatch",
            attempt_status="failed",
            phase="full-validation",
            error_code="full-validation",
            error_summary="Полная проверка завершилась ошибкой.",
            source_check_at="2026-08-30T02:59:30Z",
            catalog_changed=True,
            candidate_catalog_observed_at="2026-08-30T02:59:30Z",
            candidate_sha="c" * 40,
            candidate_pr=31,
            candidate_validation="failed",
        )
        self.assertEqual(result["last_successful_check"]["at"], "2026-08-30T02:59:30Z")
        self.assertTrue(result["last_successful_check"]["catalog_changed"])
        self.assertEqual(result["candidate"]["validation"], "failed")

    def test_unchanged_candidate_preserves_validation_for_same_sha(self) -> None:
        previous = self._previous()
        previous["candidate"] = {
            "catalog_observed_at": "2026-08-29T03:00:00Z",
            "branch_sha": "c" * 40,
            "pr_number": 30,
            "validation": "success",
        }
        result = self._build(
            previous=previous,
            candidate_catalog_observed_at="2026-08-29T03:00:00Z",
            candidate_sha="c" * 40,
            candidate_pr=30,
            candidate_validation="unchanged",
        )
        self.assertEqual(result["candidate"]["validation"], "success")

    def test_status_contains_human_notice_and_stable_owner(self) -> None:
        result = self._build()
        self.assertEqual(result["owner"], status_mod.OWNER)
        self.assertIn(status_mod.DOC, result["_managed_notice"])
        self.assertIn(status_mod.MANUAL_REFRESH_COMMAND, result["_managed_notice"])

    def test_existing_managed_status_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text(json.dumps(self._previous(), ensure_ascii=False), encoding="utf-8")
            loaded = status_mod.load_previous_status(path)
        self.assertEqual(loaded["owner"], status_mod.OWNER)

    def test_unknown_or_modified_status_fails_closed(self) -> None:
        variants = [
            {"schema": status_mod.SCHEMA, "_managed_notice": status_mod.MANAGED_NOTICE},
            {**self._previous(), "owner": "somebody-else"},
            {**self._previous(), "_managed_notice": "изменено вручную"},
        ]
        for value in variants:
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as td:
                    path = Path(td) / "status.json"
                    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(status_mod.StatusError):
                        status_mod.load_previous_status(path)

    def test_malformed_existing_status_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "status.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(status_mod.StatusError):
                status_mod.load_previous_status(path)


if __name__ == "__main__":
    unittest.main()
