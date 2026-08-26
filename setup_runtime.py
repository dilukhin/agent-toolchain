"""Compatibility facade for unchanged OpenCode/npm policy and legacy direct callers."""
from __future__ import annotations

import os

import setup_runtime_legacy as _legacy

Reporter = _legacy.Reporter
STATE_CONFIGURED = _legacy.STATE_CONFIGURED
STATE_CONFLICT = _legacy.STATE_CONFLICT
STATE_FAILED = _legacy.STATE_FAILED
STATE_INFO = _legacy.STATE_INFO
STATE_MISSING = _legacy.STATE_MISSING
STATE_OK = _legacy.STATE_OK
STATE_OUTDATED = _legacy.STATE_OUTDATED
STATE_SKIPPED = _legacy.STATE_SKIPPED

shutil = _legacy.shutil
run = _legacy.run
executable_inventory = _legacy.executable_inventory
active_instance = _legacy.active_instance
duplicate_recommendation = _legacy.duplicate_recommendation
isolated_manager_detail = _legacy.isolated_manager_detail
render_instances = _legacy.render_instances
report_common_tool_inventory = _legacy.report_common_tool_inventory
_known_opencode_managers = _legacy._known_opencode_managers
installed_version = _legacy.installed_version
_module_origin = _legacy._module_origin


def _sync_legacy_policy() -> None:
    _legacy.run = run
    _legacy.shutil = shutil
    _legacy.executable_inventory = executable_inventory
    _legacy.active_instance = active_instance
    _legacy.duplicate_recommendation = duplicate_recommendation
    _legacy.isolated_manager_detail = isolated_manager_detail
    _legacy.render_instances = render_instances
    _legacy.report_common_tool_inventory = report_common_tool_inventory
    _legacy._known_opencode_managers = _known_opencode_managers
    _legacy._module_origin = _module_origin


def reconcile_npm(*args, **kwargs):
    _sync_legacy_policy()
    return _legacy.reconcile_npm(*args, **kwargs)


def _reconcile_opencode_cli(*args, **kwargs):
    _sync_legacy_policy()
    return _legacy._reconcile_opencode_cli(*args, **kwargs)


def _tool_runtime_preconciled(skip_install: bool) -> bool:
    return skip_install and os.environ.get("AGENT_TOOLCHAIN_RUNTIME_PRECONCILED") == "1"


def ensure_ssh_relay_runtime(repo, python_exe, reporter, check, skip_install):
    if _tool_runtime_preconciled(skip_install):
        return
    _sync_legacy_policy()
    return _legacy.ensure_ssh_relay_runtime(repo, python_exe, reporter, check, skip_install)


def ensure_agent_safe_runtime(repo, python_exe, reporter, check, skip_install):
    if _tool_runtime_preconciled(skip_install):
        return
    _sync_legacy_policy()
    return _legacy.ensure_agent_safe_runtime(repo, python_exe, reporter, check, skip_install)
