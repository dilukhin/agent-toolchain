"""Runtime facade: managed CLI tool runtimes plus unchanged OpenCode/npm policy."""
from __future__ import annotations

import setup_managed_tools as _managed
import setup_runtime_legacy as _legacy

# Re-export the established runtime-policy surface so existing focused tests and callers
# continue to patch the same names while helper-tool deployment moves to a new layer.
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


def reconcile_npm(*args, **kwargs):
    _sync_legacy_policy()
    return _legacy.reconcile_npm(*args, **kwargs)


def _reconcile_opencode_cli(*args, **kwargs):
    _sync_legacy_policy()
    return _legacy._reconcile_opencode_cli(*args, **kwargs)


def ensure_ssh_relay_runtime(repo, python_exe, reporter, check, skip_install):
    _managed.run = run
    _legacy.run = run
    return _managed.ensure_ssh_relay_runtime(repo, python_exe, reporter, check, skip_install)


def ensure_agent_safe_runtime(repo, python_exe, reporter, check, skip_install):
    _managed.run = run
    _legacy.run = run
    return _managed.ensure_agent_safe_runtime(repo, python_exe, reporter, check, skip_install)
