"""Compatibility facade for unchanged OpenCode/npm policy and legacy direct callers."""
from __future__ import annotations

import atexit
import os
import re
import subprocess
import sys

import setup_external_updates as _external_updates
import setup_runtime_legacy as _legacy
from setup_inventory import ExternalCliSpec, external_cli_inventory

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
_external_latest = _external_updates._latest

_NPM_METADATA_TIMEOUT_SECONDS = 30.0
_NPM_METADATA_ENV = {
    "npm_config_fetch_timeout": "10000",
    "npm_config_fetch_retries": "1",
    "npm_config_fetch_retry_mintimeout": "1000",
    "npm_config_fetch_retry_maxtimeout": "5000",
}
_EXTERNAL_UPDATE_TIMEOUT_SECONDS = 5.0
_last_npm_metadata_error: str | None = None

_TLDR_RESULTS: dict[str, object] = {}
_TLDR_REGISTERED = False
_ORIGINAL_REPORTER_RENDER = Reporter.render


def _toolchainctl_tldr_enabled() -> bool:
    if len(sys.argv) < 2 or sys.argv[1] not in {"check", "apply"}:
        return False
    name = os.path.basename(sys.argv[0]).lower()
    return name in {"toolchainctl", "toolchainctl.py"}


def _manual_action(detail: str) -> str | None:
    marker = "MANUAL ACTION REQUIRED:"
    if marker not in detail:
        return None
    action = detail.split(marker, 1)[1].strip().rstrip(".")
    return action or None


def _trim_action(text: str, limit: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _clarify_routerai_placeholder_detail(result) -> None:
    if result.component != "RouterAI credential":
        return
    old_marker = "служебная заглушка предыдущей версии не является API key; запишите реальный ключ RouterAI:"
    if old_marker not in result.detail:
        return
    path = result.detail.split(old_marker, 1)[1].strip().rstrip(".")
    if not path:
        return
    result.detail = (
        "MANUAL ACTION REQUIRED: RouterAI не настроен: замените `your-routerai-api-key-here` в "
        f"{path} на реальный API-ключ RouterAI одной строкой, без `Bearer` и кавычек; "
        "новый ключ создаётся в RouterAI: Настройки → API-ключи"
    )


def _opencode_update_action(result) -> str | None:
    if result.component != "OpenCode CLI" or result.state != STATE_OUTDATED:
        return None
    match = re.search(
        r"установлено ([^;]+); доступно ([^;]+);.*рекомендуемая команда обновления: `([^`]+)`",
        result.detail,
    )
    if match is None:
        return None
    installed, latest, command = (part.strip() for part in match.groups())
    return f"обновить OpenCode {installed} → {latest}: `{command}`"


def _tldr_actions(results) -> list[str]:
    latest: dict[str, object] = {}
    for result in results:
        latest.pop(result.component, None)
        latest[result.component] = result

    actions: list[str] = []
    for result in latest.values():
        manual = _manual_action(result.detail)
        action: str | None = _opencode_update_action(result)
        if action is not None:
            pass
        elif result.component == "RouterAI credential" and manual and "RouterAI не настроен:" in manual:
            action = manual.split("; новый ключ создаётся", 1)[0].strip()
        elif manual is not None:
            action = manual
        elif result.state in {STATE_MISSING, STATE_OUTDATED} and (
            "обычный apply" in result.detail or "toolchainctl apply" in result.detail
        ):
            action = "выполнить `toolchainctl apply`"
        elif result.state == STATE_FAILED and "npm metadata lookup:" in result.detail:
            action = "повторить `toolchainctl apply` после восстановления доступа к npm registry"
        elif result.state in {STATE_FAILED, STATE_CONFLICT}:
            action = f"исправить «{result.component}»: {result.detail}"
        elif result.state in {STATE_MISSING, STATE_OUTDATED}:
            action = f"проверить «{result.component}»: {result.detail}"

        if action is None:
            continue
        action = _trim_action(action)
        if action not in actions:
            actions.append(action)
    return actions


def _format_tldr(results) -> str:
    actions = _tldr_actions(results)
    if not actions:
        return "TL/DR: дополнительных действий не требуется."
    lines = ["TL/DR: рекомендуется:"]
    lines.extend(f"  - {action}" for action in actions[:6])
    if len(actions) > 6:
        lines.append(f"  - ещё {len(actions) - 6} рекомендац. — см. таблицы выше")
    return "\n".join(lines)


def _emit_tldr() -> None:
    if not _TLDR_RESULTS:
        return
    try:
        print()
        print(_format_tldr(_TLDR_RESULTS.values()))
    except Exception:
        # TL/DR is advisory only and must never turn a successful reconciliation into a failure.
        return


def _reporter_render_with_tldr(self, *args, **kwargs):
    global _TLDR_REGISTERED
    if _toolchainctl_tldr_enabled():
        for result in self.results:
            _clarify_routerai_placeholder_detail(result)
            _TLDR_RESULTS.pop(result.component, None)
            _TLDR_RESULTS[result.component] = result
        if not _TLDR_REGISTERED:
            atexit.register(_emit_tldr)
            _TLDR_REGISTERED = True
    return _ORIGINAL_REPORTER_RENDER(self, *args, **kwargs)


Reporter.render = _reporter_render_with_tldr


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


def _version_triplet(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _fix_plugin_change_wording(detail: str) -> str:
    match = re.search(r"обновлён с (\S+) до (\S+) через npm", detail)
    if match is None:
        return detail
    before, after = match.groups()
    before_key = _version_triplet(before)
    after_key = _version_triplet(after)
    if before_key is not None and after_key is not None and after_key < before_key:
        return detail.replace(
            match.group(0),
            f"понижен с {before} до {after} через npm",
            1,
        )
    return detail


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


def _external_opencode_inventory():
    return external_cli_inventory(ExternalCliSpec("opencode", "OpenCode"))


def _annotate_external_opencode_freshness(reporter, start_index: int) -> None:
    try:
        inventory = _external_opencode_inventory()
    except Exception:
        return
    if inventory is None or inventory.active is None or inventory.conflict:
        return
    if inventory.active.provider == "npm" or not inventory.update_advice:
        # npm-owned OpenCode already has its own latest-version reconciliation path.
        return

    installed = _legacy._version_number(inventory.active.version)
    if installed is None:
        return

    try:
        latest, error = _external_latest(inventory, _EXTERNAL_UPDATE_TIMEOUT_SECONDS)
    except Exception:
        latest, error = None, "lookup failed"

    target = next(
        (item for item in reporter.results[start_index:] if item.component == "OpenCode CLI"),
        None,
    )
    if target is None or target.state not in {STATE_OK, STATE_INFO}:
        return

    if error or not latest:
        if target.state == STATE_OK:
            target.state = STATE_INFO
            target.detail += (
                f"; установленная копия исправна, но актуальность версии через {inventory.active.provider} "
                "не удалось подтвердить read-only проверкой"
            )
        return

    latest_version = _legacy._version_number(str(latest)) or str(latest).strip()
    if latest_version == installed:
        if target.state == STATE_OK:
            target.detail += f"; доступных обновлений через {inventory.active.provider} не найдено"
        return

    target.state = STATE_OUTDATED
    target.detail = (
        f"активный экземпляр: {inventory.active.path}; установлено {installed}; доступно {latest_version}; "
        f"владелец обновления: {inventory.active.provider}; обычный reconciliation внешний CLI не изменяет; "
        f"рекомендуемая команда обновления: `{inventory.update_advice}`"
    )


def _annotate_plugin_version_match(reporter, start_index: int) -> None:
    opencode_version = _standalone_opencode_version()
    if opencode_version is None:
        return
    for result in reporter.results[start_index:]:
        if result.component != "OpenCode plugin":
            continue
        result.detail = _fix_plugin_change_wording(result.detail)
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
    _annotate_external_opencode_freshness(reporter, start_index)
    _annotate_plugin_version_match(reporter, start_index)
    return result


def _reconcile_opencode_cli(config, reporter, check, npm):
    global _last_npm_metadata_error
    _last_npm_metadata_error = None
    start_index = len(reporter.results)
    _sync_legacy_policy()
    result = _legacy._reconcile_opencode_cli(config, reporter, check, npm)
    _annotate_npm_metadata_failure(reporter, start_index)
    _annotate_external_opencode_freshness(reporter, start_index)
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
