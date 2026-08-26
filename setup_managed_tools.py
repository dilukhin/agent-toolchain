"""Managed per-tool runtimes for agent-toolchain Python CLI tools."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from setup_lib import (
    Reporter,
    STATE_CONFIGURED,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_MISSING,
    STATE_OK,
    STATE_OUTDATED,
    STATE_SKIPPED,
    atomic_write,
    run,
)

_RUNTIME_MARKER = ".agent-toolchain-managed-tool.json"
_ENTRYPOINT_MARKER = "agent-toolchain:managed-entrypoint:v1"


@dataclass(frozen=True)
class PythonToolRuntime:
    name: str
    public_command: str
    health_argv: tuple[tuple[str, ...], ...]
    required_help_token: str | None = None


SSH_RELAY_RUNTIME = PythonToolRuntime(
    name="ssh_relay",
    public_command="ssh_relay",
    health_argv=(("--version",), ("doctor",), ("--help",)),
    required_help_token="job",
)
AGENT_SAFE_RUNTIME = PythonToolRuntime(
    name="agent-safe",
    public_command="safe",
    health_argv=(("--help",),),
)


def data_root() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return (Path(base) / "agent-toolchain").resolve()
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return (Path(base) / "agent-toolchain").expanduser().resolve()
    return (Path.home() / ".local" / "share" / "agent-toolchain").resolve()


def public_bin_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_BIN_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return (Path(base) / "agent-toolchain" / "bin").resolve()
        return (Path.home() / ".local" / "bin").resolve()
    return (Path.home() / ".local" / "bin").resolve()


def _source_commit(repo: Path) -> str | None:
    cp = run(["git", "rev-parse", "HEAD"], cwd=repo)
    value = cp.stdout.strip()
    if cp.returncode != 0 or len(value) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        return None
    return value.lower()


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_command(venv: Path, command: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{command}.exe"
    return venv / "bin" / command


def _release_dir(spec: PythonToolRuntime, commit: str) -> Path:
    return data_root() / "tools" / spec.name / "releases" / commit


def _marker_payload(spec: PythonToolRuntime, commit: str) -> dict[str, object]:
    return {
        "schema": 1,
        "owner": "agent-toolchain",
        "tool": spec.name,
        "source_commit": commit,
        "runtime": "python-venv",
    }


def _marker_path(release: Path) -> Path:
    return release / _RUNTIME_MARKER


def _owned_release(release: Path, spec: PythonToolRuntime, commit: str | None = None) -> bool:
    marker = _marker_path(release)
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("schema") != 1 or data.get("owner") != "agent-toolchain" or data.get("tool") != spec.name:
        return False
    return commit is None or data.get("source_commit") == commit


def _release_for_internal_command(command: Path) -> Path | None:
    try:
        return command.resolve(strict=False).parents[2]
    except (IndexError, OSError):
        return None


def _health(spec: PythonToolRuntime, command: Path) -> tuple[bool, str]:
    details: list[str] = []
    help_text = ""
    for argv in spec.health_argv:
        cp = run([str(command), *argv])
        if cp.returncode != 0:
            tail = (cp.stderr or cp.stdout).strip()[-300:]
            return False, f"{' '.join(argv)} failed: {tail or 'exit ' + str(cp.returncode)}"
        text = cp.stdout.strip()
        if argv == ("--help",):
            help_text = text
        if text:
            details.append(text.splitlines()[0])
    if spec.required_help_token and spec.required_help_token not in help_text:
        return False, f"--help does not contain required token {spec.required_help_token!r}"
    return True, "; ".join(details) or "runtime checks passed"


def _venv_prerequisite(python_exe: str) -> tuple[bool, str]:
    cp = run([python_exe, "-B", "-c", "import ensurepip, venv; import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
    version = cp.stdout.strip() or "unknown"
    return cp.returncode == 0, version


def _install_release(spec: PythonToolRuntime, repo: Path, commit: str, python_exe: str, reporter: Reporter) -> Path | None:
    release = _release_dir(spec, commit)
    releases = release.parent
    if release.exists():
        if not _owned_release(release, spec, commit):
            reporter.add(f"{spec.name} runtime", STATE_CONFLICT,
                         f"target runtime path exists but ownership is not proven: {release}")
            return None
        return release

    prerequisite_ok, version = _venv_prerequisite(python_exe)
    if not prerequisite_ok:
        reporter.add(
            f"{spec.name} runtime",
            STATE_FAILED,
            f"base Python {version} cannot create isolated venv. MANUAL ACTION REQUIRED: install venv/ensurepip support for this Python and rerun toolchainctl apply",
        )
        return None

    releases.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = Path(tempfile.mkdtemp(prefix=f".{commit}.tmp-", dir=str(releases)))
    try:
        venv = temporary / "venv"
        create = run([python_exe, "-B", "-m", "venv", str(venv)])
        if create.returncode != 0:
            reporter.add(f"{spec.name} runtime", STATE_FAILED,
                         "failed to create isolated venv: " + create.stderr.strip()[-400:])
            return None
        runtime_python = _venv_python(venv)
        pip = run([str(runtime_python), "-m", "pip", "install", "--disable-pip-version-check", str(repo)])
        if pip.returncode != 0:
            reporter.add(f"{spec.name} runtime", STATE_FAILED,
                         "package installation failed: " + pip.stderr.strip()[-400:])
            return None
        command = _venv_command(venv, spec.public_command)
        if not command.is_file():
            reporter.add(f"{spec.name} runtime", STATE_FAILED,
                         f"package install did not create expected command: {command}")
            return None
        ok, detail = _health(spec, command)
        if not ok:
            reporter.add(f"{spec.name} runtime", STATE_FAILED,
                         "new isolated runtime failed health validation: " + detail)
            return None
        marker = json.dumps(_marker_payload(spec, commit), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        atomic_write(_marker_path(temporary), marker.encode("utf-8"))
        if release.exists():
            reporter.add(f"{spec.name} runtime", STATE_FAILED,
                         f"runtime path appeared concurrently; refusing to replace it: {release}")
            return None
        os.replace(temporary, release)
        temporary = None
        reporter.add(f"{spec.name} runtime", STATE_CONFIGURED,
                     f"installed non-editable isolated runtime from source commit {commit[:12]}: {release}")
        return release
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _public_entrypoint(spec: PythonToolRuntime) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return public_bin_dir() / f"{spec.public_command}{suffix}"


def _render_windows_entrypoint(spec: PythonToolRuntime, target: Path) -> bytes:
    text = (
        f"@REM {_ENTRYPOINT_MARKER}:{spec.name}\r\n"
        "@echo off\r\n"
        f'@"{target}" %*\r\n'
    )
    return text.encode("utf-8-sig")


def _managed_old_linux_link(spec: PythonToolRuntime, public: Path) -> bool:
    if not public.is_symlink():
        return False
    try:
        old_target = public.resolve(strict=False)
    except OSError:
        return False
    release = _release_for_internal_command(old_target)
    return release is not None and _owned_release(release, spec)


def _managed_old_windows_wrapper(spec: PythonToolRuntime, public: Path) -> bool:
    try:
        text = public.read_text(encoding="utf-8-sig")
    except OSError:
        return False
    normalized = text.replace("\r\n", "\n")
    return normalized.startswith(f"@REM {_ENTRYPOINT_MARKER}:{spec.name}\n")


def _reconcile_entrypoint(spec: PythonToolRuntime, target: Path, reporter: Reporter, check: bool) -> bool:
    public = _public_entrypoint(spec)
    if os.name == "nt":
        desired = _render_windows_entrypoint(spec, target)
        if public.exists():
            try:
                current = public.read_bytes()
            except OSError as exc:
                reporter.add(f"{spec.name} entrypoint", STATE_CONFLICT, f"cannot read {public}: {exc}")
                return False
            if current == desired:
                reporter.add(f"{spec.name} entrypoint", STATE_OK, str(public))
                return True
            if not _managed_old_windows_wrapper(spec, public):
                reporter.add(f"{spec.name} entrypoint", STATE_CONFLICT,
                             f"existing public command is not owned by agent-toolchain and was preserved: {public}")
                return False
        if check:
            reporter.add(f"{spec.name} entrypoint", STATE_OUTDATED if public.exists() else STATE_MISSING,
                         f"toolchainctl apply will publish managed command {public} -> {target}")
            return False
        public.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(public, desired)
        reporter.add(f"{spec.name} entrypoint", STATE_CONFIGURED, f"published {public} -> {target}")
        return True

    if public.is_symlink():
        try:
            if public.resolve(strict=False) == target.resolve(strict=False):
                reporter.add(f"{spec.name} entrypoint", STATE_OK, f"{public} -> {target}")
                return True
        except OSError:
            pass
    if public.exists() or public.is_symlink():
        if not _managed_old_linux_link(spec, public):
            reporter.add(f"{spec.name} entrypoint", STATE_CONFLICT,
                         f"existing public command is not owned by agent-toolchain and was preserved: {public}")
            return False
    if check:
        reporter.add(f"{spec.name} entrypoint", STATE_OUTDATED if (public.exists() or public.is_symlink()) else STATE_MISSING,
                     f"toolchainctl apply will publish managed command {public} -> {target}")
        return False
    public.parent.mkdir(parents=True, exist_ok=True)
    temporary = public.with_name(public.name + f".tmp-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        reporter.add(f"{spec.name} entrypoint", STATE_FAILED,
                     f"temporary entrypoint path already exists: {temporary}")
        return False
    try:
        os.symlink(str(target), str(temporary))
        os.replace(temporary, public)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink(missing_ok=True)
    reporter.add(f"{spec.name} entrypoint", STATE_CONFIGURED, f"published {public} -> {target}")
    return True


def _report_resolution(spec: PythonToolRuntime, target: Path, reporter: Reporter) -> None:
    public = _public_entrypoint(spec)
    resolved = shutil.which(spec.public_command)
    if not resolved:
        reporter.add(
            f"{spec.name} command resolution",
            STATE_OUTDATED,
            f"MANUAL ACTION REQUIRED: add {public.parent} to PATH (or start a new login shell after bootstrap); expected command: {public}",
        )
        return
    candidate = Path(resolved).resolve(strict=False)
    acceptable = {public.resolve(strict=False), target.resolve(strict=False)}
    if candidate in acceptable:
        reporter.add(f"{spec.name} command resolution", STATE_OK, f"{spec.public_command} -> {resolved}")
    else:
        reporter.add(f"{spec.name} command resolution", STATE_CONFLICT,
                     f"PATH resolves {spec.public_command} to foreign/shadowing executable {resolved}; managed command is {public}")


def ensure_python_tool_runtime(spec: PythonToolRuntime, repo: Path, python_exe: str, reporter: Reporter,
                               check: bool, skip_install: bool) -> None:
    component = f"{spec.name} runtime"
    if skip_install:
        reporter.add(component, STATE_SKIPPED, "managed tool runtime reconciliation skipped")
        return
    commit = _source_commit(repo)
    if commit is None:
        reporter.add(component, STATE_CONFLICT, f"cannot determine authoritative source commit for {repo}")
        return
    release = _release_dir(spec, commit)
    runtime_exists = release.exists()
    if runtime_exists and not _owned_release(release, spec, commit):
        reporter.add(component, STATE_CONFLICT,
                     f"runtime path exists but ownership is not proven: {release}")
        return
    if not runtime_exists:
        if check:
            prerequisite_ok, version = _venv_prerequisite(python_exe)
            if not prerequisite_ok:
                reporter.add(component, STATE_CONFLICT,
                             f"base Python {version} lacks venv/ensurepip. MANUAL ACTION REQUIRED: install venv support before toolchainctl apply")
                return
            reporter.add(component, STATE_MISSING,
                         f"toolchainctl apply will install isolated non-editable runtime from source commit {commit[:12]}: {release}")
            return
        release = _install_release(spec, repo, commit, python_exe, reporter)
        if release is None:
            return
    else:
        reporter.add(component, STATE_OK, f"source commit {commit[:12]}: {release}")

    venv = release / "venv"
    command = _venv_command(venv, spec.public_command)
    if not command.is_file():
        reporter.add(f"{spec.name} health", STATE_CONFLICT,
                     f"owned runtime is incomplete; expected command is missing: {command}")
        return
    ok, detail = _health(spec, command)
    if not ok:
        reporter.add(f"{spec.name} health", STATE_CONFLICT,
                     "installed managed runtime failed health validation: " + detail)
        return
    reporter.add(f"{spec.name} health", STATE_OK, detail)
    if _reconcile_entrypoint(spec, command, reporter, check):
        _report_resolution(spec, command, reporter)


def ensure_ssh_relay_runtime(repo: Path, python_exe: str, reporter: Reporter,
                             check: bool, skip_install: bool) -> None:
    # Internal fallback keeps old isolated unit fixtures meaningful; production calls always pass a Git checkout.
    if not (repo / ".git").exists():
        from setup_runtime_legacy import ensure_ssh_relay_runtime as legacy
        legacy(repo, python_exe, reporter, check, skip_install)
        return
    ensure_python_tool_runtime(SSH_RELAY_RUNTIME, repo, python_exe, reporter, check, skip_install)


def ensure_agent_safe_runtime(repo: Path, python_exe: str, reporter: Reporter,
                              check: bool, skip_install: bool) -> None:
    if not (repo / ".git").exists():
        from setup_runtime_legacy import ensure_agent_safe_runtime as legacy
        legacy(repo, python_exe, reporter, check, skip_install)
        return
    ensure_python_tool_runtime(AGENT_SAFE_RUNTIME, repo, python_exe, reporter, check, skip_install)
