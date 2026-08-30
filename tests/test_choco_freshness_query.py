from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_external_updates import _latest  # noqa: E402


class ChocolateyFreshnessQueryTests(unittest.TestCase):
    @staticmethod
    def _item():
        item = mock.Mock(active=mock.Mock(provider="chocolatey", version="1.18.18"), conflict=False)
        item.spec.command = "opencode"
        return item

    def test_exact_search_returns_remote_latest_version(self) -> None:
        item = self._item()
        completed = mock.Mock(returncode=0, stdout="opencode|1.18.25\n", stderr="")
        with mock.patch("setup_external_updates.shutil.which", return_value="C:/ProgramData/chocolatey/bin/choco.exe"), mock.patch(
            "setup_external_updates.run", return_value=completed
        ) as run_mock:
            self.assertEqual(_latest(item, 5), ("1.18.25", None))

        run_mock.assert_called_once_with(
            ["C:/ProgramData/chocolatey/bin/choco.exe", "search", "opencode", "--exact", "--limit-output"],
            timeout=5,
        )

    def test_exact_search_no_results_is_not_treated_as_current(self) -> None:
        item = self._item()
        with mock.patch("setup_external_updates.shutil.which", return_value="choco"), mock.patch(
            "setup_external_updates.run",
            return_value=mock.Mock(returncode=2, stdout="", stderr=""),
        ):
            latest, error = _latest(item, 5)

        self.assertIsNone(latest)
        self.assertIn("no results", error)

    def test_exact_search_unexpected_row_fails_closed(self) -> None:
        item = self._item()
        with mock.patch("setup_external_updates.shutil.which", return_value="choco"), mock.patch(
            "setup_external_updates.run",
            return_value=mock.Mock(returncode=0, stdout="other|9.9.9\n", stderr=""),
        ):
            latest, error = _latest(item, 5)

        self.assertIsNone(latest)
        self.assertIn("no matching package row", error)


if __name__ == "__main__":
    unittest.main()
