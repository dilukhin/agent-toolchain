"""Managed transitional YC guard deployment owned by agent-toolchain.

The semantic policy is not implemented here. This module fetches an exact
opencode_permissions ref, asks its canonical builder to materialize the YC
runtime artifact, validates the artifact, and publishes a versioned local
runtime plus a stable yc.cmd entrypoint.

The historical LanFabric guard is treated as read-only migration metadata. It
is never overwritten or deleted by this component.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

SOURCE_REPOSITORY = "dilukhin/opencode_permissions"
SOURCE_REF = "7922d612f244882aae3d843a64393b1363b593d9"
SOURCE_ARCHIVE_URL = f"https://codeload.github.com/{SOURCE_REPOSITORY}/zip/{SOURCE_REF}"
EXPECTED_ARTIFACT_ID = "sha256:149ca1892d90f493da59d527999bdafbfb16ff4d7d53b3989de4f0e439c5e0ef"
ARTIFACT_FORMAT = "opencode-permissions-yc-transitional-artifact/v1"
ARTIFACT_OWNER = "dilukhin/opencode_permissions"
ARTIFACT_DOMAIN = b"opencode_permissions.yc_transitional_artifact.v1\n"
STATE_SCHEMA = 1
STATE_OWNER = "agent-toolchain"
STATE_COMPONENT = "yc-transitional-guard"
ENTRYPOINT_MARKER = "agent-toolchain:yc-transitional-guard:v1"
OWNERSHIP_MARKER = ".agent-toolchain-yc-guard.json"
DEPLOYMENT_FILE = "deployment.json"
ENTRY_SCRIPT = "yc_transitional_entry.py"
_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
_MAX_MEMBERS = 5000


class YcGuardDeploymentError(RuntimeError):
    def __init__(self, code: str, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _require(condition: bool, code: str, detail: Any = None) -> None:
    if not condition:
        raise YcGuardDeploymentError(code, detail)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_json(path: Path, code: str) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), code, str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YcGuardDeploymentError(code, type(exc).__name__) from exc
    _require(isinstance(value, dict), code, "root must be object")
    return value


def _safe_relative(value: Any, code: str) -> Path:
    _require(isinstance(value, str) and value, code)
    posix = PurePosixPath(value)
    _require(not posix.is_absolute() and all(part not in {"", ".", ".."} for part in posix.parts), code, value)
    return Path(*posix.parts)


def _artifact_identity_core(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_format": manifest.get("artifact_format"),
        "owner": manifest.get("owner"),
        "target": manifest.get("target"),
        "policy": manifest.get("policy"),
        "files": manifest.get("files"),
        "constraints": manifest.get("constraints"),
        "transitional_auto_allow_mutations": manifest.get("transitional_auto_allow_mutations"),
    }


def _artifact_id(manifest: dict[str, Any]) -> str:
    return "sha256:" + _sha256_bytes(ARTIFACT_DOMAIN + _canonical(_artifact_identity_core(manifest)))


def _segment(artifact_id: str) -> str:
    _require(
        isinstance(artifact_id, str)
        and artifact_id.startswith("sha256:")
        and len(artifact_id) == 71
        and all(ch in "0123456789abcdef" for ch in artifact_id.split(":", 1)[1]),
        "YC_ARTIFACT_ID_INVALID",
    )
    return "sha256-" + artifact_id.split(":", 1)[1]


def validate_artifact(artifact_dir: Path, *, require_segment_name: bool = True) -> dict[str, Any]:
    artifact_dir = Path(artifact_dir).resolve()
    manifest = _load_json(artifact_dir / "manifest.json", "YC_ARTIFACT_MANIFEST_INVALID")
    _require(manifest.get("schema") == 1, "YC_ARTIFACT_SCHEMA_UNSUPPORTED")
    _require(manifest.get("artifact_format") == ARTIFACT_FORMAT, "YC_ARTIFACT_FORMAT_UNSUPPORTED")
    _require(manifest.get("status") == "transitional_ready", "YC_ARTIFACT_NOT_READY")
    _require(manifest.get("owner") == ARTIFACT_OWNER, "YC_ARTIFACT_OWNER_MISMATCH")
    target = manifest.get("target")
    _require(isinstance(target, dict), "YC_ARTIFACT_TARGET_INVALID")
    _require(target.get("provider") == "yandex-cloud", "YC_ARTIFACT_PROVIDER_MISMATCH")
    _require(target.get("platform") == "windows", "YC_ARTIFACT_PLATFORM_MISMATCH")
    _require(target.get("mode") == "transitional", "YC_ARTIFACT_MODE_MISMATCH")

    constraints = manifest.get("constraints")
    _require(isinstance(constraints, dict), "YC_ARTIFACT_CONSTRAINTS_INVALID")
    required = {
        "canonical_policy_only": True,
        "setup_semantic_rewrite": False,
        "developer_checkout_dependency": False,
        "hard_deny_terminal": True,
        "unknown_or_opaque_not_allow": True,
        "runtime_path_resolution": False,
        "downstream_executable_identity_required": True,
        "caller_approval_flags_trusted": False,
        "real_executor_packaged": False,
        "transitional_exact_mutation_auto_allow": True,
        "full_authorization_binding_complete": False,
    }
    for key, expected in required.items():
        _require(constraints.get(key) == expected, "YC_ARTIFACT_CONSTRAINT_MISMATCH", key)

    expected_id = _artifact_id(manifest)
    _require(manifest.get("artifact_id") == expected_id, "YC_ARTIFACT_ID_MISMATCH")
    expected_segment = _segment(expected_id)
    _require(manifest.get("artifact_path_segment") == expected_segment, "YC_ARTIFACT_SEGMENT_MISMATCH")
    if require_segment_name:
        _require(artifact_dir.name == expected_segment, "YC_ARTIFACT_DIRECTORY_MISMATCH")

    files = manifest.get("files")
    _require(isinstance(files, list) and files, "YC_ARTIFACT_FILES_INVALID")
    expected_files = {"manifest.json"}
    seen: set[str] = set()
    for item in files:
        _require(isinstance(item, dict), "YC_ARTIFACT_FILE_ENTRY_INVALID")
        rel = _safe_relative(item.get("path"), "YC_ARTIFACT_FILE_PATH_INVALID")
        rel_text = rel.as_posix()
        _require(rel_text not in seen, "YC_ARTIFACT_FILE_DUPLICATE", rel_text)
        seen.add(rel_text)
        expected_files.add(rel_text)
        path = artifact_dir / rel
        _require(path.is_file() and not path.is_symlink(), "YC_ARTIFACT_FILE_MISSING", rel_text)
        data = path.read_bytes()
        _require(len(data) == item.get("size"), "YC_ARTIFACT_FILE_SIZE_MISMATCH", rel_text)
        _require(_sha256_bytes(data) == item.get("sha256"), "YC_ARTIFACT_FILE_DIGEST_MISMATCH", rel_text)

    actual_files: set[str] = set()
    for path in artifact_dir.rglob("*"):
        _require(not path.is_symlink(), "YC_ARTIFACT_SYMLINK_FORBIDDEN", str(path))
        if path.is_file():
            actual_files.add(path.relative_to(artifact_dir).as_posix())
        elif not path.is_dir():
            raise YcGuardDeploymentError("YC_ARTIFACT_SPECIAL_FILE_FORBIDDEN", str(path))
    _require(actual_files == expected_files, "YC_ARTIFACT_FILESET_MISMATCH")

    policy = manifest.get("policy")
    _require(isinstance(policy, dict), "YC_ARTIFACT_POLICY_INVALID")
    _require(policy.get("schema") == "yc-policy/v1", "YC_ARTIFACT_POLICY_SCHEMA_UNSUPPORTED")
    policy_rel = _safe_relative(policy.get("relative_path"), "YC_ARTIFACT_POLICY_PATH_INVALID")
    _require(_sha256_file(artifact_dir / policy_rel) == policy.get("sha256"), "YC_ARTIFACT_POLICY_DIGEST_MISMATCH")
    return manifest


def default_data_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain").resolve()
    return (Path.home() / ".local" / "share" / "agent-toolchain").resolve()


def default_bin_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_BIN_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "bin").resolve()
    return (Path.home() / ".local" / "bin").resolve()


def default_state_dir() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "state").resolve()
    return (Path.home() / ".local" / "state" / "agent-toolchain").resolve()


def default_legacy_guard_root() -> Path:
    override = os.environ.get("YC_LEGACY_GUARD_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    _require(bool(local), "LOCALAPPDATA_REQUIRED")
    return (Path(local) / "LanFabric" / "yc-guard").resolve()


def _is_absolute_yc_path(value: str) -> bool:
    try:
        wp = PureWindowsPath(value)
        if wp.is_absolute() and wp.name.lower() == "yc.exe":
            return True
    except (TypeError, ValueError):
        pass
    try:
        p = Path(value)
        return p.is_absolute() and p.name.lower() == "yc.exe"
    except (TypeError, ValueError, OSError):
        return False


def _walk_string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_string_values(item)


def resolve_legacy_downstream(legacy_guard_root: Path) -> Path:
    legacy_guard_root = Path(legacy_guard_root).resolve()
    state = _load_json(legacy_guard_root / "state.json", "LEGACY_GUARD_STATE_INVALID")
    candidates = sorted({value for value in _walk_string_values(state) if _is_absolute_yc_path(value)})
    _require(len(candidates) == 1, "LEGACY_DOWNSTREAM_NOT_UNIQUE", len(candidates))
    return Path(candidates[0]).resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_downstream(
    downstream: Path,
    *,
    legacy_guard_root: Path,
    data_dir: Path,
    bin_dir: Path,
) -> dict[str, str]:
    downstream = Path(downstream).resolve()
    _require(downstream.name.lower() == "yc.exe", "DOWNSTREAM_BASENAME_UNEXPECTED")
    _require(downstream.is_file() and not downstream.is_symlink(), "DOWNSTREAM_EXECUTABLE_INVALID")
    for forbidden_root, code in (
        (Path(legacy_guard_root).resolve(), "DOWNSTREAM_POINTS_INTO_LEGACY_GUARD"),
        (Path(data_dir).resolve(), "DOWNSTREAM_POINTS_INTO_TOOLCHAIN_DATA"),
        (Path(bin_dir).resolve(), "DOWNSTREAM_POINTS_INTO_TOOLCHAIN_BIN"),
    ):
        _require(not _is_relative_to(downstream, forbidden_root), code)
    digest = _sha256_file(downstream)
    return {
        "resolved_path": str(downstream),
        "sha256": digest,
        "object_identity": "sha256:" + digest,
    }


def _path_index(directory: Path, path_env: str | None = None) -> int | None:
    target = os.path.normcase(str(Path(directory).resolve()))
    for index, raw in enumerate((path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep)):
        if not raw:
            continue
        try:
            current = os.path.normcase(str(Path(raw).expanduser().resolve()))
        except OSError:
            continue
        if current == target:
            return index
    return None


def preflight_effective_path(bin_dir: Path, current_yc: str | None = None, path_env: str | None = None) -> dict[str, Any]:
    bin_index = _path_index(bin_dir, path_env)
    _require(bin_index is not None, "TOOLCHAIN_BIN_NOT_ON_CURRENT_PATH", str(bin_dir))
    observed = current_yc if current_yc is not None else shutil.which("yc")
    if not observed:
        return {"bin_index": bin_index, "current_yc": None}
    current_parent = Path(observed).resolve().parent
    current_index = _path_index(current_parent, path_env)
    if current_parent != Path(bin_dir).resolve():
        _require(current_index is not None, "YC_CURRENT_YC_ORIGIN_NOT_IN_PATH", str(current_parent))
        _require(current_index >= bin_index, "YC_PATH_SHADOW_CONFLICT", str(current_parent))
    return {"bin_index": bin_index, "current_yc": str(Path(observed).resolve())}


def _download_bytes(url: str, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "agent-toolchain-yc-guard/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YcGuardDeploymentError("SOURCE_DOWNLOAD_FAILED", type(exc).__name__) from exc
    _require(len(data) <= max_bytes, "SOURCE_ARCHIVE_TOO_LARGE")
    return data


def _extract_source_archive(data: bytes, destination: Path) -> Path:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise YcGuardDeploymentError("SOURCE_ARCHIVE_INVALID") from exc
    destination = Path(destination).resolve()
    roots: set[str] = set()
    total = 0
    with archive:
        members = archive.infolist()
        _require(len(members) <= _MAX_MEMBERS, "SOURCE_ARCHIVE_TOO_MANY_MEMBERS")
        for info in members:
            _require("\\" not in info.filename, "SOURCE_ARCHIVE_BACKSLASH_PATH", info.filename)
            posix = PurePosixPath(info.filename)
            _require(
                bool(posix.parts)
                and not posix.is_absolute()
                and all(part not in {"", ".", ".."} for part in posix.parts),
                "SOURCE_ARCHIVE_UNSAFE_PATH",
                info.filename,
            )
            roots.add(posix.parts[0])
            _require(((info.external_attr >> 16) & 0o170000) != 0o120000, "SOURCE_ARCHIVE_SYMLINK_FORBIDDEN", info.filename)
            target = destination.joinpath(*posix.parts).resolve(strict=False)
            _require(_is_relative_to(target, destination), "SOURCE_ARCHIVE_ESCAPE", info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += info.file_size
            _require(total <= _MAX_EXTRACTED_BYTES, "SOURCE_ARCHIVE_EXPANDS_TOO_LARGE")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    _require(len(roots) == 1, "SOURCE_ARCHIVE_ROOT_INVALID", sorted(roots))
    root = destination / next(iter(roots))
    _require((root / "tools" / "build_yc_transitional_artifact.py").is_file(), "SOURCE_BUILDER_MISSING")
    return root


def materialize_canonical_artifact(source_root: Path, output_root: Path) -> Path:
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    builder = source_root / "tools" / "build_yc_transitional_artifact.py"
    _require(builder.is_file() and not builder.is_symlink(), "SOURCE_BUILDER_INVALID")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(builder),
            "--root",
            str(source_root),
            "--write",
            "--output-root",
            str(output_root),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    _require(completed.returncode == 0, "SOURCE_BUILDER_FAILED", completed.stderr[-1000:])
    directories = [path for path in output_root.iterdir() if path.is_dir()]
    _require(len(directories) == 1, "SOURCE_BUILDER_OUTPUT_AMBIGUOUS", len(directories))
    manifest = validate_artifact(directories[0])
    _require(manifest.get("artifact_id") == EXPECTED_ARTIFACT_ID, "SOURCE_ARTIFACT_ID_MISMATCH", manifest.get("artifact_id"))
    return directories[0]


def _entrypoint_bytes(python_executable: Path, entry_script: Path, artifact_id: str) -> bytes:
    python_text = str(Path(python_executable).resolve())
    script_text = str(Path(entry_script).resolve())
    _require('"' not in python_text and '"' not in script_text, "ENTRYPOINT_PATH_QUOTE_UNSUPPORTED")
    text = (
        f"@REM {ENTRYPOINT_MARKER} {artifact_id}\r\n"
        "@echo off\r\n"
        "@setlocal\r\n"
        '@set "PYTHONUTF8=1"\r\n'
        '@set "PYTHONIOENCODING=utf-8"\r\n'
        f'@"{python_text}" -B "{script_text}" %*\r\n'
        '@set "_AGENT_TOOLCHAIN_YC_RC=%ERRORLEVEL%"\r\n'
        "@endlocal & exit /b %_AGENT_TOOLCHAIN_YC_RC%\r\n"
    )
    return text.encode("utf-8")


def _state_path(state_dir: Path) -> Path:
    return Path(state_dir) / "yc-transitional-guard.json"


def _release_marker(release: Path) -> dict[str, Any]:
    return _load_json(release / OWNERSHIP_MARKER, "YC_RELEASE_MARKER_INVALID")


def _validate_release(release: Path, desired: dict[str, Any]) -> None:
    _require(release.is_dir() and not release.is_symlink(), "YC_RELEASE_INVALID", str(release))
    marker = _release_marker(release)
    for key in ("schema", "owner", "component", "artifact_id", "source_ref", "downstream_sha256"):
        _require(marker.get(key) == desired.get(key), "YC_RELEASE_OWNERSHIP_MISMATCH", key)
    artifact = release / "artifact"
    manifest = validate_artifact(artifact, require_segment_name=False)
    _require(manifest["artifact_id"] == desired["artifact_id"], "YC_RELEASE_ARTIFACT_MISMATCH")
    entry_script = release / ENTRY_SCRIPT
    deployment = release / DEPLOYMENT_FILE
    _require(_sha256_file(entry_script) == marker.get("entry_script_sha256"), "YC_RELEASE_ENTRY_SCRIPT_MODIFIED")
    _require(_sha256_file(deployment) == marker.get("deployment_sha256"), "YC_RELEASE_DEPLOYMENT_MODIFIED")


def _publish_release(
    *,
    artifact_dir: Path,
    downstream: dict[str, str],
    data_dir: Path,
    entry_script_source: Path,
) -> tuple[Path, dict[str, Any]]:
    manifest = validate_artifact(artifact_dir)
    release = Path(data_dir) / "tools" / "yc-guard" / manifest["artifact_path_segment"]
    deployment = {
        "schema": 1,
        "owner": STATE_OWNER,
        "component": STATE_COMPONENT,
        "artifact_id": manifest["artifact_id"],
        "source_repository": SOURCE_REPOSITORY,
        "source_ref": SOURCE_REF,
        "artifact_relative_path": "artifact",
        "downstream_path": downstream["resolved_path"],
        "downstream_sha256": downstream["sha256"],
    }
    deployment_bytes = (json.dumps(deployment, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    entry_bytes = Path(entry_script_source).read_bytes()
    desired_marker = {
        "schema": 1,
        "owner": STATE_OWNER,
        "component": STATE_COMPONENT,
        "artifact_id": manifest["artifact_id"],
        "source_ref": SOURCE_REF,
        "downstream_sha256": downstream["sha256"],
        "entry_script_sha256": _sha256_bytes(entry_bytes),
        "deployment_sha256": _sha256_bytes(deployment_bytes),
    }
    if release.exists() or release.is_symlink():
        _validate_release(release, desired_marker)
        return release, desired_marker

    release.parent.mkdir(parents=True, exist_ok=True)
    stage = release.parent / f".{release.name}.stage-{os.getpid()}-{uuid.uuid4().hex}"
    _require(not stage.exists(), "YC_RELEASE_STAGE_CONFLICT", str(stage))
    try:
        stage.mkdir()
        shutil.copytree(artifact_dir, stage / "artifact")
        (stage / ENTRY_SCRIPT).write_bytes(entry_bytes)
        (stage / DEPLOYMENT_FILE).write_bytes(deployment_bytes)
        (stage / OWNERSHIP_MARKER).write_text(
            json.dumps(desired_marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _validate_release(stage, desired_marker)
        _require(not release.exists(), "YC_RELEASE_APPEARED_CONCURRENTLY", str(release))
        os.replace(stage, release)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return release, desired_marker


def _load_existing_state(state_dir: Path) -> dict[str, Any] | None:
    path = _state_path(state_dir)
    if not path.exists():
        return None
    state = _load_json(path, "YC_DEPLOYMENT_STATE_INVALID")
    _require(state.get("schema") == STATE_SCHEMA, "YC_DEPLOYMENT_STATE_SCHEMA_UNSUPPORTED")
    _require(state.get("owner") == STATE_OWNER and state.get("component") == STATE_COMPONENT, "YC_DEPLOYMENT_STATE_OWNER_MISMATCH")
    _require(state.get("phase") in {"prepared", "active", "disabled"}, "YC_DEPLOYMENT_STATE_PHASE_INVALID")
    return state


def inspect_guard(
    *,
    legacy_guard_root: Path | None = None,
    data_dir: Path | None = None,
    bin_dir: Path | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    legacy = Path(legacy_guard_root or default_legacy_guard_root()).resolve()
    data = Path(data_dir or default_data_dir()).resolve()
    public_bin = Path(bin_dir or default_bin_dir()).resolve()
    state_root = Path(state_dir or default_state_dir()).resolve()
    result: dict[str, Any] = {
        "legacy_guard_present": legacy.is_dir(),
        "legacy_state_present": (legacy / "state.json").is_file(),
        "toolchain_bin_on_path": _path_index(public_bin) is not None,
        "current_yc_owned": False,
        "deployment_phase": None,
        "artifact_id": None,
        "downstream_reference_valid": False,
    }
    if result["legacy_state_present"]:
        try:
            downstream = resolve_legacy_downstream(legacy)
            result["downstream_reference_valid"] = downstream.is_file() and downstream.name.lower() == "yc.exe"
        except YcGuardDeploymentError:
            pass
    state = _load_existing_state(state_root)
    if state:
        result["deployment_phase"] = state.get("phase")
        result["artifact_id"] = state.get("artifact_id")
    entrypoint = public_bin / "yc.cmd"
    if entrypoint.is_file() and state and state.get("entrypoint_sha256") == _sha256_file(entrypoint):
        result["current_yc_owned"] = True
    return result


def apply_guard(
    *,
    artifact_dir: Path,
    legacy_guard_root: Path,
    entry_script_source: Path,
    data_dir: Path,
    bin_dir: Path,
    state_dir: Path,
    python_executable: Path | None = None,
    downstream_yc: Path | None = None,
    require_effective_path: bool = True,
) -> dict[str, Any]:
    legacy = Path(legacy_guard_root).resolve()
    data = Path(data_dir).resolve()
    public_bin = Path(bin_dir).resolve()
    state_root = Path(state_dir).resolve()
    python_path = Path(python_executable or sys.executable).resolve()

    manifest = validate_artifact(Path(artifact_dir))
    downstream_path = Path(downstream_yc).resolve() if downstream_yc is not None else resolve_legacy_downstream(legacy)
    downstream = validate_downstream(
        downstream_path,
        legacy_guard_root=legacy,
        data_dir=data,
        bin_dir=public_bin,
    )
    if require_effective_path:
        preflight_effective_path(public_bin)

    existing_state = _load_existing_state(state_root)
    entrypoint = public_bin / "yc.cmd"
    if entrypoint.exists() or entrypoint.is_symlink():
        if not existing_state:
            raise YcGuardDeploymentError("YC_ENTRYPOINT_UNKNOWN_CONFLICT", str(entrypoint))
        _require(entrypoint.is_file() and not entrypoint.is_symlink(), "YC_ENTRYPOINT_INVALID")
        _require(_sha256_file(entrypoint) == existing_state.get("entrypoint_sha256"), "YC_ENTRYPOINT_MODIFIED")

    release, marker = _publish_release(
        artifact_dir=Path(artifact_dir),
        downstream=downstream,
        data_dir=data,
        entry_script_source=Path(entry_script_source),
    )
    entry_bytes = _entrypoint_bytes(python_path, release / ENTRY_SCRIPT, manifest["artifact_id"])
    entry_hash = _sha256_bytes(entry_bytes)

    desired_state = {
        "schema": STATE_SCHEMA,
        "owner": STATE_OWNER,
        "component": STATE_COMPONENT,
        "phase": "prepared",
        "artifact_id": manifest["artifact_id"],
        "source_repository": SOURCE_REPOSITORY,
        "source_ref": SOURCE_REF,
        "release_path": str(release),
        "entrypoint_path": str(entrypoint),
        "entrypoint_sha256": entry_hash,
        "downstream_path": downstream["resolved_path"],
        "downstream_sha256": downstream["sha256"],
        "legacy_guard_root": str(legacy),
        "release_marker_sha256": _sha256_file(release / OWNERSHIP_MARKER),
    }

    if existing_state:
        identity_fields = ("artifact_id", "source_ref", "release_path", "entrypoint_path", "downstream_path", "downstream_sha256")
        for field in identity_fields:
            _require(existing_state.get(field) == desired_state.get(field), "YC_EXISTING_DEPLOYMENT_CONFLICT", field)
        if existing_state.get("phase") == "active":
            _require(entrypoint.is_file() and not entrypoint.is_symlink(), "YC_ENTRYPOINT_MISSING")
            _require(_sha256_file(entrypoint) == existing_state.get("entrypoint_sha256"), "YC_ENTRYPOINT_MODIFIED")
            _validate_release(release, marker)
            if require_effective_path:
                resolved = shutil.which("yc")
                _require(resolved is not None and Path(resolved).resolve() == entrypoint.resolve(), "YC_EFFECTIVE_READBACK_FAILED", resolved)
            return {"changed": False, "artifact_id": manifest["artifact_id"], "effective_readback": "PASS"}

    _atomic_write(_state_path(state_root), (json.dumps(desired_state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    public_bin.mkdir(parents=True, exist_ok=True)
    _atomic_write(entrypoint, entry_bytes)

    active = dict(desired_state)
    active["phase"] = "active"
    _atomic_write(_state_path(state_root), (json.dumps(active, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    _validate_release(release, marker)
    _require(_sha256_file(entrypoint) == entry_hash, "YC_ENTRYPOINT_READBACK_FAILED")
    if require_effective_path:
        resolved = shutil.which("yc")
        _require(resolved is not None and Path(resolved).resolve() == entrypoint.resolve(), "YC_EFFECTIVE_READBACK_FAILED", resolved)
    return {"changed": True, "artifact_id": manifest["artifact_id"], "effective_readback": "PASS"}


def disable_guard(*, bin_dir: Path, state_dir: Path) -> dict[str, Any]:
    public_bin = Path(bin_dir).resolve()
    state_root = Path(state_dir).resolve()
    state = _load_existing_state(state_root)
    _require(state is not None, "YC_DEPLOYMENT_STATE_MISSING")
    entrypoint = Path(state["entrypoint_path"]).resolve()
    _require(entrypoint == (public_bin / "yc.cmd").resolve(), "YC_ENTRYPOINT_PATH_MISMATCH")
    if entrypoint.exists() or entrypoint.is_symlink():
        _require(entrypoint.is_file() and not entrypoint.is_symlink(), "YC_ENTRYPOINT_INVALID")
        _require(_sha256_file(entrypoint) == state.get("entrypoint_sha256"), "YC_ENTRYPOINT_MODIFIED")
        entrypoint.unlink()
    disabled = dict(state)
    disabled["phase"] = "disabled"
    _atomic_write(_state_path(state_root), (json.dumps(disabled, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return {"changed": state.get("phase") != "disabled", "artifact_id": state.get("artifact_id"), "runtime_cached": True}


def _materialize_for_apply(source_root: Path | None, temporary: Path) -> Path:
    if source_root is None:
        archive = _download_bytes(SOURCE_ARCHIVE_URL, max_bytes=_MAX_ARCHIVE_BYTES)
        source = _extract_source_archive(archive, temporary / "source")
    else:
        source = Path(source_root).resolve()
        _require((source / "tools" / "build_yc_transitional_artifact.py").is_file(), "SOURCE_ROOT_INVALID")
    output = temporary / "artifact"
    output.mkdir()
    return materialize_canonical_artifact(source, output)


def add_cli_parser(subparsers) -> None:
    parser = subparsers.add_parser("yc-guard", help="manage the transitional owned YC guard")
    actions = parser.add_subparsers(dest="yc_guard_command", required=True)
    actions.add_parser("check", help="read-only YC guard deployment inspection")
    apply = actions.add_parser("apply", help="install the pinned transitional YC guard")
    apply.add_argument("--source-root", help=argparse.SUPPRESS)
    apply.add_argument("--legacy-guard-root")
    apply.add_argument("--downstream-yc", help=argparse.SUPPRESS)
    disable = actions.add_parser("disable", help="remove only the owned yc.cmd entrypoint")
    disable.add_argument("--bin-dir", help=argparse.SUPPRESS)
    disable.add_argument("--state-dir", help=argparse.SUPPRESS)


def run_cli(args) -> int:
    try:
        if args.yc_guard_command == "check":
            print(json.dumps(inspect_guard(), sort_keys=True, ensure_ascii=False))
            return 0
        if args.yc_guard_command == "disable":
            result = disable_guard(
                bin_dir=Path(args.bin_dir).resolve() if args.bin_dir else default_bin_dir(),
                state_dir=Path(args.state_dir).resolve() if args.state_dir else default_state_dir(),
            )
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0

        legacy = Path(args.legacy_guard_root).resolve() if args.legacy_guard_root else default_legacy_guard_root()
        downstream = Path(args.downstream_yc).resolve() if args.downstream_yc else None
        with tempfile.TemporaryDirectory(prefix="agent-toolchain-yc-") as td:
            root = Path(td)
            artifact = _materialize_for_apply(Path(args.source_root).resolve() if args.source_root else None, root)
            result = apply_guard(
                artifact_dir=artifact,
                legacy_guard_root=legacy,
                entry_script_source=Path(__file__).resolve().parent / ENTRY_SCRIPT,
                data_dir=default_data_dir(),
                bin_dir=default_bin_dir(),
                state_dir=default_state_dir(),
                downstream_yc=downstream,
                require_effective_path=True,
            )
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except YcGuardDeploymentError as exc:
        detail = f": {exc.detail}" if exc.detail not in (None, "") else ""
        print(f"modified/conflict  YC transitional guard  {exc.code}{detail}", file=sys.stderr)
        return 2
