"""Read-only inventory of executable instances and duplicate-installation reporting."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from setup_lib import Reporter, STATE_OK, STATE_WARNING, run


@dataclass(frozen=True)
class ExecutableInstance:
    path: Path
    version: str | None
    manager: str
    active: bool


def _windows_names(command: str) -> list[str]:
    if Path(command).suffix:
        return [command]
    pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
    names = [command]
    names.extend(command + ext.lower() for ext in pathext if ext)
    names.extend(command + ext.upper() for ext in pathext if ext)
    return names


def command_paths(command: str) -> list[Path]:
    """Return all physical command candidates in PATH order without changing PATH."""
    names = _windows_names(command) if os.name == "nt" else [command]
    found: list[Path] = []
    seen: set[str] = set()
    for raw_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_dir:
            continue
        directory = Path(raw_dir.strip('"')).expanduser()
        for name in names:
            candidate = directory / name
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
            except OSError:
                continue
            key = os.path.normcase(str(resolved))
            if key in seen:
                continue
            if os.name != "nt" and not os.access(resolved, os.X_OK):
                continue
            seen.add(key)
            found.append(resolved)
    return found


def infer_manager(path: Path) -> str:
    value = str(path).replace("\\", "/").lower()
    if "chocolatey" in value or "/choco/" in value:
        return "choco"
    if "/scoop/" in value:
        return "scoop"
    if "/pnpm/" in value or "pnpm-global" in value:
        return "pnpm"
    if "/bun/" in value or "/.bun/" in value:
        return "bun"
    if "/npm/" in value or "node_modules/opencode-ai" in value:
        return "npm"
    if "/homebrew/" in value or value.startswith("/opt/homebrew/"):
        return "brew"
    if "/mise/" in value or "/.mise/" in value:
        return "mise"
    if "/.opencode/bin/" in value or "/.local/bin/opencode" in value:
        return "local"
    return "unknown"


def _version_for(path: Path, args: tuple[str, ...]) -> str | None:
    cp = run([str(path), *args])
    if cp.returncode != 0:
        return None
    text = (cp.stdout or cp.stderr).strip()
    if not text:
        return None
    return text.splitlines()[0].strip()


def executable_inventory(command: str, *, version_args: tuple[str, ...] = ("--version",)) -> list[ExecutableInstance]:
    paths = command_paths(command)
    active_raw = shutil.which(command)
    active: Path | None = None
    if active_raw:
        try:
            active = Path(active_raw).resolve()
        except OSError:
            active = Path(active_raw)
    result: list[ExecutableInstance] = []
    for path in paths:
        is_active = active is not None and os.path.normcase(str(path)) == os.path.normcase(str(active))
        result.append(
            ExecutableInstance(
                path=path,
                version=_version_for(path, version_args),
                manager=infer_manager(path),
                active=is_active,
            )
        )
    if result and not any(item.active for item in result):
        # PATH inspection can canonicalize a shim differently from shutil.which().
        first = result[0]
        result[0] = ExecutableInstance(first.path, first.version, first.manager, True)
    return result


def _short_instance(item: ExecutableInstance) -> str:
    role = "активный" if item.active else "затенённый"
    version = item.version or "версия не определена"
    manager = item.manager if item.manager != "unknown" else "менеджер не определён"
    return f"{role}: {item.path} ({version}; {manager})"


def duplicate_recommendation(display_name: str, items: list[ExecutableInstance]) -> str:
    active = next((item for item in items if item.active), items[0] if items else None)
    if active is None:
        return ""
    manager = active.manager if active.manager != "unknown" else "текущего владельца"
    return (
        f"Рекомендация: оставить активный экземпляр {display_name} под управлением {manager}; "
        "остальные глобальные экземпляры удалить через их собственный менеджер либо изолировать от общего PATH."
    )


def report_common_tool_inventory(reporter: Reporter) -> None:
    """Report duplicate executable instances for core toolchain without blocking setup."""
    specs = [
        ("Git", "git", ("--version",)),
        ("Python", "python", ("--version",)),
        ("Node.js", "node", ("--version",)),
        ("npm", "npm", ("--version",)),
        ("uv", "uv", ("--version",)),
    ]
    for display, command, version_args in specs:
        items = executable_inventory(command, version_args=version_args)
        if len(items) <= 1:
            continue
        detail = "; ".join(_short_instance(item) for item in items)
        detail += ". " + duplicate_recommendation(display, items)
        reporter.add(f"Инвентаризация {display}", STATE_WARNING, detail)


def render_instances(items: list[ExecutableInstance]) -> str:
    return "; ".join(_short_instance(item) for item in items)


def active_instance(items: list[ExecutableInstance]) -> ExecutableInstance | None:
    return next((item for item in items if item.active), items[0] if items else None)


def isolated_manager_detail(manager: str, version: str) -> str:
    return f"дополнительная установка {manager} {version} не является активной в PATH; если изоляция намеренная, её можно оставить"
