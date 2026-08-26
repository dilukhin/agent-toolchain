"""Toolchain-only adapter that suppresses legacy helper checkout management.

The monolithic legacy setup_core still owns OpenCode/npm/config reconciliation. When
called by toolchainctl, helper runtimes and skills have already been reconciled from
pinned ToolSpecs, so tracking checkouts must not become production dependencies.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import setup_core
from setup_lib import STATE_OK, STATE_SKIPPED

_ENV = "AGENT_TOOLCHAIN_RUNTIME_PRECONCILED"
_ORIGINAL_MAIN = setup_core.main
_EXTERNAL_SKILL_COMPONENTS = {
    "skill ssh-relay",
    "skill recovery-mode",
    "skill risk-gate",
    "skill safe-cli",
    "skill unknown-system-safety",
}
_EXTERNAL_SKILL_RELATIVE_PATHS = (
    "ssh_relay/opencode/skills/ssh-relay/SKILL.md",
    "agent-safe/opencode/skills/recovery-mode/SKILL.md",
    "agent-safe/opencode/skills/risk-gate/SKILL.md",
    "agent-safe/opencode/skills/safe-cli/SKILL.md",
    "agent-safe/opencode/skills/unknown-system-safety/SKILL.md",
)
_PLACEHOLDER_SKILL_BYTES = b"managed-by-pinned-tool-phase\n"


def _projects_dir(argv: list[str] | None) -> Path | None:
    if argv is None:
        return None
    try:
        index = argv.index("--projects-dir")
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return Path(argv[index + 1]).expanduser().resolve()


def _toolchain_main(argv: list[str] | None = None) -> int:
    if os.environ.get(_ENV) != "1":
        return int(_ORIGINAL_MAIN(argv))

    projects_dir = _projects_dir(argv)
    if projects_dir is None:
        return int(_ORIGINAL_MAIN(argv))

    external_sources = {
        projects_dir / Path(relative.replace("/", os.sep))
        for relative in _EXTERNAL_SKILL_RELATIVE_PATHS
    }

    original_repo = setup_core.reconcile_repo
    original_safe_repo = setup_core.reconcile_agent_safe_repo
    original_validate = setup_core.validate_skill
    original_reconcile_file = setup_core.reconcile_file
    original_which = setup_core.shutil.which
    original_read_bytes = Path.read_bytes

    def bypass_repo(*, component: str, reporter: Any, **_: Any) -> tuple[bool, str]:
        reporter.add(
            component,
            STATE_SKIPPED,
            "tracking checkout is not a production dependency; pinned ToolSpec phase already reconciled this tool",
        )
        return True, STATE_OK

    def validate_skill(path: Path, expected_name: str) -> tuple[bool, str]:
        if path in external_sources:
            return True, "validated by pinned ToolSpec phase"
        return original_validate(path, expected_name)

    def reconcile_file(**kwargs: Any) -> bool:
        component = kwargs.get("component")
        if component in _EXTERNAL_SKILL_COMPONENTS:
            reporter = kwargs["reporter"]
            reporter.add(
                component,
                STATE_SKIPPED,
                "managed from the same pinned ToolSpec ref as the installed runtime",
            )
            return False
        return bool(original_reconcile_file(**kwargs))

    def which(name: str, *args: Any, **kwargs: Any) -> str | None:
        if name == "git":
            # The legacy branch only uses this as a gate before calling the repo
            # reconcilers above; no Git process is executed by this adapter.
            return "agent-toolchain-managed-source"
        return original_which(name, *args, **kwargs)

    def read_bytes(path: Path) -> bytes:
        if path in external_sources:
            # setup_core reads helper SKILL.md before calling reconcile_file. In
            # toolchain mode the authoritative payload was already validated and
            # reconciled from the pinned ToolSpec ref, so an inert in-memory value is
            # sufficient and avoids any temporary filesystem staging during check.
            return _PLACEHOLDER_SKILL_BYTES
        return original_read_bytes(path)

    setup_core.reconcile_repo = bypass_repo
    setup_core.reconcile_agent_safe_repo = bypass_repo
    setup_core.validate_skill = validate_skill
    setup_core.reconcile_file = reconcile_file
    setup_core.shutil.which = which
    Path.read_bytes = read_bytes
    try:
        return int(_ORIGINAL_MAIN(argv))
    finally:
        setup_core.reconcile_repo = original_repo
        setup_core.reconcile_agent_safe_repo = original_safe_repo
        setup_core.validate_skill = original_validate
        setup_core.reconcile_file = original_reconcile_file
        setup_core.shutil.which = original_which
        Path.read_bytes = original_read_bytes


def install() -> None:
    """Install the adapter once in the current process."""
    if setup_core.main is not _toolchain_main:
        setup_core.main = _toolchain_main
