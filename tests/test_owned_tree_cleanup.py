from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_tool_skills_impl as skills  # noqa: E402


class OwnedTemporaryTreeCleanupTests(unittest.TestCase):
    def test_read_only_file_in_owned_tree_can_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "owned-checkout"
            nested = root / ".git" / "objects" / "aa"
            nested.mkdir(parents=True)
            payload = nested / "readonly-object"
            payload.write_bytes(b"git-object")
            payload.chmod(stat.S_IREAD)

            skills._remove_owned_tree(root)

            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
