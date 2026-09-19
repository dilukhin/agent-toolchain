"""Shared installed user-state location; no registry path from command input."""
from __future__ import annotations

import os
from pathlib import Path


def state_base() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local).resolve()
        return (Path.home() / ".local" / "state").resolve()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve()
    return (Path.home() / ".local" / "state").resolve()


def canonical_state_dir() -> Path:
    base = state_base()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return base / "agent-toolchain" / "state"
    return base / "agent-toolchain"


def default_state_dir() -> Path:
    # Existing CLI fixture/deployment override. The provider never uses it.
    override = os.environ.get("AGENT_TOOLCHAIN_STATE_DIR")
    if override:
        return Path(override).expanduser().absolute()
    return canonical_state_dir()
