"""Toolchain-only adapter that suppresses legacy helper checkout management.

The monolithic legacy setup_core still owns OpenCode/npm/config reconciliation.  When
called by toolchainctl, helper runtimes and skills have already been reconciled from
pinned ToolSpecs, so tracking checkouts must not become production dependencies.
"""
from __future__ import annotations

import os
import tempfile
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


def _replace_argv_value(argv: list[str] | None, flag: str, value: str) -> list[str] | None:
    if argv is None:
        return None
    result = list(argv)
    try:
        index = result.index(flag)
    except ValueError:
        result.extend([flag, value])
    else:
        if index + 1 >= len(result):
            result.append(value)
        else:
            result[index + 1] = value
    return result


def _write_legacy_skill_view(root: Path) -> None:
    # setup_core evaluates source.read_bytes() before reconcile_file is called. The
    # files below are inert placeholders: validation and reconciliation for exactly
    # these external helper skills are bypassed because the pinned ToolSpec phase has
    # already validated and reconciled the authoritative payloads.
    relative_paths = (
        "ssh_relay/opencode/skills/ssh-relay/SKILL.md",
        "agent-safe/opencode/skills/recovery-mode/SKILL.md",
        "agent-safe/opencode/skills/risk-gate/SKILL.md",
        "agent-safe/opencode/skills/safe-cli/SKILL.md",
        "agent-safe/opencode/skills/unknown-system-safety/SKILL.md",
    )
    for relative in relative_paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"managed-by-pinned-tool-phase\n")


def _toolchain_main(argv: list[str] | None = None) -> int:
    if os.environ.get(_ENV) != "1":
        return int(_ORIGINAL_MAIN(argv))

    original_repo = setup_core.reconcile_repo
    original_safe_repo = setup_core.reconcile_agent_safe_repo
    original_validate = setup_core.validate_skill
    original_reconcile_file = setup_core.reconcile_file
    original_which = setup_core.shutil.which

    with tempfile.TemporaryDirectory(prefix="agent-toolchain-core-sources-") as td:
        source_view = Path(td)
        _write_legacy_skill_view(source_view)

        def bypass_repo(*, component: str, reporter: Any, **_: Any) -> tuple[bool, str]:
            reporter.add(
                component,
                STATE_SKIPPED,
                "tracking checkout is not a production dependency; pinned ToolSpec phase already reconciled this tool",
            )
            return True, STATE_OK

        def validate_skill(path: Path, expected_name: str) -> tuple[bool, str]:
            try:
                path.resolve().relative_to(source_view.resolve())
            except (OSError, ValueError):
                return original_validate(path, expected_name)
            return True, "validated by pinned ToolSpec phase"

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

        setup_core.reconcile_repo = bypass_repo
        setup_core.reconcile_agent_safe_repo = bypass_repo
        setup_core.validate_skill = validate_skill
        setup_core.reconcile_file = reconcile_file
        setup_core.shutil.which = which
        try:
            adapted = _replace_argv_value(argv, "--projects-dir", str(source_view))
            return int(_ORIGINAL_MAIN(adapted))
        finally:
            setup_core.reconcile_repo = original_repo
            setup_core.reconcile_agent_safe_repo = original_safe_repo
            setup_core.validate_skill = original_validate
            setup_core.reconcile_file = original_reconcile_file
            setup_core.shutil.which = original_which


def install() -> None:
    """Install the adapter once in the current process."""
    if setup_core.main is not _toolchain_main:
        setup_core.main = _toolchain_main
