"""Runtime/package checks for opencode_setup."""
from __future__ import annotations

import json
import os
import re
import shlex
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
from setup_lib import (
    Reporter,
    STATE_CONFIGURED,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_INFO,
    STATE_MISSING,
    STATE_OK,
    STATE_OUTDATED,
    STATE_SKIPPED,
    atomic_write,
    run,
)

_MANAGED_RUNTIME_MARKER = "opencode_setup-bootstrap-python-v1"
_SSH_RELAY_LAUNCHER_MARKER = "opencode_setup:ssh_relay-entrypoint-v1"
_SSH_RELAY_PUBLIC_MARKER = "opencode_setup:ssh_relay-public-entrypoint-v1"


def _managed_python_runtime_root(python_exe: str) -> Path | None:
    """Return the opencode_setup-owned venv root for python_exe, if ownership is proven."""
    try:
        executable = Path(python_exe).expanduser().resolve()
        root = executable.parent.parent
        marker = root / ".opencode-setup-managed-runtime"
        if not marker.is_file():
            return None
        if marker.read_text(encoding="utf-8").strip() != _MANAGED_RUNTIME_MARKER:
            return None
        expected = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if executable != expected.resolve():
            return None
        return root
    except OSError:
        return None


def _ssh_relay_internal_entrypoint(runtime_root: Path) -> Path:
    if os.name == "nt":
        return runtime_root / "Scripts" / "ssh_relay.cmd"
    return runtime_root / "bin" / "ssh_relay"


def _ssh_relay_public_entrypoint() -> Path:
    override = os.environ.get("OPENCODE_SETUP_BIN_DIR")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "opencode_setup" / "bin" if local else Path.home() / ".local" / "bin"
    else:
        base = Path.home() / ".local" / "bin"
    return base / ("ssh_relay.cmd" if os.name == "nt" else "ssh_relay")


def _escape_cmd_path(path: Path) -> str:
    return str(path).replace("%", "%%")


def _render_ssh_relay_internal_entrypoint(repo: Path) -> bytes:
    source = (repo / "ssh_relay.py").resolve()
    if os.name == "nt":
        text = (
            "@echo off\r\n"
            f"rem {_SSH_RELAY_LAUNCHER_MARKER}\r\n"
            f'"%~dp0python.exe" -B "{_escape_cmd_path(source)}" %*\r\n'
        )
    else:
        text = (
            "#!/bin/sh\n"
            f"# {_SSH_RELAY_LAUNCHER_MARKER}\n"
            'SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            f'exec "$SCRIPT_DIR/python" -B {shlex.quote(str(source))} "$@"\n'
        )
    return text.encode("utf-8")


def _render_ssh_relay_public_entrypoint(internal: Path) -> bytes:
    text = (
        "@echo off\r\n"
        f"rem {_SSH_RELAY_PUBLIC_MARKER}\r\n"
        f'"{_escape_cmd_path(internal.resolve())}" %*\r\n'
    )
    return text.encode("utf-8")


def _inspect_generated_launcher(path: Path, desired: bytes, *, executable: bool) -> tuple[str, str]:
    if not path.exists():
        return STATE_MISSING, str(path)
    if path.is_symlink() or not path.is_file():
        return STATE_CONFLICT, f"entrypoint path exists but is not the expected regular file: {path}"
    try:
        current = path.read_bytes()
    except OSError as exc:
        return STATE_CONFLICT, f"cannot read managed entrypoint {path}: {exc}"
    if current != desired:
        return STATE_CONFLICT, f"existing entrypoint differs from opencode_setup generated content; preserved: {path}"
    if executable and not os.access(path, os.X_OK):
        return STATE_OUTDATED, f"managed launcher is not executable: {path}"
    return STATE_OK, str(path)


def _reconcile_internal_ssh_relay_launcher(
    repo: Path,
    runtime_root: Path,
    reporter: Reporter,
    check: bool,
) -> tuple[Path | None, bool]:
    path = _ssh_relay_internal_entrypoint(runtime_root)
    desired = _render_ssh_relay_internal_entrypoint(repo)
    executable = os.name != "nt"
    state, detail = _inspect_generated_launcher(path, desired, executable=executable)
    if state == STATE_OK:
        reporter.add("ssh_relay runtime launcher", STATE_OK, detail)
        return path, False
    if state == STATE_CONFLICT:
        reporter.add("ssh_relay runtime launcher", STATE_CONFLICT, detail)
        return None, False
    if check:
        action = "создаст" if state == STATE_MISSING else "восстановит executable mode для"
        reporter.add(
            "ssh_relay runtime launcher",
            state,
            f"{detail}; обычный apply {action} launcher, использующий managed Python runtime",
        )
        return None, False
    try:
        if state == STATE_MISSING:
            atomic_write(path, desired)
        if executable:
            path.chmod(0o755)
    except OSError as exc:
        reporter.add(
            "ssh_relay runtime launcher",
            STATE_FAILED,
            f"не удалось настроить managed launcher {path}: {exc}",
        )
        return None, False
    reporter.add(
        "ssh_relay runtime launcher",
        STATE_CONFIGURED,
        f"launcher настроен: {path}; он всегда использует Python из managed runtime",
    )
    return path, True


def _inspect_linux_public_entrypoint(public: Path, internal: Path) -> tuple[str, str]:
    if public.is_symlink():
        try:
            target = (public.parent / os.readlink(public)).resolve()
        except OSError as exc:
            return STATE_CONFLICT, f"cannot inspect ssh_relay symlink {public}: {exc}"
        if target == internal.resolve():
            return STATE_OK, f"{public} -> {internal}"
        return STATE_CONFLICT, f"existing symlink points elsewhere and is preserved: {public} -> {target}"
    if public.exists():
        return STATE_CONFLICT, f"existing non-managed entrypoint is preserved: {public}"
    return STATE_MISSING, str(public)


def _reconcile_public_ssh_relay_entrypoint(
    internal: Path,
    reporter: Reporter,
    check: bool,
) -> tuple[Path | None, bool]:
    public = _ssh_relay_public_entrypoint()
    if os.name != "nt":
        state, detail = _inspect_linux_public_entrypoint(public, internal)
        if state == STATE_OK:
            reporter.add("ssh_relay entrypoint", STATE_OK, detail)
            return public, False
        if state == STATE_CONFLICT:
            reporter.add("ssh_relay entrypoint", STATE_CONFLICT, detail)
            return None, False
        if check:
            reporter.add(
                "ssh_relay entrypoint",
                STATE_MISSING,
                f"{public}; обычный apply создаст безопасную ссылку на managed runtime launcher",
            )
            return None, False
        try:
            public.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(str(internal.resolve()), public)
        except OSError as exc:
            reporter.add("ssh_relay entrypoint", STATE_FAILED, f"не удалось создать {public}: {exc}")
            return None, False
        reporter.add(
            "ssh_relay entrypoint",
            STATE_CONFIGURED,
            f"создан: {public} -> {internal}; используйте `ssh_relay ...` вместо прямого `./ssh_relay.py`",
        )
        return public, True

    desired = _render_ssh_relay_public_entrypoint(internal)
    state, detail = _inspect_generated_launcher(public, desired, executable=False)
    if state == STATE_OK:
        reporter.add("ssh_relay entrypoint", STATE_OK, detail)
        return public, False
    if state == STATE_CONFLICT:
        reporter.add("ssh_relay entrypoint", STATE_CONFLICT, detail)
        return None, False
    if check:
        reporter.add(
            "ssh_relay entrypoint",
            state,
            f"{detail}; обычный apply создаст managed Windows entrypoint",
        )
        return None, False
    try:
        atomic_write(public, desired)
    except OSError as exc:
        reporter.add("ssh_relay entrypoint", STATE_FAILED, f"не удалось создать {public}: {exc}")
        return None, False
    reporter.add(
        "ssh_relay entrypoint",
        STATE_CONFIGURED,
        f"создан managed Windows entrypoint: {public}",
    )
    return public, True


def _run_ssh_relay_entrypoint(entrypoint: Path, arg: str):
    if os.name == "nt":
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return run([comspec, "/d", "/c", str(entrypoint), arg])
    return run([str(entrypoint), arg])


def _report_ssh_relay_resolution(entrypoint: Path, internal: Path, reporter: Reporter) -> None:
    resolved = shutil.which("ssh_relay")
    if not resolved:
        reporter.add(
            "ssh_relay command resolution",
            STATE_OUTDATED,
            f"managed entrypoint готов: {entrypoint}, но его каталог не активен в PATH. "
            f"MANUAL ACTION REQUIRED: добавьте {entrypoint.parent} в PATH или используйте абсолютный путь {entrypoint}",
        )
        return
    try:
        resolved_path = Path(resolved).resolve()
    except OSError:
        resolved_path = Path(resolved)
    expected = internal.resolve() if os.name != "nt" else entrypoint.resolve()
    if os.path.normcase(str(resolved_path)) == os.path.normcase(str(expected)):
        reporter.add("ssh_relay command resolution", STATE_OK, f"ssh_relay -> {resolved}")
        return
    reporter.add(
        "ssh_relay command resolution",
        STATE_CONFLICT,
        f"команда ssh_relay в PATH разрешается в другой экземпляр: {resolved}; managed entrypoint сохранён: {entrypoint}",
    )


def _report_ssh_relay_source_launcher(repo: Path, managed_python: str, public: Path, reporter: Reporter) -> None:
    if os.name == "nt":
        return
    script = repo / "ssh_relay.py"
    try:
        first_line = script.open("r", encoding="utf-8").readline().strip()
    except OSError:
        return
    if first_line != "#!/usr/bin/env python3":
        return
    source_python = shutil.which("python3")
    if not source_python:
        return
    try:
        if Path(source_python).resolve() == Path(managed_python).resolve():
            return
    except OSError:
        pass
    probe = run([source_python, "-c", "import paramiko"])
    if probe.returncode == 0:
        return
    reporter.add(
        "ssh_relay source launcher",
        STATE_INFO,
        f"{script} запускается через неуправляемый Python {source_python}, где paramiko отсутствует. "
        f"Прямой `./ssh_relay.py daemon ...` поэтому не является проверенным runtime; используйте managed entrypoint: {public}",
    )


def ensure_ssh_relay_runtime(repo: Path, python_exe: str, reporter: Reporter,
                             check: bool, skip_install: bool) -> None:
    if sys.version_info < (3, 12):
        reporter.add("ssh_relay runtime", STATE_CONFLICT, "для ssh_relay требуется Python 3.12+")
        return
    if skip_install:
        reporter.add("ssh_relay runtime", STATE_SKIPPED, "установка/проверка зависимостей пропущена")
        return

    import_test = run([python_exe, "-c", "import paramiko"])
    installed_now = False
    runtime_ready = import_test.returncode == 0
    if not runtime_ready:
        if check:
            reporter.add(
                "ssh_relay runtime",
                STATE_MISSING,
                "отсутствует Python-зависимость paramiko; обычный apply установит её автоматически "
                "в управляемый Python runtime",
            )
        else:
            install = run([python_exe, "-m", "pip", "install", "paramiko"])
            if install.returncode != 0:
                reporter.add(
                    "ssh_relay runtime",
                    STATE_FAILED,
                    "не удалось установить paramiko в управляемый Python runtime: " + install.stderr.strip()[-400:],
                )
                return
            installed_now = True
            runtime_ready = True

    runtime_root = _managed_python_runtime_root(python_exe)
    if runtime_root is None:
        if not runtime_ready:
            return
        version = run([python_exe, str(repo / "ssh_relay.py"), "--version"])
        help_cp = run([python_exe, str(repo / "ssh_relay.py"), "--help"])
        if version.returncode != 0 or help_cp.returncode != 0 or "job" not in help_cp.stdout:
            state = STATE_FAILED if installed_now else STATE_CONFLICT
            reporter.add(
                "ssh_relay runtime",
                state,
                "--version/--help завершились ошибкой либо отсутствует команда job",
            )
        elif installed_now:
            reporter.add(
                "ssh_relay runtime",
                STATE_CONFIGURED,
                "paramiko установлен; source CLI проверен: " + (version.stdout.strip() or "ssh_relay"),
            )
        else:
            reporter.add("ssh_relay runtime", STATE_OK, version.stdout.strip() or "проверено")
        return

    if runtime_ready:
        if installed_now:
            reporter.add(
                "ssh_relay runtime",
                STATE_CONFIGURED,
                f"paramiko установлен в managed Python runtime: {python_exe}",
            )
        else:
            reporter.add(
                "ssh_relay runtime",
                STATE_OK,
                f"paramiko доступен в managed Python runtime: {python_exe}",
            )

    internal, internal_changed = _reconcile_internal_ssh_relay_launcher(repo, runtime_root, reporter, check)
    if internal is None:
        target = _ssh_relay_public_entrypoint()
        _report_ssh_relay_source_launcher(repo, python_exe, target, reporter)
        return
    public, public_changed = _reconcile_public_ssh_relay_entrypoint(internal, reporter, check)
    target = public or _ssh_relay_public_entrypoint()
    _report_ssh_relay_source_launcher(repo, python_exe, target, reporter)
    if public is None or not runtime_ready:
        return

    version = _run_ssh_relay_entrypoint(public, "--version")
    help_cp = _run_ssh_relay_entrypoint(public, "--help")
    if version.returncode != 0 or help_cp.returncode != 0 or "job" not in help_cp.stdout:
        state = STATE_FAILED if (internal_changed or public_changed) else STATE_CONFLICT
        reporter.add(
            "ssh_relay health",
            state,
            "managed entrypoint существует, но --version/--help не подтвердили работоспособность ssh_relay",
        )
        return
    reporter.add(
        "ssh_relay health",
        STATE_OK,
        f"managed entrypoint проверен: {public}; {version.stdout.strip() or 'ssh_relay'}",
    )
    _report_ssh_relay_resolution(public, internal, reporter)


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
        reporter.add("agent-safe runtime", STATE_SKIPPED, "установка/проверка зависимостей пропущена")
        return

    repo_resolved = repo.resolve()
    origin = _module_origin(python_exe, "agent_safe")
    managed_import = origin is not None and origin.is_relative_to(repo_resolved)
    installed_now = False
    if not managed_import:
        state = STATE_MISSING if origin is None else STATE_OUTDATED
        detail = ("editable-пакет не установлен" if origin is None
                  else f"импорт разрешается вне управляемого репозитория: {origin}")
        if check:
            reporter.add(
                "agent-safe runtime",
                state,
                detail + "; обычный apply установит управляемый editable-пакет автоматически",
            )
            return
        install = run([python_exe, "-m", "pip", "install", "-e", str(repo)])
        if install.returncode != 0:
            reporter.add(
                "agent-safe runtime",
                STATE_FAILED,
                "не удалось выполнить управляемую editable-установку: " + install.stderr.strip()[-400:],
            )
            return
        installed_now = True
        origin = _module_origin(python_exe, "agent_safe")
        if origin is None or not origin.is_relative_to(repo_resolved):
            reporter.add(
                "agent-safe runtime",
                STATE_FAILED,
                "editable install выполнен, но итоговый import идёт не из управляемого репозитория",
            )
            return

    help_cp = run([python_exe, "-m", "agent_safe", "--help"])
    if help_cp.returncode != 0:
        reporter.add(
            "agent-safe runtime",
            STATE_FAILED if installed_now else STATE_CONFLICT,
            "python -m agent_safe --help завершился ошибкой",
        )
    elif installed_now:
        reporter.add("agent-safe runtime", STATE_CONFIGURED,
                     "управляемый editable-пакет установлен; import/help проверены")
    else:
        reporter.add("agent-safe runtime", STATE_OK, "управляемый editable import/help проверен")


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
                "MANUAL ACTION REQUIRED: установите npm, доступный в PATH, затем повторите setup, "
                "либо предварительно установите OpenCode поддерживаемым standalone-способом.",
            )
            return
        latest_cli = _npm_latest_version(npm, cli_package)
        if latest_cli is None:
            reporter.add("OpenCode CLI", STATE_CONFLICT, f"не удалось получить актуальную версию npm-пакета {cli_package}")
            return
        if check:
            reporter.add(
                "OpenCode CLI",
                STATE_MISSING,
                f"OpenCode не найден; обычный apply установит автоматически через npm: {cli_package}@{latest_cli}",
            )
            return
        cp = run([npm, "install", "-g", f"{cli_package}@{latest_cli}"])
        if cp.returncode != 0:
            reporter.add("OpenCode CLI", STATE_FAILED,
                         "npm install не выполнил установку OpenCode: " + cp.stderr.strip()[-400:])
            return
        after = active_instance(executable_inventory("opencode"))
        after_version = _version_number(after.version) if after else None
        if after is None or after_version != latest_cli:
            reporter.add(
                "OpenCode CLI",
                STATE_FAILED,
                "npm install завершён, но итоговая проверка не нашла активный opencode целевой версии",
            )
            return
        reporter.add(
            "OpenCode CLI",
            STATE_CONFIGURED,
            f"OpenCode установлен через npm: {after.path}; {cli_package}@{latest_cli}",
        )
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
        reporter.add("ПРЕДУПРЕЖДЕНИЕ: изолированные установки OpenCode", STATE_INFO, detail)

    update_command = _update_command(manager)
    command_detail = f"; команда обновления: {update_command}" if update_command else "; команда обновления не определена"

    actual_version = _version_number(active.version)
    manager_version = managers.get(manager)
    if manager_version and actual_version and manager_version != actual_version:
        reporter.add(
            "ПРЕДУПРЕЖДЕНИЕ: версия OpenCode расходится с менеджером",
            STATE_INFO,
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
        detail = (f"активный npm-экземпляр {active.path}; цель {latest_cli}, npm зарегистрировал {current_cli or 'нет'}, "
                  f"executable сообщает {actual_version or 'не определено'}{command_detail}")
        if check:
            reporter.add("OpenCode CLI", state, detail + "; обычный apply обновит npm-установку автоматически")
            return
        cp = run([npm, "install", "-g", f"{cli_package}@{latest_cli}"])
        if cp.returncode != 0:
            reporter.add("OpenCode CLI", STATE_FAILED,
                         "npm install не выполнил обновление OpenCode: " + cp.stderr.strip()[-400:])
        elif _npm_global_version(npm, cli_package) != latest_cli:
            reporter.add("OpenCode CLI", STATE_FAILED,
                         "npm install завершён, но npm не показывает целевую версию")
        else:
            refreshed = active_instance(executable_inventory("opencode"))
            refreshed_version = _version_number(refreshed.version) if refreshed else None
            if refreshed is None or refreshed.manager != "npm" or refreshed_version != latest_cli:
                reporter.add(
                    "OpenCode CLI",
                    STATE_FAILED,
                    "npm обновлён, но итоговый активный opencode в PATH не соответствует управляемому npm-экземпляру/версии",
                )
            else:
                reporter.add(
                    "OpenCode CLI",
                    STATE_CONFIGURED,
                    f"OpenCode обновлён через npm: {refreshed.path}; {cli_package}@{latest_cli}{command_detail}",
                )


def reconcile_npm(config_dir: Path, config: dict[str, Any], reporter: Reporter,
                  check: bool, skip: bool) -> None:
    report_common_tool_inventory(reporter)
    if skip:
        reporter.add("OpenCode npm packages", STATE_SKIPPED, "установка/проверка npm-пакетов пропущена")
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
                "npm недоступен для текущего fresh-install пути OpenCode; MANUAL ACTION REQUIRED: "
                "установите npm либо предварительно установите OpenCode поддерживаемым standalone-способом",
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
    state = STATE_MISSING if current is None else STATE_OUTDATED
    if check:
        reporter.add(
            "OpenCode plugin",
            state,
            f"цель {target}, установлено {current or 'нет'}; обычный apply установит/обновит plugin автоматически",
        )
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    cp = run([npm, "install", "--prefix", str(config_dir), "--save-exact", f"{plugin_package}@{target}"])
    if cp.returncode != 0:
        reporter.add("OpenCode plugin", STATE_FAILED,
                     "npm install не выполнил установку/обновление plugin: " + cp.stderr.strip()[-400:])
    elif installed_version(package_json) != target:
        reporter.add("OpenCode plugin", STATE_FAILED,
                     "npm install завершён, но итоговая проверка не видит целевую версию plugin")
    else:
        action = "установлен" if current is None else f"обновлён с {current}"
        reporter.add("OpenCode plugin", STATE_CONFIGURED, f"{action} до {target} через npm")
