from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_runtime as runtime  # noqa: E402


class RussianActionGuidanceTests(unittest.TestCase):
    def test_routerai_labels_are_russian_and_collapse_to_apply(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "RouterAI model labels",
            runtime.STATE_OUTDATED,
            "13 managed label(s) differ; ordinary apply will update only recognized managed names",
        )
        result = reporter.results[-1]

        runtime._localize_actionable_detail(result)
        summary = runtime._format_tldr(reporter.results)

        self.assertIn("управляемые подписи моделей устарели: 13", result.detail)
        self.assertIn("пользовательские названия сохранит", result.detail)
        self.assertNotIn("managed label", result.detail)
        self.assertNotIn("ordinary apply", result.detail)
        self.assertEqual(summary.count("выполнить `toolchainctl apply`"), 1)
        self.assertNotIn("проверить «RouterAI model labels»", summary)

    def test_global_agents_conflict_gives_explicit_choice(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "global AGENTS.md",
            runtime.STATE_CONFLICT,
            "managed file was modified locally; preserved",
        )
        result = reporter.results[-1]

        with mock.patch.dict(os.environ, {"OPENCODE_CONFIG_DIR": "/tmp/opencode"}):
            runtime._localize_actionable_detail(result)
            summary = runtime._format_tldr(reporter.results)

        self.assertIn("управляемый файл изменён локально", result.detail)
        self.assertIn("/tmp/opencode/AGENTS.md", result.detail)
        self.assertIn("обычный `toolchainctl apply` этот конфликт не устранит", result.detail)
        self.assertNotIn("managed file", result.detail)
        self.assertIn("если локальные правки не нужны", summary)
        self.assertIn("`toolchainctl apply --force`", summary)
        self.assertIn("если нужны — сначала сохранить их вручную", summary)
        self.assertNotIn("исправить «global AGENTS.md»", summary)

    def test_generic_managed_file_conflict_is_russian_and_actionable(self) -> None:
        reporter = runtime.Reporter()
        reporter.add("managed sample", runtime.STATE_CONFLICT, "managed file was modified locally; preserved")
        result = reporter.results[-1]

        runtime._localize_actionable_detail(result)
        summary = runtime._format_tldr(reporter.results)

        self.assertEqual(result.detail, "управляемый файл изменён локально; файл сохранён без перезаписи")
        self.assertIn("разобраться с «managed sample»", summary)
        self.assertIn("`toolchainctl apply --force`", summary)


if __name__ == "__main__":
    unittest.main()
