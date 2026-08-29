from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _write_console(text: str) -> None:
    """Write unittest diagnostics as UTF-8 even under legacy Windows console encodings."""
    try:
        sys.stdout.write(text)
        return
    except UnicodeEncodeError:
        pass
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8", errors="replace"))
        buffer.flush()
        return
    sys.stdout.write(text.encode("ascii", errors="backslashreplace").decode("ascii"))


def _run_checked(argv: list[str], *, title: str) -> int:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout.decode("utf-8", errors="replace")
    if output:
        _write_console(output)
    if completed.returncode != 0:
        annotation = output.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        _write_console(f"::error title={title}::{annotation}\n")
    return int(completed.returncode)


def _routerai_repository_guards() -> int:
    verify_rc = _run_checked(
        [sys.executable, str(ROOT / "scripts" / "update_routerai_catalog.py"), "--verify-generated"],
        title="RouterAI generated-state guard",
    )
    if verify_rc != 0:
        return verify_rc

    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return 0
    merge_sha = os.environ.get("GITHUB_SHA")
    head_branch = os.environ.get("GITHUB_HEAD_REF")
    if not merge_sha or not head_branch:
        _write_console(
            "::error title=RouterAI ownership guard::"
            "GitHub pull_request environment is missing GITHUB_SHA/GITHUB_HEAD_REF\n"
        )
        return 2
    return _run_checked(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_routerai_owned_diff.py"),
            "--base-ref",
            f"{merge_sha}^1",
            "--head-ref",
            f"{merge_sha}^2",
            "--head-branch",
            head_branch,
        ],
        title="RouterAI ownership guard",
    )


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_*.py")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    _write_console(output)
    if not result.wasSuccessful():
        annotation = output.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        _write_console(f"::error title=Setup regression::{annotation}\n")
        return 1
    return _routerai_repository_guards()


if __name__ == "__main__":
    raise SystemExit(main())
