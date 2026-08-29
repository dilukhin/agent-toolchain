"""Pinned ToolSpec deployment for agent-toolchain managed Python CLI tools."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any

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
from setup_tools import ToolSpec

_RUNTIME_MARKER = ".agent-toolchain-managed-tool.json"
_ENTRYPOINT_MARKER = "agent-toolchain:managed-entrypoint:v1"


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


def platform_name() -> str:
    return "windows" if os.name == "nt" else "linux"


def _is_commit_sha(value: str | None) -> bool:
    return bool(
        value
        and len(value) == 40
        and all(ch in "0123456789abcdefABCDEF" for ch in value)
    )


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_command(venv: Path, command: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{command}.exe"
    return venv / "bin" / command


def _release_dir(spec: ToolSpec) -> Path:
    ref = spec.ref.lower() if spec.ref else "builtin"
    if spec.runtime == "python-builtin":
        ref = f"builtin-{_builtin_fingerprint(spec)}"
    return data_root() / "tools" / spec.name / "releases" / ref


def _marker_path(release: Path) -> Path:
    return release / _RUNTIME_MARKER


def _marker_payload(spec: ToolSpec) -> dict[str, object]:
    release = _release_dir(spec) if spec.runtime == "python-builtin" else None
    return {
        "schema": 1,
        "owner": "agent-toolchain",
        "tool": spec.name,
        "repo": spec.repo,
        "source_ref": spec.ref.lower() if spec.ref else None,
        "runtime": spec.runtime,
        "payload_sha256": release.name.removeprefix("builtin-") if release else None,
    }


def _canonical_text_payload(path: Path) -> bytes:
    # Text-mode reading normalizes CRLF/CR to LF. utf-8-sig also strips a historical BOM.
    # Builtin release identity therefore depends on source content, not checkout line endings.
    return path.read_text(encoding="utf-8-sig").encode("utf-8")


def _builtin_payload(spec: ToolSpec) -> dict[str, bytes]:
    source_dir = Path(__file__).resolve().parent
    source = source_dir / f"{spec.module}.py"
    payload = {f"{spec.module}.py": _canonical_text_payload(source)}
    if spec.name == "proxy-tools":
        for dependency in ("setup_inventory.py", "setup_external_updates.py", "setup_lib.py"):
            payload[dependency] = _canonical_text_payload(source_dir / dependency)
    for command in spec.entrypoints:
        script = (
            "#!/usr/bin/env python3\n"
            "import sys as _sys\n"
            "_sys.dont_write_bytecode = True\n"
            f"from {spec.module} import main\n"
            f"raise SystemExit(main([\"{command.removesuffix('-proxied')}\", *_sys.argv[1:]]))\n"
        )
        payload[f"{command}.py"] = script.encode("utf-8")
    return payload


def _builtin_fingerprint(spec: ToolSpec) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(_builtin_payload(spec).items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _builtin_release_payload_matches(release: Path, spec: ToolSpec) -> bool:
    desired = _builtin_payload(spec)
    expected_files = set(desired) | {_RUNTIME_MARKER}
    required_exec = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    try:
        actual_files: set[str] = set()
        for path in release.rglob("*"):
            if path.is_symlink():
                return False
            if path.is_file():
                actual_files.add(path.relative_to(release).as_posix())
        if actual_files != expected_files:
            return False
        for relative, content in desired.items():
            target = release / relative
            if target.read_bytes() != content:
                return False
            if os.name != "nt" and relative.endswith("-proxied.py"):
                if target.stat().st_mode & required_exec != required_exec:
                    return False
    except OSError:
        return False
    return True


def _owned_release(release: Path, spec: ToolSpec) -> bool:
    if release.is_symlink():
        return False
    try:
        data = json.loads(_marker_path(release).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    base_owned = (
        isinstance(data, dict)
        and data.get("schema") == 1
        and data.get("owner") == "agent-toolchain"
        and data.get("tool") == spec.name
        and data.get("repo") == spec.repo
        and data.get("source_ref") == (spec.ref.lower() if spec.ref else None)
        and data.get("runtime") == spec.runtime
    )
    if not base_owned:
        return False
    if spec.runtime != "python-builtin":
        return True
    desired_fingerprint = _builtin_fingerprint(spec)
    return (
        data == _marker_payload(spec)
        and data.get("payload_sha256") == desired_fingerprint
        and release.name == f"builtin-{desired_fingerprint}"
        and _builtin_release_payload_matches(release, spec)
    )


def _validate_supported_spec(spec: ToolSpec) -> str | None:
    if platform_name() not in spec.platforms:
        return None
    if spec.source == "builtin" and spec.runtime == "python-builtin" and spec.update_policy == "bundled-with-setup":
        if len(spec.entrypoints) < 1 or not spec.module:
            return "builtin Python tool requires module and entrypoints"
        return None
    if spec.source != "git" or spec.runtime != "python-venv" or spec.update_policy != "pinned-tested":
        return (
            "current managed-tool deployer supports only source=git, runtime=python-venv, "
            "update_policy=pinned-tested"
        )
    if not spec.repo or not _is_commit_sha(spec.ref):
        return "pinned Python tool requires repository and an immutable 40-hex commit ref"
    if len(spec.entrypoints) != 1:
        return "current Python tool deployer requires exactly one public entrypoint"
    for check in spec.health_contract:
        if not check.argv or check.argv[0] not in spec.entrypoints:
            return "each health command must start with a ToolSpec-declared entrypoint"
    return None


def _venv_prerequisite(python_exe: str) -> tuple[bool, str]:
    cp = run([
        python_exe,
        "-B",
        "-c",
        "import ensurepip, venv; import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
    ])
    return cp.returncode == 0, cp.stdout.strip() or "unknown"


def _health(spec: ToolSpec, release: Path) -> tuple[bool, str]:
    venv = release / "venv"
    details: list[str] = []
    for check in spec.health_contract:
        internal = _venv_command(venv, check.argv[0])
        if not internal.is_file():
            return False, f"expected installed entrypoint is missing: {internal}"
        cp = run([str(internal), *check.argv[1:]])
        if cp.returncode != 0:
            tail = (cp.stderr or cp.stdout).strip()[-300:]
            return False, f"{' '.join(check.argv)} failed: {tail or 'exit ' + str(cp.returncode)}"
        text = cp.stdout.strip()
        if text:
            details.append(text.splitlines()[0])
    return True, "; ".join(details) or "runtime checks passed"


def _pip_source(spec: ToolSpec) -> str:
    assert spec.repo is not None and spec.ref is not None
    repo = spec.repo
    if repo.endswith(".git"):
        return f"git+{repo}@{spec.ref}"
    return f"git+{repo}.git@{spec.ref}"


def _install_release(spec: ToolSpec, python_exe: str, reporter: Reporter) -> Path | None:
    release = _release_dir(spec)
    if release.exists() or release.is_symlink():
        if _owned_release(release, spec):
            return release
        reporter.add(
            f"{spec.name} runtime",
            STATE_CONFLICT,
            f"target runtime path exists but ownership is not proven: {release}",
        )
        return None

    prerequisite_ok, version = _venv_prerequisite(python_exe)
    if not prerequisite_ok:
        reporter.add(
            f"{spec.name} runtime",
            STATE_FAILED,
            f"base Python {version} cannot create an isolated venv. MANUAL ACTION REQUIRED: install venv/ensurepip support and rerun toolchainctl apply",
        )
        return None
    if not shutil.which("git"):
        reporter.add(
            f"{spec.name} runtime",
            STATE_FAILED,
            "Git is required to install the pinned repository ref. MANUAL ACTION REQUIRED: install Git and rerun toolchainctl apply",
        )
        return None

    releases = release.parent
    try:
        releases.mkdir(parents=True, exist_ok=True)
        release.mkdir()
    except FileExistsError:
        if _owned_release(release, spec):
            return release
        reporter.add(
            f"{spec.name} runtime",
            STATE_CONFLICT,
            f"runtime path appeared concurrently and ownership is not proven: {release}",
        )
        return None
    except OSError as exc:
        reporter.add(f"{spec.name} runtime", STATE_FAILED, f"cannot create runtime path {release}: {exc}")
        return None

    complete = False
    try:
        # A Python venv is not relocatable: POSIX console-script shebangs and some
        # Windows launchers embed the interpreter path. Build directly at the final
        # immutable release path, then remove this exact same-run directory on failure.
        venv = release / "venv"
        create = run([python_exe, "-B", "-m", "venv", str(venv)])
        if create.returncode != 0:
            reporter.add(
                f"{spec.name} runtime",
                STATE_FAILED,
                "failed to create isolated venv: " + create.stderr.strip()[-400:],
            )
            return None

        runtime_python = _venv_python(venv)
        install = run([
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            _pip_source(spec),
        ])
        if install.returncode != 0:
            reporter.add(
                f"{spec.name} runtime",
                STATE_FAILED,
                "pinned package installation failed: " + install.stderr.strip()[-400:],
            )
            return None

        ok, detail = _health(spec, release)
        if not ok:
            reporter.add(
                f"{spec.name} runtime",
                STATE_FAILED,
                "new isolated runtime failed health validation: " + detail,
            )
            return None

        marker = json.dumps(_marker_payload(spec), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            atomic_write(_marker_path(release), marker.encode("utf-8"))
        except OSError as exc:
            reporter.add(
                f"{spec.name} runtime",
                STATE_FAILED,
                f"runtime passed health validation but ownership marker could not be written: {exc}",
            )
            return None

        complete = True
        reporter.add(
            f"{spec.name} runtime",
            STATE_CONFIGURED,
            f"installed pinned non-editable runtime {spec.ref[:12]} from {spec.repo}: {release}",
        )
        return release
    finally:
        if not complete and release.exists() and not release.is_symlink():
            shutil.rmtree(release, ignore_errors=True)


def _public_entrypoint(spec: ToolSpec, command: str) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return public_bin_dir() / f"{command}{suffix}"


def _render_windows_entrypoint(spec: ToolSpec, target: Path, *, legacy_bom: bool = False) -> bytes:
    if spec.runtime == "python-builtin":
        text = (f"@REM {_ENTRYPOINT_MARKER}:{spec.name}\r\n@echo off\r\n"
                f'@"{sys.executable}" "{target}" %*\r\n')
    else:
        text = (
            f"@REM {_ENTRYPOINT_MARKER}:{spec.name}\r\n"
            "@echo off\r\n"
            f'@"{target}" %*\r\n'
        )
    return text.encode("utf-8-sig" if legacy_bom else "utf-8")


def _previous_entrypoint(previous: Any, command: str) -> tuple[Path, Path] | None:
    if not isinstance(previous, dict):
        return None
    entries = previous.get("entrypoints")
    if not isinstance(entries, dict):
        return None
    item = entries.get(command)
    if not isinstance(item, dict):
        return None
    public = item.get("public_path")
    target = item.get("target")
    if not isinstance(public, str) or not isinstance(target, str):
        return None
    # public path is compared semantically, but target spelling is part of the exact
    # Windows .cmd bytes originally recorded by _manifest_record and must be preserved.
    return Path(public).resolve(strict=False), Path(target)


def _current_entrypoint_is_owned(spec: ToolSpec, command: str, public: Path, previous: Any) -> bool:
    previous_paths = _previous_entrypoint(previous, command)
    if previous_paths is None or previous_paths[0] != public.resolve(strict=False):
        return False
    old_target = previous_paths[1]
    if os.name == "nt":
        try:
            current = public.read_bytes()
            return current in {
                _render_windows_entrypoint(spec, old_target),
                _render_windows_entrypoint(spec, old_target, legacy_bom=True),
            }
        except OSError:
            return False
    if not public.is_symlink():
        return False
    try:
        return public.resolve(strict=False) == old_target.resolve(strict=False)
    except OSError:
        return False


def _entrypoint_matches_desired(spec: ToolSpec, public: Path, target: Path) -> bool:
    if os.name == "nt":
        try:
            return public.read_bytes() == _render_windows_entrypoint(spec, target)
        except OSError:
            return False
    if not public.is_symlink():
        return False
    try:
        return public.resolve(strict=False) == target.resolve(strict=False)
    except OSError:
        return False


def _reconcile_entrypoint(
    spec: ToolSpec,
    command: str,
    target: Path,
    previous: Any,
    reporter: Reporter,
    check: bool,
) -> tuple[bool, bool]:
    public = _public_entrypoint(spec, command)
    exists = public.exists() or public.is_symlink()
    if exists and _entrypoint_matches_desired(spec, public, target):
        reporter.add(f"{spec.name} entrypoint", STATE_OK, f"{public} -> {target}")
        return True, False
    if exists and not _current_entrypoint_is_owned(spec, command, public, previous):
        reporter.add(
            f"{spec.name} entrypoint",
            STATE_CONFLICT,
            f"existing public command is not proven owned by agent-toolchain and was preserved: {public}",
        )
        return False, False
    if check:
        reporter.add(
            f"{spec.name} entrypoint",
            STATE_OUTDATED if exists else STATE_MISSING,
            f"toolchainctl apply will publish managed command {public} -> {target}",
        )
        return False, False

    public.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        atomic_write(public, _render_windows_entrypoint(spec, target))
    else:
        temporary = public.with_name(public.name + f".tmp-{os.getpid()}")
        if temporary.exists() or temporary.is_symlink():
            reporter.add(
                f"{spec.name} entrypoint",
                STATE_FAILED,
                f"temporary entrypoint path already exists: {temporary}",
            )
            return False, False
        try:
            os.symlink(str(target), str(temporary))
            os.replace(temporary, public)
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink(missing_ok=True)
    reporter.add(f"{spec.name} entrypoint", STATE_CONFIGURED, f"published {public} -> {target}")
    return True, True


def _report_resolution(spec: ToolSpec, command: str, target: Path, reporter: Reporter) -> None:
    public = _public_entrypoint(spec, command)
    resolved = shutil.which(command)
    if not resolved:
        reporter.add(
            f"{spec.name} command resolution",
            STATE_OUTDATED,
            f"MANUAL ACTION REQUIRED: add {public.parent} to PATH (or start a new login shell after PATH activation); expected command: {public}",
        )
        return
    candidate = Path(resolved).resolve(strict=False)
    acceptable = {public.resolve(strict=False), target.resolve(strict=False)}
    if candidate in acceptable:
        reporter.add(f"{spec.name} command resolution", STATE_OK, f"{command} -> {resolved}")
    else:
        reporter.add(
            f"{spec.name} command resolution",
            STATE_CONFLICT,
            f"PATH resolves {command} to foreign/shadowing executable {resolved}; managed command is {public}",
        )


def _manifest_record(spec: ToolSpec, release: Path) -> dict[str, object]:
    venv = release / "venv"
    entries: dict[str, dict[str, str]] = {}
    for command in spec.entrypoints:
        entries[command] = {
            "public_path": str(_public_entrypoint(spec, command)),
            "target": str(_venv_command(venv, command) if spec.runtime == "python-venv" else release / f"{command}.py"),
        }
    return {
        "owner": "agent-toolchain",
        "source": spec.source,
        "repo": spec.repo,
        "source_ref": spec.ref,
        "runtime": spec.runtime,
        "runtime_path": str(release),
        "entrypoints": entries,
        "health_contract": [list(check.argv) for check in spec.health_contract],
        "platforms": list(spec.platforms),
    }


def _install_builtin(spec: ToolSpec, reporter: Reporter) -> Path | None:
    release = _release_dir(spec)
    if release.exists() or release.is_symlink():
        if _owned_release(release, spec):
            return release
        reporter.add(f"{spec.name} runtime", STATE_CONFLICT, f"runtime path exists but ownership is not proven: {release}")
        return None
    try:
        release.mkdir(parents=True)
        for relative, content in _builtin_payload(spec).items():
            target = release / relative
            atomic_write(target, content)
            if target.suffix == ".py" and target.name.endswith("-proxied.py") and os.name != "nt":
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        for command in spec.entrypoints:
            target = release / f"{command}.py"
            if os.name != "nt":
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        atomic_write(_marker_path(release), (json.dumps(_marker_payload(spec), sort_keys=True) + "\n").encode("utf-8"))
        reporter.add(f"{spec.name} runtime", STATE_CONFIGURED, f"installed bundled runtime: {release}")
        return release
    except OSError as exc:
        reporter.add(f"{spec.name} runtime", STATE_FAILED, f"cannot install builtin runtime: {exc}")
        return None


def reconcile_builtin_tool(spec: ToolSpec, reporter: Reporter, *, check: bool, manifest: dict[str, Any]) -> bool:
    if platform_name() not in spec.platforms:
        reporter.add(f"{spec.name} runtime", STATE_SKIPPED, f"not enabled on {platform_name()}")
        return False
    error = _validate_supported_spec(spec)
    if error:
        reporter.add(f"{spec.name} runtime", STATE_CONFLICT, error)
        return False
    release = _release_dir(spec)
    release_present = release.exists() or release.is_symlink()
    if release_present and not _owned_release(release, spec):
        reporter.add(
            f"{spec.name} runtime",
            STATE_CONFLICT,
            f"builtin runtime payload or ownership integrity check failed: {release}",
        )
        return False
    if not release_present:
        if check:
            reporter.add(f"{spec.name} runtime", STATE_MISSING, f"toolchainctl apply will install bundled runtime {release}")
            return False
        release = _install_builtin(spec, reporter)
        if release is None:
            return False
    else:
        reporter.add(f"{spec.name} runtime", STATE_OK, f"bundled runtime: {release}")
    for check_spec in spec.health_contract:
        target = release / f"{check_spec.argv[0]}.py"
        if not target.is_file():
            reporter.add(f"{spec.name} health", STATE_CONFLICT, f"builtin entrypoint is missing: {target}")
            return False
        cp = run([sys.executable, "-B", str(target), *check_spec.argv[1:]])
        if cp.returncode != 0:
            reporter.add(f"{spec.name} health", STATE_CONFLICT, f"health command failed: {cp.stderr.strip()[-240:]}")
            return False
    reporter.add(f"{spec.name} health", STATE_OK, "builtin runtime checks passed")
    for command in spec.entrypoints:
        target = release / f"{command}.py"
        ok, changed = _reconcile_entrypoint(spec, command, target, manifest.get("managed_tools", {}).get(spec.name), reporter, check)
        if not ok:
            return False
        _report_resolution(spec, command, target, reporter)
    desired = _manifest_record(spec, release)
    previous = manifest.setdefault("managed_tools", {}).get(spec.name)
    if previous != desired:
        if check:
            reporter.add(f"{spec.name} ownership metadata", STATE_OUTDATED, "managed_tools metadata will be recorded by apply")
            return False
        manifest["managed_tools"][spec.name] = desired
        reporter.add(f"{spec.name} ownership metadata", STATE_CONFIGURED, "recorded in ownership manifest")
        return True
    return False


def reconcile_python_tool(
    spec: ToolSpec,
    python_exe: str,
    reporter: Reporter,
    *,
    check: bool,
    skip_install: bool,
    manifest: dict[str, Any],
) -> bool:
    if platform_name() not in spec.platforms:
        reporter.add(f"{spec.name} runtime", STATE_SKIPPED, f"not enabled on {platform_name()}")
        return False
    unsupported = _validate_supported_spec(spec)
    if unsupported:
        reporter.add(f"{spec.name} runtime", STATE_CONFLICT, unsupported)
        return False
    if skip_install:
        reporter.add(f"{spec.name} runtime", STATE_SKIPPED, "managed tool runtime reconciliation skipped")
        return False

    release = _release_dir(spec)
    if (release.exists() or release.is_symlink()) and not _owned_release(release, spec):
        reporter.add(
            f"{spec.name} runtime",
            STATE_CONFLICT,
            f"runtime path exists but ownership is not proven: {release}",
        )
        return False
    if not release.exists():
        if check:
            prerequisite_ok, version = _venv_prerequisite(python_exe)
            if not prerequisite_ok:
                reporter.add(
                    f"{spec.name} runtime",
                    STATE_CONFLICT,
                    f"base Python {version} lacks venv/ensurepip. MANUAL ACTION REQUIRED: install venv support before toolchainctl apply",
                )
            else:
                reporter.add(
                    f"{spec.name} runtime",
                    STATE_MISSING,
                    f"toolchainctl apply will install pinned isolated runtime {spec.ref[:12]} from {spec.repo}",
                )
            return False
        release = _install_release(spec, python_exe, reporter)
        if release is None:
            return False
    else:
        reporter.add(f"{spec.name} runtime", STATE_OK, f"pinned ref {spec.ref[:12]}: {release}")

    ok, detail = _health(spec, release)
    if not ok:
        reporter.add(
            f"{spec.name} health",
            STATE_CONFLICT,
            "installed managed runtime failed health validation: " + detail,
        )
        return False
    reporter.add(f"{spec.name} health", STATE_OK, detail)

    managed_tools = manifest.setdefault("managed_tools", {})
    previous = managed_tools.get(spec.name)
    entrypoint_changed = False
    for command in spec.entrypoints:
        target = _venv_command(release / "venv", command)
        entrypoint_ok, changed = _reconcile_entrypoint(spec, command, target, previous, reporter, check)
        entrypoint_changed |= changed
        if not entrypoint_ok:
            return False
        _report_resolution(spec, command, target, reporter)

    desired = _manifest_record(spec, release)
    if previous != desired:
        if check:
            reporter.add(
                f"{spec.name} ownership metadata",
                STATE_OUTDATED,
                "managed_tools metadata does not match the pinned installed runtime; toolchainctl apply will record it",
            )
            return False
        managed_tools[spec.name] = desired
        reporter.add(f"{spec.name} ownership metadata", STATE_CONFIGURED, "recorded in ownership manifest")
        return True
    reporter.add(f"{spec.name} ownership metadata", STATE_OK, "ownership manifest matches installed runtime")
    return entrypoint_changed


def reconcile_tool_specs(
    specs: dict[str, ToolSpec],
    python_exe: str,
    reporter: Reporter,
    *,
    check: bool,
    skip_install: bool,
    manifest: dict[str, Any],
) -> bool:
    changed = False
    for name in sorted(specs):
        spec = specs[name]
        if spec.runtime == "python-builtin":
            changed |= reconcile_builtin_tool(spec, reporter, check=check, manifest=manifest)
        else:
            changed |= reconcile_python_tool(spec, python_exe, reporter, check=check, skip_install=skip_install, manifest=manifest)
    return changed