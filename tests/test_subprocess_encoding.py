from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_lib  # noqa: E402


class SubprocessDecodingTests(unittest.TestCase):
    def test_utf8_stdout_and_stderr_are_decoded_independently_of_windows_ansi_codepage(self) -> None:
        script = (
            "import os; "
            "os.write(1, 'русский вывод'.encode('utf-8')); "
            "os.write(2, 'ошибка'.encode('utf-8'))"
        )
        cp = setup_lib.run([sys.executable, "-B", "-c", script])
        self.assertEqual(cp.returncode, 0)
        self.assertEqual(cp.stdout, "русский вывод")
        self.assertEqual(cp.stderr, "ошибка")

    def test_invalid_utf8_never_crashes_capture(self) -> None:
        script = "import os; os.write(1, bytes([0xff, 0x81, 0xfe]))"
        cp = setup_lib.run([sys.executable, "-B", "-c", script])
        self.assertEqual(cp.returncode, 0)
        self.assertIsInstance(cp.stdout, str)


if __name__ == "__main__":
    unittest.main()
