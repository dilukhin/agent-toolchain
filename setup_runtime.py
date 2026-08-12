"""Runtime/package checks for opencode_setup."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from setup_lib import Reporter, STATE_CONFLICT, STATE_MISSING, STATE_OK, STATE_OUTDATED, run


def ensure_ssh_relay_runtime(repo: Path, python_exe: str, reporter: Reporter,
                             check: bool, skip_install: bool) -> None:
    if sys.version_info < (3, 12):
        reporter.add("ssh_relay runtime", STATE_CONFLICT, "ssh_relay requires Python 3.12+")
        return
    if skip_install:
        reporter.add("ssh_relay runtime", STATE_OK, "dependency install/validation skipped")
        return
    import_test = run([python_exe, "-c", "import paramiko"])
    if import_test.returncode != 0:
        reporter.add("ssh_relay runtime", STATE_MISSING, "Python dependency paramiko is missing")
        if check:
            return
        install = run([python_exe, "-m", "pip", "install", "paramiko"])
        if install.returncode != 0:
            reporter.add("ssh_relay runtime install", STATE_CONFLICT, install.stderr.strip()[-400:])
            return
    version = run([python_exe, str(repo / "ssh_relay.py"), "--version"])
    help_cp = run([python_exe, str(repo / "ssh_relay.py"), "--help"])
    if version.returncode != 0 or help_cp.returncode != 0 or "job" not in help_cp.stdout:
        reporter.add("ssh_relay runtime validation", STATE_CONFLICT, "--version/--help failed or job command is absent")
    else:
        reporter.add("ssh_relay runtime" if import_test.returncode == 0 else "ssh_relay runtime validation",
                     STATE_OK, version.stdout.strip() or "validated")


def ensure_agent_safe_runtime(repo: Path, python_exe: str, reporter: Reporter,
                              check: bool, skip_install: bool) -> None:
    if skip_install:
        reporter.add("agent-safe runtime", STATE_OK, "dependency install/validation skipped")
        return
    imported = run([python_exe, "-c", "import agent_safe"])
    if imported.returncode != 0:
        reporter.add("agent-safe runtime", STATE_MISSING, "editable package is not installed")
        if check:
            return
        install = run([python_exe, "-m", "pip", "install", "-e", str(repo)])
        if install.returncode != 0:
            reporter.add("agent-safe runtime install", STATE_CONFLICT, install.stderr.strip()[-400:])
            return
    help_cp = run([python_exe, "-m", "agent_safe", "--help"])
    if help_cp.returncode != 0:
        reporter.add("agent-safe runtime validation", STATE_CONFLICT, "python -m agent_safe --help failed")
    else:
        reporter.add("agent-safe runtime" if imported.returncode == 0 else "agent-safe runtime validation",
                     STATE_OK, "safe CLI import/help validated")


def installed_version(package_json: Path) -> str | None:
    try:
        value = json.loads(package_json.read_text(encoding="utf-8")).get("version")
        return value if isinstance(value, str) else None
    except Exception:
        return None


def reconcile_npm(config_dir: Path, config: dict[str, Any], reporter: Reporter,
                  check: bool, skip: bool) -> None:
    if skip:
        reporter.add("OpenCode npm packages", STATE_OK, "package install/check skipped")
        return
    npm = shutil.which("npm")
    if not npm:
        reporter.add("OpenCode npm packages", STATE_CONFLICT, "npm is not available")
        return
    if shutil.which("opencode") is None:
        reporter.add("OpenCode CLI", STATE_MISSING, "@opencode-ai/cli")
        if not check:
            cp = run([npm, "install", "-g", "@opencode-ai/cli"])
            if cp.returncode != 0:
                reporter.add("OpenCode CLI install", STATE_CONFLICT, cp.stderr.strip()[-400:])
    else:
        reporter.add("OpenCode CLI", STATE_OK if check else STATE_OUTDATED,
                     "installed" if check else "refresh to current npm release")
        if not check:
            cp = run([npm, "install", "-g", "@opencode-ai/cli"])
            if cp.returncode != 0:
                reporter.add("OpenCode CLI update", STATE_CONFLICT, cp.stderr.strip()[-400:])

    target = str(config["dependencies"]["@opencode-ai/plugin"])
    package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
    current = installed_version(package_json)
    if current == target:
        reporter.add("OpenCode plugin", STATE_OK, target)
        return
    reporter.add("OpenCode plugin", STATE_MISSING if current is None else STATE_OUTDATED,
                 f"target {target}, installed {current or 'none'}")
    if not check:
        config_dir.mkdir(parents=True, exist_ok=True)
        cp = run([npm, "install", "--prefix", str(config_dir), "--save-exact", f"@opencode-ai/plugin@{target}"])
        if cp.returncode != 0:
            reporter.add("OpenCode plugin install", STATE_CONFLICT, cp.stderr.strip()[-400:])
