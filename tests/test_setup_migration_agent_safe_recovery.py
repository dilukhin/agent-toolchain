from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_core import reconcile_agent_safe_repo  # noqa: E402
from setup_lib import Reporter, STATE_CONFLICT, STATE_OK, STATE_OUTDATED  # noqa: E402


def run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")
    return cp


class AgentSafeEggInfoRecoveryTests(unittest.TestCase):
    def _make_repos(self, root: Path) -> tuple[Path, Path, Path]:
        seed = root / "seed"
        bare = root / "remote.git"
        work = root / "work"

        run_git("init", "-q", "-b", "main", str(seed))
        run_git("-C", str(seed), "config", "user.email", "test@example.invalid")
        run_git("-C", str(seed), "config", "user.name", "test")
        (seed / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        (seed / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        run_git("-C", str(seed), "add", ".")
        run_git("-C", str(seed), "commit", "-qm", "initial")
        run_git("clone", "-q", "--bare", str(seed), str(bare))
        run_git("clone", "-q", "--branch", "main", str(bare), str(work))
        return seed, bare, work

    def _publish_ignore_fix(self, seed: Path, bare: Path) -> str:
        (seed / ".gitignore").write_text("__pycache__/\n*.egg-info/\n", encoding="utf-8")
        run_git("-C", str(seed), "add", ".gitignore")
        run_git("-C", str(seed), "commit", "-qm", "ignore egg info")
        run_git("-C", str(seed), "push", "-q", str(bare), "main")
        return run_git("-C", str(seed), "rev-parse", "HEAD").stdout.strip()

    def test_check_and_apply_recover_legacy_egg_info_without_deleting_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            seed, bare, work = self._make_repos(Path(td))
            egg_info = work / "src" / "agent_safe.egg-info"
            egg_info.mkdir(parents=True)
            (egg_info / "PKG-INFO").write_text("generated\n", encoding="utf-8")

            before = run_git("-C", str(work), "status", "--porcelain=v1", "--untracked-files=all").stdout
            self.assertIn("src/agent_safe.egg-info/PKG-INFO", before)

            remote_head = self._publish_ignore_fix(seed, bare)

            check_reporter = Reporter()
            usable, state = reconcile_agent_safe_repo(
                component="agent-safe repository",
                path=work,
                url=str(bare),
                branch="main",
                reporter=check_reporter,
                check=True,
            )
            self.assertTrue(usable)
            self.assertEqual(state, STATE_OUTDATED)
            self.assertFalse(check_reporter.has_conflict)
            self.assertTrue((egg_info / "PKG-INFO").is_file())

            apply_reporter = Reporter()
            usable, state = reconcile_agent_safe_repo(
                component="agent-safe repository",
                path=work,
                url=str(bare),
                branch="main",
                reporter=apply_reporter,
                check=False,
            )
            self.assertTrue(usable)
            self.assertEqual(state, STATE_OK)
            self.assertFalse(apply_reporter.has_conflict)
            self.assertTrue((egg_info / "PKG-INFO").is_file())
            self.assertEqual(run_git("-C", str(work), "rev-parse", "HEAD").stdout.strip(), remote_head)
            self.assertEqual(
                run_git("-C", str(work), "status", "--porcelain=v1", "--untracked-files=all").stdout,
                "",
            )

    def test_other_untracked_files_still_block_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            seed, bare, work = self._make_repos(Path(td))
            egg_info = work / "src" / "agent_safe.egg-info"
            egg_info.mkdir(parents=True)
            (egg_info / "PKG-INFO").write_text("generated\n", encoding="utf-8")
            (work / "danger.txt").write_text("user data\n", encoding="utf-8")
            self._publish_ignore_fix(seed, bare)

            reporter = Reporter()
            usable, state = reconcile_agent_safe_repo(
                component="agent-safe repository",
                path=work,
                url=str(bare),
                branch="main",
                reporter=reporter,
                check=True,
            )
            self.assertFalse(usable)
            self.assertEqual(state, STATE_CONFLICT)
            self.assertTrue(reporter.has_conflict)
            self.assertTrue((work / "danger.txt").is_file())
            self.assertTrue((egg_info / "PKG-INFO").is_file())


if __name__ == "__main__":
    unittest.main()
