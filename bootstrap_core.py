#!/usr/bin/env python3
"""Cross-platform stdlib-only bootstrap publisher for the agent-toolchain core."""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import shutil
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

SOURCE_ROOT = Path(__file__).resolve().parent
CORE_MARKER = ".agent-toolchain-managed-core.json"
ENTRYPOINT_MARKER = "agent-toolchain:managed-core-entrypoint:v1"

# Immutable contract used by schema-1 markers that predate the self-describing payload list.
# Keep this list stable so a machine can skip releases and still prove ownership of its old core.
LEGACY_REQUIRED_FILES_V1 = (
    "toolchainctl.py",
    "setup_core.py",
    "setup_core_adapter.py",
    "setup_lib.py",
    "setup_manifest.py",
    "setup_migration.py",
    "setup_runtime.py",
    "setup_runtime_legacy.py",
    "setup_managed_tools.py",
    "setup_tool_skills.py",
    "setup_tool_skills_impl.py",
    "setup_path.py",
    "setup_inventory.py",
    "setup_tools.py",
    "config_data.json",
)
LEGACY_REQUIRED_TREES_V1 = ("templates", "skills/remote-long-running")

# Current publish contract. Future versions may extend these without changing legacy validation.
REQUIRED_FILES = LEGACY_REQUIRED_FILES_V1
REQUIRED_TREES = LEGACY_REQUIRED_TREES_V1


def data_root() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain").resolve()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return (Path(xdg).expanduser() / "agent-toolchain").resolve()
    return (Path.home() / ".local" / "share" / "agent-toolchain").resolve()


def bin_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_BIN_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "bin").resolve()
    return (Path.home() / ".local" / "bin").resolve()


def _iter_contract_files(root: Path, required_files: tuple[str, ...], required_trees: tuple[str, ...]):
    for relative in required_files:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"Missing bootstrap source file: {path}")
        yield relative.replace("\\", "/"), path
    for tree in required_trees:
        base = root / tree
        if not base.is_dir():
            raise RuntimeError(f"Missing bootstrap source directory: {base}")
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path.relative_to(root).as_posix(), path


def _iter_source_files(root: Path):
    yield from _iter_contract_files(root, REQUIRED_FILES, REQUIRED_TREES)


def _fingerprint_files(files) -> str:
    digest = hashlib.sha256()
    for relative, path in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_fingerprint(root: Path) -> str:
    return _fingerprint_files(_iter_source_files(root))


def _legacy_v1_fingerprint(root: Path) -> str:
    return _fingerprint_files(_iter_contract_files(root, LEGACY_REQUIRED_FILES_V1, LEGACY_REQUIRED_TREES_V1))


def source_payload(root: Path) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for relative, path in _iter_source_files(root):
        payload.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return payload


def _payload_fingerprint(root: Path, payload: object) -> str | None:
    if not isinstance(payload, list) or not payload:
        return None
    digest = hashlib.sha256()
    seen: set[str] = set()
    try:
        for item in payload:
            if not isinstance(item, dict):
                return None
            relative = item.get("path")
            expected = item.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected, str):
                return None
            if "\\" in relative or len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
                return None
            posix = PurePosixPath(relative)
            if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
                return None
            normalized = posix.as_posix()
            if normalized in seen:
                return None
            seen.add(normalized)
            path = root.joinpath(*posix.parts)
            if path.is_symlink() or not path.is_file():
                return None
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                return None
            digest.update(normalized.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")

        actual: set[str] = set()
        for path in root.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink():
                return None
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative != CORE_MARKER:
                    actual.add(relative)
        if actual != seen:
            return None
    except OSError:
        return None
    return digest.hexdigest()


def _owned_core(core: Path) -> dict[str, object] | None:
    if core.is_symlink():
        return None
    try:
        data = json.loads((core / CORE_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != 1 or data.get("owner") != "agent-toolchain":
        return None
    fingerprint = data.get("fingerprint")
    if not isinstance(fingerprint, str):
        return None
    payload = data.get("payload")
    if payload is not None:
        actual = _payload_fingerprint(core, payload)
    else:
        try:
            actual = _legacy_v1_fingerprint(core)
        except (OSError, RuntimeError):
            return None
    if actual != fingerprint:
        return None
    return data


def _entrypoint_path() -> Path:
    return bin_dir() / ("toolchainctl.cmd" if os.name == "nt" else "toolchainctl")


def _entrypoint_bytes(core: Path, *, windows: bool | None = None) -> bytes:
    tool = core / "toolchainctl.py"
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        text = (
            f"@REM {ENTRYPOINT_MARKER}\r\n"
            "@echo off\r\n"
            "@setlocal\r\n"
            '@set "PYTHONUTF8=1"\r\n'
            '@set "PYTHONIOENCODING=utf-8"\r\n'
            f'@"{sys.executable}" -B "{tool}" %*\r\n'
            '@set "_AGENT_TOOLCHAIN_RC=%ERRORLEVEL%"\r\n'
            "@endlocal & exit /b %_AGENT_TOOLCHAIN_RC%\r\n"
        )
        return text.encode("utf-8-sig")
    text = (
        "#!/usr/bin/env bash\n"
        f"# {ENTRYPOINT_MARKER}\n"
        f'exec "{sys.executable}" -B "{tool}" "$@"\n'
    )
    return text.encode("utf-8")


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _entrypoint_owned(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    if os.name == "nt":
        return text.splitlines()[:1] == [f"@REM {ENTRYPOINT_MARKER}"]
    lines = text.splitlines()
    return len(lines) >= 2 and lines[1] == f"# {ENTRYPOINT_MARKER}"


def _preflight_entrypoint(core: Path) -> None:
    path = _entrypoint_path()
    if not _path_present(path):
        return
    desired = _entrypoint_bytes(core)
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current == desired:
        return
    if not _entrypoint_owned(path):
        raise RuntimeError(f"Refusing to replace foreign toolchainctl entrypoint: {path}")


def _copy_payload(source: Path, staging: Path, fingerprint: str) -> None:
    for relative in REQUIRED_FILES:
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    for tree in REQUIRED_TREES:
        shutil.copytree(source / tree, staging / tree, dirs_exist_ok=False)

    staged_fingerprint = source_fingerprint(staging)
    if staged_fingerprint != fingerprint:
        raise RuntimeError("Bootstrap source changed while its managed core payload was being staged")

    marker = {
        "schema": 1,
        "owner": "agent-toolchain",
        "fingerprint": staged_fingerprint,
        "payload": source_payload(staging),
    }
    update_ref = os.environ.get("AGENT_TOOLCHAIN_UPDATE_REF")
    if update_ref and len(update_ref) == 40 and all(ch in "0123456789abcdef" for ch in update_ref):
        marker["source_ref"] = update_ref
    (staging / CORE_MARKER).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_staged(staging: Path) -> None:
    for path in sorted(staging.glob("*.py")):
        py_compile.compile(str(path), doraise=True)


def _publish_core(source: Path, core: Path, fingerprint: str) -> tuple[bool, Path | None]:
    core_present = core.exists() or core.is_symlink()
    current = _owned_core(core) if core_present else None
    if core_present and current is None:
        raise RuntimeError(f"Refusing to replace modified or unowned core directory: {core}")
    if current is not None and current.get("fingerprint") == fingerprint:
        return False, None

    root = core.parent
    root.mkdir(parents=True, exist_ok=True)
    staging: Path | None = Path(tempfile.mkdtemp(prefix=".core.tmp-", dir=str(root)))
    backup: Path | None = None
    try:
        _copy_payload(source, staging, fingerprint)
        _validate_staged(staging)
        if core_present:
            backup = root / f"core.previous.{time.strftime('%Y%m%d%H%M%S')}.{os.getpid()}"
            if backup.exists() or backup.is_symlink():
                raise RuntimeError(f"Bootstrap backup path already exists: {backup}")
            os.replace(core, backup)
        try:
            os.replace(staging, core)
            staging = None
        except Exception:
            if backup is not None and backup.exists() and not core.exists():
                os.replace(backup, core)
                backup = None
            raise
        return True, backup
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _publish_entrypoint(core: Path) -> bool:
    path = _entrypoint_path()
    desired = _entrypoint_bytes(core)
    if _path_present(path):
        try:
            current = path.read_bytes()
        except OSError:
            current = None
        if current == desired:
            return False
        if not _entrypoint_owned(path):
            raise RuntimeError(f"Refusing to replace foreign toolchainctl entrypoint: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    if _path_present(temporary):
        raise RuntimeError(f"Temporary toolchainctl entrypoint already exists: {temporary}")
    try:
        temporary.write_bytes(desired)
        if os.name != "nt":
            temporary.chmod(0o755)
        os.replace(temporary, path)
    finally:
        if _path_present(temporary):
            temporary.unlink(missing_ok=True)
    return True


def _report_resolution() -> None:
    entry = _entrypoint_path()
    resolved = shutil.which("toolchainctl")
    if resolved and Path(resolved).resolve(strict=False) == entry.resolve(strict=False):
        print(f"up-to-date        toolchainctl command resolution  {resolved}")
    else:
        print(
            f"outdated          toolchainctl command resolution  MANUAL ACTION REQUIRED: run {entry} apply once; "
            f"that command will reconcile supported PATH activation. Until then use the absolute command {entry}"
        )


def main() -> int:
    if sys.version_info < (3, 10):
        print("agent-toolchain requires Python 3.10+ for its stdlib-only core.", file=sys.stderr)
        return 2
    core = data_root() / "core"
    try:
        fingerprint = source_fingerprint(SOURCE_ROOT)
        _preflight_entrypoint(core)
        core_changed, backup = _publish_core(SOURCE_ROOT, core, fingerprint)
        entry_changed = _publish_entrypoint(core)
    except (OSError, RuntimeError, py_compile.PyCompileError) as exc:
        print(f"modified/conflict  agent-toolchain bootstrap  {exc}", file=sys.stderr)
        return 2

    print(("configured" if core_changed else "up-to-date").ljust(18) + f"agent-toolchain core  {core}")
    print(("configured" if entry_changed else "up-to-date").ljust(18) + f"toolchainctl entrypoint  {_entrypoint_path()}")
    if backup is not None:
        print(f"info              previous managed core retained  {backup}")
    _report_resolution()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
