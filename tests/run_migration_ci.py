from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_setup_*.py")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    sys.stdout.write(output)
    if result.wasSuccessful():
        return 0
    annotation = output.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title=Setup regression::{annotation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
