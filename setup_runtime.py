"""Compatibility facade for unchanged OpenCode/npm policy and legacy direct callers."""
from __future__ import annotations

import os
import subprocess

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
_legacy_resolve_npm_target = _legacy._resolve_npm_target

_NPM_METADATA_TIMEOUT_SECONDS = 30.0
_NPM_METADATA_ENV = {
    "npm_config_fetch_timeout": "10000",
    "npm_config_fetch_retries": "1",
    "npm_config_fetch_retry_mintimeout": "1000",
    "npm_config_fetch_retry_maxtimeout": "5000",
}
_last_npm_metadata_error: str | None = None


def _call_runtime_run(cmd, *, cwd=None, env=None, timeout=None):
    kwargs = {}
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if timeout is not None:
        try:
            return run(cmd, timeout=timeout, **kwargs)
        except TypeError as exc:
            # Unit-test fakes from the compatibility layer predate timeout support.
            if "unexpected keyword argument 'timeout'" not in str(exc):
                raise
    return run(cmd, **kwargs)


def _safe_npm_metadata_error(cp) -> str:
    text = f"{cp.stdout}\n{cp.stderr}".lower()
    if any(token in text for token in ("etimedout", "timeout", "timed out")):
        return "network timeout"
    if any(token in text for token in ("eai_again", "enotfound", "getaddrinfo", "dns")):
        return "DNS/network lookup failed"
    if any(token in text for token in ("ssl", "tls", "certificate", "cert_")):
        return "TLS/SSL failure"
    if any(token in text for token in ("econnreset", "econnrefused", "socket hang up")):
        return "network connection failed"
    if any(token in text for token in ("e401", "e403", "unauthorized", "forbidden")):
        return "registry authentication/authorization failed"
    return f"npm view failed with exit code {cp.returncode}"


def _bounded_legacy_run(cmd, cwd=None, env=None, timeout=None):
    global _last_npm_metadata_error
    is_npm_metadata = len(cmd) >= 2 and str(cmd[1]).lower() == "view"
    if not is_npm_metadata:
        return _call_runtime_run(cmd, cwd=cwd, env=env, timeout=timeout)

    bounded_env = dict(os.environ)
    if env is not None:
        bounded_env.update(env)
    bounded_env.update(_NPM_METADATA_ENV)
    effective_timeout = _NPM_METADATA_TIMEOUT_SECONDS
    if timeout is not None:
        effective_timeout = min(float(timeout), _NPM_METADATA_TIMEOUT_SECONDS)

    try:
        cp = _call_runtime_run(cmd, cwd=cwd, env=bounded_env, timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        _last_npm_metadata_error = f"timeout after {effective_timeout:g}s"
        return subprocess.CompletedProcess(cmd, 124, "", _last_npm_metadata_error)

    if cp.returncode != 0:
        _last_npm_metadata_error = _safe_npm_metadata_error(cp)
    elif not cp.stdout.strip():
        _last_npm_metadata_error = "npm returned an empty metadata response"
    else:
        _last_npm_metadata_error = None
    return cp


def _standalone_opencode_version() -> str | None:
    items = executable_inventory("opencode")
    if len(items) != 1:
        return None
    active = active_instance(items)
    if active is None or active.manager == "npm":
        return None
    return _legacy._version_number(active.version)


def _resolve_npm_target(npm: str, package: str, configured: object) -> str | None:
    policy = str(configured or "latest").strip()
    if package != "@opencode-ai/plugin" or policy.lower() != "latest":
        return _legacy_resolve_npm_target(npm, package, configured)

    items = executable_inventory("opencode")
    if len(items) > 1:
        # Plugin compatibility target is ambiguous when CLI ownership/resolution is ambiguous.
        return None

    active = active_instance(items)
    if active is None or active.manager == "npm":
        # npm-managed OpenCode is reconciled to npm latest before plugin reconciliation.
        return _legacy_resolve_npm_target(npm, package, configured)

    opencode_version = _legacy._version_number(active.version)
    if opencode_version is None:
        return None

    exact_package = f"{package}@{opencode_version}"
    published = _legacy._npm_latest_version(npm, exact_package)
    return opencode_version if published == opencode_version else None


def _annotate_npm_metadata_failure(reporter, start_index: int) -> None:
    if not _last_npm_metadata_error:
        return
    for result in reporter.results[start_index:]:
        if result.state != STATE_CONFLICT:
            continue
        if not (
            "не удалось определить целевую версию" in result.detail
            or "не удалось получить актуальную версию npm-пакета" in result.detail
        ):
            continue
        result.state = STATE_FAILED
        result.detail = f"{result.detail}; npm metadata lookup: {_last_npm_metadata_error}"


def _annotate_plugin_version_match(reporter, start_index: int) -> None:
    opencode_version = _standalone_opencode_version()
    if opencode_version is None:
        return
    for result in reporter.results[start_index:]:
        if result.component != "OpenCode plugin":
            continue
        result.detail = result.detail.replace(
            " (npm latest)",
            f" (совпадает с OpenCode {opencode_version})",
        )
        if result.state in {STATE_CONFIGURED, STATE_MISSING, STATE_OUTDATED}:
            marker = f"целевая версия следует активному OpenCode {opencode_version}"
            if marker not in result.detail:
                result.detail = f"{result.detail}; {marker}"


def _sync_legacy_policy() -> None:
    _legacy.run = _bounded_legacy_run
    _legacy.shutil = shutil
    _legacy.executable_inventory = executable_inventory
    _legacy.active_instance = active_instance
    _legacy.duplicate_recommendation = duplicate_recommendation
    _legacy.isolated_manager_detail = isolated_manager_detail
    _legacy.render_instances = render_instances
    _legacy.report_common_tool_inventory = report_common_tool_inventory
    _legacy._known_opencode_managers = _known_opencode_managers
    _legacy._module_origin = _module_origin
    _legacy._resolve_npm_target = _resolve_npm_target


def reconcile_npm(config_dir, config, reporter, check, skip):
    global _last_npm_metadata_error
    _last_npm_metadata_error = None
    start_index = len(reporter.results)
    _sync_legacy_policy()
    result = _legacy.reconcile_npm(config_dir, config, reporter, check, skip)
    _annotate_npm_metadata_failure(reporter, start_index)
    _annotate_plugin_version_match(reporter, start_index)
    return result


def _reconcile_opencode_cli(config, reporter, check, npm):
    global _last_npm_metadata_error
    _last_npm_metadata_error = None
    start_index = len(reporter.results)
    _sync_legacy_policy()
    result = _legacy._reconcile_opencode_cli(config, reporter, check, npm)
    _annotate_npm_metadata_failure(reporter, start_index)
    return result


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
