from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

import setup_external_updates as updates  # noqa: E402


class RouterAiStatusTests(unittest.TestCase):
    def _status(self) -> dict:
        return {
            "_managed_notice": "managed; docs/routerai_refresh_status_design_ru.md",
            "schema": 1,
            "updated_at": "2026-08-29T03:18:12Z",
            "published": {
                "catalog_observed_at": "2026-08-28T15:22:09Z",
                "main_sha": "a" * 40,
            },
            "last_successful_check": {
                "at": "2026-08-29T03:18:12Z",
                "catalog_changed": False,
            },
            "last_attempt": {
                "at": "2026-08-29T03:18:12Z",
                "trigger": "schedule",
                "status": "success",
                "phase": "complete",
                "error": None,
            },
            "candidate": None,
        }

    def test_fresh_status_is_short_and_russian(self) -> None:
        status = self._status()
        now = updates._parse_utc("2026-08-29T09:18:12Z")
        assert now is not None
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at="2026-08-28T15:22:09Z",
            now=now,
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "RouterAI: каталог проверен 6 ч. назад.")

    def test_failed_attempt_preserves_last_good_meaning(self) -> None:
        status = self._status()
        status["last_attempt"] = {
            "at": "2026-08-30T03:18:12Z",
            "trigger": "schedule",
            "status": "failed",
            "phase": "fetch-routerai",
            "error": {
                "code": "routerai-timeout",
                "summary": "RouterAI не ответил за отведённое время.",
                "advice": "Повторите штатное обновление.",
            },
        }
        now = updates._parse_utc("2026-08-30T04:18:12Z")
        assert now is not None
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at="2026-08-28T15:22:09Z",
            now=now,
        )
        text = "\n".join(lines)
        self.assertIn("последняя попытка", text)
        self.assertIn("последняя успешная проверка", text)
        self.assertIn("Используются последние опубликованные цены", text)

    def test_published_newer_than_installed_suggests_toolchain_update(self) -> None:
        status = self._status()
        status["published"]["catalog_observed_at"] = "2026-08-29T10:00:00Z"
        now = updates._parse_utc("2026-08-29T11:00:00Z")
        assert now is not None
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at="2026-08-28T15:22:09Z",
            now=now,
        )
        self.assertEqual(lines[-2:], [
            "RouterAI: доступно обновление каталога.",
            "Выполните: toolchainctl update --apply",
        ])
        self.assertNotIn("Сейчас обновление не требуется.", lines)

    def test_candidate_is_reported_without_replacing_published_data(self) -> None:
        status = self._status()
        status["candidate"] = {
            "catalog_observed_at": "2026-08-29T10:00:00Z",
            "branch_sha": "b" * 40,
            "pr_number": 31,
            "validation": "success",
        }
        now = updates._parse_utc("2026-08-29T11:00:00Z")
        assert now is not None
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at="2026-08-28T15:22:09Z",
            now=now,
        )
        text = "\n".join(lines)
        self.assertEqual(lines, [
            "RouterAI: каталог проверен 7 ч. назад.",
            "Найдено обновление, ожидающее публикации в основной ветке agent-toolchain.",
            "Сейчас обновление не требуется.",
        ])
        self.assertNotIn("цены актуальны", text)
        self.assertNotIn("toolchainctl update", text)
        self.assertNotIn("PR", text)

    def test_pending_candidate_does_not_hide_published_update(self) -> None:
        status = self._status()
        status["published"]["catalog_observed_at"] = "2026-08-29T09:00:00Z"
        status["candidate"] = {
            "catalog_observed_at": "2026-08-29T10:00:00Z",
            "validation": "success",
        }
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at="2026-08-28T15:22:09Z",
            now=updates._parse_utc("2026-08-29T11:00:00Z"),
        )
        self.assertIn("Выполните: toolchainctl update --apply", lines)
        self.assertNotIn("Сейчас обновление не требуется.", lines)

    def test_unpublished_candidate_never_suggests_installation(self) -> None:
        for validation in ("pending", "failed"):
            with self.subTest(validation=validation):
                status = self._status()
                status["candidate"] = {
                    "catalog_observed_at": "2026-08-29T10:00:00Z",
                    "validation": validation,
                    "pr_number": 31,
                }
                lines = updates.routerai_status_advisory(
                    status,
                    installed_observed_at="2026-08-28T15:22:09Z",
                    now=updates._parse_utc("2026-08-29T11:00:00Z"),
                )
                self.assertIn("Сейчас обновление не требуется.", lines)
                self.assertNotIn("toolchainctl update", "\n".join(lines))
                self.assertNotIn("PR", "\n".join(lines))

    def test_unknown_installed_catalog_does_not_claim_no_update_required(self) -> None:
        status = self._status()
        status["candidate"] = {
            "catalog_observed_at": "2026-08-29T10:00:00Z",
            "validation": "success",
        }
        lines = updates.routerai_status_advisory(
            status,
            installed_observed_at=None,
            now=updates._parse_utc("2026-08-29T11:00:00Z"),
        )
        self.assertNotIn("Сейчас обновление не требуется.", lines)
        self.assertNotIn("toolchainctl update", "\n".join(lines))

    def test_cache_fallback_survives_remote_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            cache = {
                "schema": updates.ROUTERAI_STATUS_CACHE_SCHEMA,
                "fetched_at": 100.0,
                "status": self._status(),
            }
            path.write_text(json.dumps(cache), encoding="utf-8")
            with mock.patch.object(
                updates,
                "refresh_routerai_status",
                side_effect=RuntimeError("сеть недоступна"),
            ):
                status, meta = updates.get_routerai_status(
                    path=path,
                    now=100.0 + updates.ROUTERAI_STATUS_TTL + 1,
                )
            self.assertIsNotNone(status)
            self.assertEqual(meta["fetch_error"], "сеть недоступна")


if __name__ == "__main__":
    unittest.main()
