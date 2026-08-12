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


def _module_origin(python_exe: str, module: str) -> Path | None:
    code = ("import pathlib, " + module + "; "
            + "print(pathlib.Path(" + module + ".__file__).resolve())")
    cp = run([python_exe, "-c", code])
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        return Path(cp.stdout.strip()).resolve()
    except OSError:
        return None


def ensure_agent_safe_runtime(repo: Path, python_exe: str, reporter: Reporter,
                              check: bool, skip_install: bool) -> None:
    if skip_install:
        reporter.add("agent-safe runtime", STATE_OK, "dependency install/validation skipped")
        return

    repo_resolved = repo.resolve()
    origin = _module_origin(python_exe, "agent_safe")
    managed_import = origin is not None and origin.is_relative_to(repo_resolved)
    if not managed_import:
        state = STATE_MISSING if origin is None else STATE_OUTDATED
        detail = "editable package is not installed" if origin is None else f"import resolves outside managed repo: {origin}"
        reporter.add("agent-safe runtime", state, detail)
        if check:
            return
        install = run([python_exe, "-m", "pip", "install", "-e", str(repo)])
        if install.returncode != 0:
            reporter.add("agent-safe runtime install", STATE_CONFLICT, install.stderr.strip()[-400:])
            return
        origin = _module_origin(python_exe, "agent_safe")
        if origin is None or not origin.is_relative_to(repo_resolved):
            reporter.add("agent-safe runtime validation", STATE_CONFLICT,
                         "editable install completed but import is not sourced from managed repository")
            return

    help_cp = run([python_exe, "-m", "agent_safe", "--help"])
    if help_cp.returncode != 0:
        reporter.add("agent-safe runtime validation", STATE_CONFLICT, "python -m agent_safe --help failed")
    else:
        reporter.add("agent-safe runtime" if managed_import else "agent-safe runtime validation",
                     STATE_OK, "managed editable import/help validated")


def installed_version(package_json: Path) -> str | None:
    try:
        value = json.loads(package_json.read_text(encoding="utf-8")).get("version")
        return value if isinstance(value, str) else None
    except Exception:
        return None


def _npm_global_version(npm: str, package: str) -> str | None:
    cp = run([npm, "list", "-g", "--depth=0", "--json", package])
    if not cp.stdout.strip():
        return None
    try:
        data = json.loads(cp.stdout)
        value = data.get("dependencies", {}).get(package, {}).get("version")
        return value if isinstance(value, str) else None
    except (json.JSONDecodeError, AttributeError):
        return None


def _npm_latest_version(npm: str, package: str) -> str | None:
    cp = run([npm, "view", package, "version", "--json"])
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    try:
        value = json.loads(cp.stdout)
        return value if isinstance(value, str) and value else None
    except json.JSONDecodeError:
        return None


def _resolve_npm_target(npm: str, package: str, configured: object) -> str | None:
    policy = str(configured or "latest").strip()
    if policy.lower() == "latest":
        return _npm_latest_version(npm, package)
    return policy


def reconcile_npm(config_dir: Path, config: dict[str, Any], reporter: Reporter,
                  check: bool, skip: bool) -> None:
    if skip:
        reporter.add("OpenCode npm packages", STATE_OK, "package install/check skipped")
        return
    npm = shutil.which("npm")
    if not npm:
        reporter.add("OpenCode npm packages", STATE_CONFLICT, "npm is not available")
        return

    cli_package = str(config["dependencies"].get("opencode-cli-package", "opencode-ai"))
    current_cli = _npm_global_version(npm, cli_package)
    latest_cli = _npm_latest_version(npm, cli_package)
    if latest_cli is None:
        reporter.add("OpenCode CLI", STATE_CONFLICT, f"cannot query npm registry for {cli_package}")
    elif current_cli == latest_cli:
        reporter.add("OpenCode CLI", STATE_OK, f"{cli_package}@{current_cli}")
    else:
        state = STATE_MISSING if current_cli is None else STATE_OUTDATED
        reporter.add("OpenCode CLI", state,
                     f"target {latest_cli}, installed {current_cli or 'none'}")
        if not check:
            cp = run([npm, "install", "-g", f"{cli_package}@{latest_cli}"])
            if cp.returncode != 0:
                reporter.add("OpenCode CLI install", STATE_CONFLICT, cp.stderr.strip()[-400:])
            elif _npm_global_version(npm, cli_package) != latest_cli:
                reporter.add("OpenCode CLI validation", STATE_CONFLICT, "npm install completed but target version is not active")

    plugin_package = "@opencode-ai/plugin"
    configured = config["dependencies"].get(plugin_package, "latest")
    target = _resolve_npm_target(npm, plugin_package, configured)
    if target is None:
        reporter.add("OpenCode plugin", STATE_CONFLICT,
                     f"cannot resolve npm target for {plugin_package} policy {configured!r}")
        return

    package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
    current = installed_version(package_json)
    if current == target:
        suffix = " (npm latest)" if str(configured).lower() == "latest" else ""
        reporter.add("OpenCode plugin", STATE_OK, target + suffix)
        return
    reporter.add("OpenCode plugin", STATE_MISSING if current is None else STATE_OUTDATED,
                 f"target {target}, installed {current or 'none'}")
    if not check:
        config_dir.mkdir(parents=True, exist_ok=True)
        cp = run([npm, "install", "--prefix", str(config_dir), "--save-exact", f"{plugin_package}@{target}"])
        if cp.returncode != 0:
            reporter.add("OpenCode plugin install", STATE_CONFLICT, cp.stderr.strip()[-400:])
        elif installed_version(package_json) != target:
            reporter.add("OpenCode plugin validation", STATE_CONFLICT,
                         "npm install completed but target plugin version is not active")
