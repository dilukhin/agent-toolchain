from __future__ import annotations

import io
import sys
import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_setup_migration")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    sys.stdout.write(output)
    if result.wasSuccessful():
        return 0
    annotation = output.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::error title=Migration regression::{annotation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
