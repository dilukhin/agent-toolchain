"""Runtime/package checks for opencode_setup."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from setup_inventory import (
    active_instance,
    duplicate_recommendation,
    executable_inventory,
    isolated_manager_detail,
    render_instances,
    report_common_tool_inventory,
)
from setup_lib import Reporter, STATE_CONFLICT, STATE_MISSING, STATE_OK, STATE_OUTDATED, run


def ensure_ssh_relay_runtime(repo: Path, python_exe: str, reporter: Reporter,
                             check: bool, skip_install: bool) -> None:
    if sys.version_info < (3, 12):
        reporter.add("ssh_relay runtime", STATE_CONFLICT, "для ssh_relay требуется Python 3.12+")
        return
    if skip_install:
        reporter.add("ssh_relay runtime", STATE_OK, "установка/проверка зависимостей пропущена")
        return
    import_test = run([python_exe, "-c", "import paramiko"])
    if import_test.returncode != 0:
        reporter.add("ssh_relay runtime", STATE_MISSING, "отсутствует Python-зависимость paramiko")
        if check:
            return
        install = run([python_exe, "-m", "pip", "install", "paramiko"])
        if install.returncode != 0:
            reporter.add("ssh_relay runtime install", STATE_CONFLICT, install.stderr.strip()[-400:])
            return
    version = run([python_exe, str(repo / "ssh_relay.py"), "--version"])
    help_cp = run([python_exe, str(repo / "ssh_relay.py"), "--help"])
    if version.returncode != 0 or help_cp.returncode != 0 or "job" not in help_cp.stdout:
        reporter.add("ssh_relay runtime validation", STATE_CONFLICT,
                     "--version/--help завершились ошибкой либо отсутствует команда job")
    else:
        reporter.add("ssh_relay runtime" if import_test.returncode == 0 else "ssh_relay runtime validation",
                     STATE_OK, version.stdout.strip() or "проверено")


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
        reporter.add("agent-safe runtime", STATE_OK, "установка/проверка зависимостей пропущена")
        return

    repo_resolved = repo.resolve()
    origin = _module_origin(python_exe, "agent_safe")
    managed_import = origin is not None and origin.is_relative_to(repo_resolved)
    if not managed_import:
        state = STATE_MISSING if origin is None else STATE_OUTDATED
        detail = "editable-пакет не установлен" if origin is None else f"импорт разрешается вне управляемого репозитория: {origin}"
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
                         "editable install завершён, но импорт идёт не из управляемого репозитория")
            return

    help_cp = run([python_exe, "-m", "agent_safe", "--help"])
    if help_cp.returncode != 0:
        reporter.add("agent-safe runtime validation", STATE_CONFLICT, "python -m agent_safe --help завершился ошибкой")
    else:
        reporter.add("agent-safe runtime" if managed_import else "agent-safe runtime validation",
                     STATE_OK, "управляемый editable import/help проверен")


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


def _choco_installed_version() -> str | None:
    choco = shutil.which("choco")
    if not choco:
        return None
    commands = [
        [choco, "list", "--limit-output", "--exact", "opencode"],
        [choco, "list", "--local-only", "--limit-output", "--exact", "opencode"],
    ]
    for cmd in commands:
        cp = run(cmd)
        if cp.returncode != 0:
            continue
        for line in cp.stdout.splitlines():
            if line.lower().startswith("opencode|"):
                return line.split("|", 1)[1].strip() or None
    return None


def _scoop_installed_version() -> str | None:
    scoop = shutil.which("scoop")
    if not scoop:
        return None
    cp = run([scoop, "list", "opencode"])
    if cp.returncode != 0:
        return None
    for line in cp.stdout.splitlines():
        match = re.search(r"\bopencode\s+([0-9][^\s]*)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _brew_installed_version() -> str | None:
    brew = shutil.which("brew")
    if not brew:
        return None
    for formula in ("anomalyco/tap/opencode", "opencode"):
        cp = run([brew, "list", "--versions", formula])
        if cp.returncode != 0 or not cp.stdout.strip():
            continue
        parts = cp.stdout.strip().split()
        if len(parts) >= 2:
            return parts[-1]
    return None


def _known_opencode_managers(npm: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if npm:
        npm_version = _npm_global_version(npm, "opencode-ai")
        if npm_version:
            result["npm"] = npm_version
    choco_version = _choco_installed_version()
    if choco_version:
        result["choco"] = choco_version
    scoop_version = _scoop_installed_version()
    if scoop_version:
        result["scoop"] = scoop_version
    brew_version = _brew_installed_version()
    if brew_version:
        result["brew"] = brew_version
    return result


def _update_command(manager: str) -> str | None:
    return {
        "curl": "opencode upgrade --method curl",
        "npm": "npm install -g opencode-ai@latest",
        "pnpm": "pnpm install -g opencode-ai@latest",
        "bun": "bun install -g opencode-ai@latest",
        "choco": "choco upgrade opencode -y",
        "scoop": "scoop update opencode",
        "brew": "brew upgrade anomalyco/tap/opencode",
    }.get(manager)


def _uninstall_command(manager: str) -> str | None:
    return {
        "npm": "npm uninstall -g opencode-ai",
        "pnpm": "pnpm remove -g opencode-ai",
        "bun": "bun remove -g opencode-ai",
        "choco": "choco uninstall opencode -y",
        "scoop": "scoop uninstall opencode",
        "brew": "brew uninstall anomalyco/tap/opencode",
    }.get(manager)


def _version_number(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\b(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\b", text)
    return match.group(1) if match else None


def _reconcile_opencode_cli(config: dict[str, Any], reporter: Reporter, check: bool, npm: str | None) -> None:
    cli_package = str(config["dependencies"].get("opencode-cli-package", "opencode-ai"))
    items = executable_inventory("opencode")
    active = active_instance(items)
    managers = _known_opencode_managers(npm)

    if len(items) > 1:
        details = render_instances(items)
        recommendation = duplicate_recommendation("OpenCode", items)
        uninstall_hints = []
        if active is not None:
            for item in items:
                if item.active or item.manager == "unknown":
                    continue
                command = _uninstall_command(item.manager)
                if command:
                    uninstall_hints.append(f"для {item.manager}: {command}")
        if uninstall_hints:
            recommendation += " Варианты удаления затенённых установок: " + "; ".join(uninstall_hints) + "."
        reporter.add(
            "OpenCode: дублирующиеся установки",
            STATE_CONFLICT,
            details + ". " + recommendation + " Автоматическая установка/обновление CLI остановлена.",
        )
        return

    if active is None:
        if managers:
            detail = "; ".join(f"{name}={version}" for name, version in sorted(managers.items()))
            reporter.add(
                "OpenCode CLI",
                STATE_CONFLICT,
                "OpenCode зарегистрирован менеджером, но команда отсутствует в PATH: " + detail
                + ". Исправьте PATH или изолируйте/удалите неиспользуемую установку; setup не создаёт ещё одну копию.",
            )
            return
        if npm is None:
            reporter.add(
                "OpenCode CLI",
                STATE_MISSING,
                "OpenCode не найден; npm недоступен для текущего fresh-install пути. "
                "Существующую standalone/curl-установку setup умеет принять без npm.",
            )
            return
        latest_cli = _npm_latest_version(npm, cli_package)
        if latest_cli is None:
            reporter.add("OpenCode CLI", STATE_CONFLICT, f"не удалось получить актуальную версию npm-пакета {cli_package}")
            return
        reporter.add("OpenCode CLI", STATE_MISSING, f"OpenCode не найден; новая установка будет через npm: {cli_package}@{latest_cli}")
        if check:
            return
        cp = run([npm, "install", "-g", f"{cli_package}@{latest_cli}"])
        if cp.returncode != 0:
            reporter.add("OpenCode CLI install", STATE_CONFLICT, cp.stderr.strip()[-400:])
            return
        after = executable_inventory("opencode")
        if not after:
            reporter.add("OpenCode CLI validation", STATE_CONFLICT,
                         "npm install завершён, но команда opencode не появилась в PATH")
        return

    manager = active.manager
    if manager == "unknown":
        normalized_path = str(active.path).replace("\\", "/").lower()
        if "/.opencode/bin/opencode" in normalized_path or "/.local/bin/opencode" in normalized_path:
            manager = "curl"
        elif len(managers) == 1:
            manager = next(iter(managers))

    extra_managers = {name: version for name, version in managers.items() if name != manager}
    if extra_managers:
        detail = "; ".join(isolated_manager_detail(name, version) for name, version in sorted(extra_managers.items()))
        reporter.add("ПРЕДУПРЕЖДЕНИЕ: изолированные установки OpenCode", STATE_OK, detail)

    update_command = _update_command(manager)
    command_detail = f"; команда обновления: {update_command}" if update_command else "; команда обновления не определена"

    actual_version = _version_number(active.version)
    manager_version = managers.get(manager)
    if manager_version and actual_version and manager_version != actual_version:
        reporter.add(
            "ПРЕДУПРЕЖДЕНИЕ: версия OpenCode расходится с менеджером",
            STATE_OK,
            f"активный executable сообщает {actual_version}, {manager} зарегистрировал {manager_version}. "
            "Возможны ручное/self-update изменение binary, stale shim или смешанная ownership; ничего не исправлено автоматически.",
        )

    if manager != "npm":
        reporter.add(
            "OpenCode CLI",
            STATE_OK,
            f"активный экземпляр: {active.path}; версия: {actual_version or active.version or 'не определена'}; "
            f"владелец: {manager}; npm-дубликат не создаётся{command_detail}",
        )
        return

    if npm is None:
        reporter.add(
            "OpenCode CLI",
            STATE_CONFLICT,
            "активный экземпляр классифицирован как npm, но команда npm недоступна; состояние не изменено",
        )
        return

    current_cli = _npm_global_version(npm, cli_package)
    latest_cli = _npm_latest_version(npm, cli_package)
    if latest_cli is None:
        reporter.add("OpenCode CLI", STATE_CONFLICT, f"не удалось получить актуальную версию npm-пакета {cli_package}")
    elif current_cli == latest_cli and actual_version in {None, latest_cli}:
        reporter.add("OpenCode CLI", STATE_OK,
                     f"активный npm-экземпляр {active.path}; {cli_package}@{current_cli}{command_detail}")
    else:
        state = STATE_MISSING if current_cli is None else STATE_OUTDATED
        reporter.add("OpenCode CLI", state,
                     f"активный npm-экземпляр {active.path}; цель {latest_cli}, npm зарегистрировал {current_cli or 'нет'}, "
                     f"executable сообщает {actual_version or 'не определено'}{command_detail}")
        if not check:
            cp = run([npm, "install", "-g", f"{cli_package}@{latest_cli}"])
            if cp.returncode != 0:
                reporter.add("OpenCode CLI install", STATE_CONFLICT, cp.stderr.strip()[-400:])
            elif _npm_global_version(npm, cli_package) != latest_cli:
                reporter.add("OpenCode CLI validation", STATE_CONFLICT,
                             "npm install завершён, но npm не показывает целевую версию")
            else:
                refreshed = active_instance(executable_inventory("opencode"))
                refreshed_version = _version_number(refreshed.version) if refreshed else None
                if refreshed is None or refreshed.manager != "npm" or refreshed_version != latest_cli:
                    reporter.add(
                        "OpenCode CLI validation",
                        STATE_CONFLICT,
                        "npm обновлён, но активный opencode в PATH не соответствует управляемому npm-экземпляру/версии",
                    )


def reconcile_npm(config_dir: Path, config: dict[str, Any], reporter: Reporter,
                  check: bool, skip: bool) -> None:
    report_common_tool_inventory(reporter)
    if skip:
        reporter.add("OpenCode npm packages", STATE_OK, "установка/проверка npm-пакетов пропущена")
        return
    npm = shutil.which("npm")

    _reconcile_opencode_cli(config, reporter, check, npm)

    if not npm:
        if active_instance(executable_inventory("opencode")) is not None:
            reporter.add(
                "OpenCode npm packages",
                STATE_OK,
                "npm недоступен и не требуется для уже установленного OpenCode; "
                "@opencode-ai/plugin при необходимости устанавливается самим OpenCode через Bun при загрузке config",
            )
        else:
            reporter.add(
                "OpenCode npm packages",
                STATE_CONFLICT,
                "npm недоступен для текущего fresh-install пути OpenCode",
            )
        return

    plugin_package = "@opencode-ai/plugin"
    configured = config["dependencies"].get(plugin_package, "latest")
    target = _resolve_npm_target(npm, plugin_package, configured)
    if target is None:
        reporter.add("OpenCode plugin", STATE_CONFLICT,
                     f"не удалось определить целевую версию {plugin_package} для политики {configured!r}")
        return

    package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
    current = installed_version(package_json)
    if current == target:
        suffix = " (npm latest)" if str(configured).lower() == "latest" else ""
        reporter.add("OpenCode plugin", STATE_OK, target + suffix)
        return
    reporter.add("OpenCode plugin", STATE_MISSING if current is None else STATE_OUTDATED,
                 f"цель {target}, установлено {current or 'нет'}")
    if not check:
        config_dir.mkdir(parents=True, exist_ok=True)
        cp = run([npm, "install", "--prefix", str(config_dir), "--save-exact", f"{plugin_package}@{target}"])
        if cp.returncode != 0:
            reporter.add("OpenCode plugin install", STATE_CONFLICT, cp.stderr.strip()[-400:])
        elif installed_version(package_json) != target:
            reporter.add("OpenCode plugin validation", STATE_CONFLICT,
                         "npm install завершён, но целевая версия plugin не активна")
