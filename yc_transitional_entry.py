#!/usr/bin/env python3
"""Self-contained runtime entrypoint for the managed transitional YC guard."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable

ARTIFACT_FORMAT = "opencode-permissions-yc-transitional-artifact/v1"
ARTIFACT_OWNER = "dilukhin/opencode_permissions"
ARTIFACT_DOMAIN = b"opencode_permissions.yc_transitional_artifact.v1\n"
DEPLOYMENT_FILE = "deployment.json"
DENY_EXIT = 77
ASK_EXIT = 78
FAIL_CLOSED_EXIT = 79
NEW_SOURCE_REF = "e363d99333248f99d2934837a259955decb4d1fa"
NEW_ARTIFACT_ID = "sha256:61bf7e809a86e1117e2e6886e571d8a835601837fdc3479f78988af6ac1d05d5"


class RuntimeGuardError(RuntimeError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeGuardError(code)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _artifact_core(manifest: dict[str, Any]) -> dict[str, Any]:
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
    return "sha256:" + hashlib.sha256(ARTIFACT_DOMAIN + _canonical(_artifact_core(manifest))).hexdigest()


def _safe_rel(value: Any) -> Path:
    _require(isinstance(value, str) and bool(value), "ARTIFACT_PATH_INVALID")
    posix = PurePosixPath(value)
    _require(not posix.is_absolute() and all(part not in {"", ".", ".."} for part in posix.parts), "ARTIFACT_PATH_INVALID")
    return Path(*posix.parts)


def validate_runtime(release: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    release = Path(release).resolve()
    deployment_path = release / DEPLOYMENT_FILE
    _require(deployment_path.is_file() and not deployment_path.is_symlink(), "DEPLOYMENT_MISSING")
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    _require(isinstance(deployment, dict) and deployment.get("schema") == 1, "DEPLOYMENT_INVALID")
    _require(deployment.get("owner") == "agent-toolchain", "DEPLOYMENT_OWNER_MISMATCH")
    _require(deployment.get("component") == "yc-transitional-guard", "DEPLOYMENT_COMPONENT_MISMATCH")

    artifact = release / deployment.get("artifact_relative_path", "")
    _require(artifact.is_dir() and not artifact.is_symlink(), "ARTIFACT_MISSING")
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    _require(isinstance(manifest, dict) and manifest.get("schema") == 1, "ARTIFACT_MANIFEST_INVALID")
    _require(manifest.get("artifact_format") == ARTIFACT_FORMAT, "ARTIFACT_FORMAT_UNSUPPORTED")
    _require(manifest.get("owner") == ARTIFACT_OWNER, "ARTIFACT_OWNER_MISMATCH")
    _require(manifest.get("artifact_id") == _artifact_id(manifest), "ARTIFACT_ID_MISMATCH")
    _require(manifest.get("artifact_id") == deployment.get("artifact_id"), "DEPLOYMENT_ARTIFACT_MISMATCH")
    constraints = manifest.get("constraints")
    _require(isinstance(constraints, dict), "ARTIFACT_CONSTRAINTS_INVALID")
    _require(constraints.get("canonical_policy_only") is True, "ARTIFACT_CANONICAL_POLICY_REQUIRED")
    _require(constraints.get("runtime_path_resolution") is False, "ARTIFACT_PATH_RESOLUTION_FORBIDDEN")
    _require(constraints.get("caller_approval_flags_trusted") is False, "ARTIFACT_CALLER_APPROVAL_FORBIDDEN")

    expected_files = {"manifest.json"}
    for item in manifest.get("files") or []:
        _require(isinstance(item, dict), "ARTIFACT_FILE_ENTRY_INVALID")
        rel = _safe_rel(item.get("path"))
        expected_files.add(rel.as_posix())
        path = artifact / rel
        _require(path.is_file() and not path.is_symlink(), "ARTIFACT_FILE_MISSING")
        _require(path.stat().st_size == item.get("size"), "ARTIFACT_FILE_SIZE_MISMATCH")
        _require(_sha256_file(path) == item.get("sha256"), "ARTIFACT_FILE_DIGEST_MISMATCH")

    actual_files: set[str] = set()
    for path in artifact.rglob("*"):
        _require(not path.is_symlink(), "ARTIFACT_SYMLINK_FORBIDDEN")
        if path.is_file():
            actual_files.add(path.relative_to(artifact).as_posix())
        else:
            _require(path.is_dir(), "ARTIFACT_SPECIAL_FILE_FORBIDDEN")
    _require(actual_files == expected_files, "ARTIFACT_FILESET_MISMATCH")

    downstream = Path(deployment.get("downstream_path", "")).resolve()
    _require(downstream.name.lower() == "yc.exe", "DOWNSTREAM_BASENAME_UNEXPECTED")
    _require(downstream.is_file() and not downstream.is_symlink(), "DOWNSTREAM_MISSING")
    observed = _sha256_file(downstream)
    _require(observed == deployment.get("downstream_sha256"), "DOWNSTREAM_IDENTITY_MISMATCH")
    return artifact, deployment, manifest


def _owned_core(root: Path) -> str:
    """Independently verify the published core before importing setup code."""
    _require(root.is_dir() and not root.is_symlink(), "CORE_UNOWNED")
    marker_path = root / ".agent-toolchain-managed-core.json"
    _require(marker_path.is_file() and not marker_path.is_symlink(), "CORE_MARKER_MISSING")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    _require(isinstance(marker, dict) and marker.get("schema") == 1 and marker.get("owner") == "agent-toolchain", "CORE_MARKER_INVALID")
    payload = marker.get("payload")
    _require(isinstance(payload, list) and bool(payload), "CORE_PAYLOAD_MISSING")
    digest = hashlib.sha256()
    seen = set()
    for row in payload:
        _require(isinstance(row, dict), "CORE_PAYLOAD_INVALID")
        name, expected = row.get("path"), row.get("sha256")
        _require(isinstance(name, str) and name and isinstance(expected, str) and len(expected) == 64, "CORE_PAYLOAD_INVALID")
        rel = PurePosixPath(name)
        _require("\\" not in name and not rel.is_absolute() and all(x not in {"", ".", ".."} for x in rel.parts), "CORE_PAYLOAD_PATH_INVALID")
        _require(rel.as_posix() not in seen, "CORE_PAYLOAD_DUPLICATE")
        seen.add(rel.as_posix())
        file = root.joinpath(*rel.parts)
        _require(file.is_file() and not file.is_symlink(), "CORE_PAYLOAD_FILE_MISSING")
        contents = file.read_bytes()
        _require(hashlib.sha256(contents).hexdigest() == expected, "CORE_PAYLOAD_MODIFIED")
        digest.update(rel.as_posix().encode("utf-8") + b"\0" + contents + b"\0")
    found = set()
    for path in root.rglob("*"):
        if "__pycache__" in path.parts:
            continue
        _require(not path.is_symlink(), "CORE_SYMLINK_FORBIDDEN")
        if path.is_file() and path != marker_path:
            found.add(path.relative_to(root).as_posix())
        else:
            _require(path.is_dir() or path == marker_path, "CORE_SPECIAL_FILE_FORBIDDEN")
    _require(found == seen, "CORE_PAYLOAD_FILESET_MISMATCH")
    _require(digest.hexdigest() == marker.get("fingerprint"), "CORE_FINGERPRINT_MISMATCH")
    _require("setup_yc_transitional_guard.py" in seen, "CORE_GUARD_SETUP_MISSING")
    return marker["fingerprint"]


def _owned_setup(release: Path, deployment: dict[str, Any], manifest: dict[str, Any]):
    """Bind paths to the integrity-checked release, then load owned setup."""
    _require(manifest["artifact_id"] == NEW_ARTIFACT_ID and deployment.get("source_ref") == NEW_SOURCE_REF, "ACTIVATION_PIN_MISMATCH")
    release = Path(release)
    _require(release.is_dir() and not release.is_symlink(), "ACTIVATION_RELEASE_INVALID")
    release = release.resolve()
    _require(Path(__file__).resolve() == release / "yc_transitional_entry.py", "ACTIVATION_RUNTIME_ORIGIN_MISMATCH")
    data = release.parents[2]
    state_dir, bin_dir = data / "state", data / "bin"
    expected = {
        "managed_data_dir": str(data), "managed_state_dir": str(state_dir),
        "managed_bin_dir": str(bin_dir), "managed_entrypoint": str(bin_dir / "yc.cmd"),
        "managed_journal": str(state_dir / "yc-transitional-upgrade.json"),
        "managed_lock": str(state_dir / "yc-transitional-upgrade.lock"),
    }
    for key, value in expected.items():
        _require(deployment.get(key) == value, "ACTIVATION_MANAGED_PATH_MISMATCH")
    _require(release == data / "tools" / "yc-guard" / NEW_ARTIFACT_ID.replace(":", "-", 1), "ACTIVATION_RELEASE_PATH_MISMATCH")
    marker_path = release / ".agent-toolchain-yc-guard.json"
    _require(marker_path.is_file() and not marker_path.is_symlink(), "ACTIVATION_MARKER_MISSING")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    _require(isinstance(marker, dict) and marker.get("schema") == 1 and marker.get("owner") == "agent-toolchain"
             and marker.get("component") == "yc-transitional-guard", "ACTIVATION_MARKER_INVALID")
    _require(marker.get("source_ref") == NEW_SOURCE_REF and marker.get("artifact_id") == NEW_ARTIFACT_ID, "ACTIVATION_MARKER_PIN_MISMATCH")
    _require(marker.get("deployment_sha256") == _sha256_file(release / DEPLOYMENT_FILE), "ACTIVATION_DEPLOYMENT_MODIFIED")
    _require(marker.get("entry_script_sha256") == _sha256_file(release / "yc_transitional_entry.py"), "ACTIVATION_SCRIPT_MODIFIED")
    _require(marker.get("downstream_sha256") == deployment["downstream_sha256"], "ACTIVATION_DOWNSTREAM_MISMATCH")
    _require(Path(deployment.get("managed_legacy_guard_root", "")).is_absolute(), "ACTIVATION_LEGACY_PATH_MISSING")
    core = data / "core"
    fingerprint = _owned_core(core)
    module_path = core / "setup_yc_transitional_guard.py"
    bootstrap_path = core / "bootstrap_core.py"
    _require(bootstrap_path.is_file() and not bootstrap_path.is_symlink(), "ACTIVATION_BOOTSTRAP_UNAVAILABLE")
    bootstrap_spec = importlib.util.spec_from_file_location("_owned_yc_guard_bootstrap_" + fingerprint, bootstrap_path)
    _require(bootstrap_spec is not None and bootstrap_spec.loader is not None, "ACTIVATION_BOOTSTRAP_UNAVAILABLE")
    bootstrap = importlib.util.module_from_spec(bootstrap_spec)
    spec = importlib.util.spec_from_file_location("_owned_yc_guard_setup_" + fingerprint, module_path)
    _require(spec is not None and spec.loader is not None, "ACTIVATION_SETUP_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(core))
    try:
        bootstrap_spec.loader.exec_module(bootstrap)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    module.bootstrap_core = bootstrap
    _require(module.SOURCE_REF == NEW_SOURCE_REF and module.EXPECTED_ARTIFACT_ID == NEW_ARTIFACT_ID, "ACTIVATION_SETUP_PIN_MISMATCH")
    return module, data, bin_dir, state_dir


@contextmanager
def _activation_gate(release: Path, deployment: dict[str, Any], manifest: dict[str, Any]):
    module, data, bin_dir, state_dir = _owned_setup(release, deployment, manifest)
    with module._upgrade_lock(state_dir, create=False):
        journal = module._load_upgrade_journal(state_dir)
        _require(journal is not None and journal["phase"] == "complete"
                 and journal["mode"] in {"upgrade", "initial"}, "ACTIVATION_NOT_COMPLETE")
        state, shim, state_bytes = module._checked_completed_active(
            state_dir=state_dir, data_dir=data, bin_dir=bin_dir,
            legacy_guard_root=Path(deployment["managed_legacy_guard_root"]), require_effective_path=False,
        )
        _require(state["source_ref"] == NEW_SOURCE_REF and state["artifact_id"] == NEW_ARTIFACT_ID, "ACTIVATION_STATE_PIN_MISMATCH")
        _require(state["release_path"] == str(Path(release).resolve()), "ACTIVATION_RELEASE_MISMATCH")
        _require(state["downstream_path"] == deployment["downstream_path"]
                 and state["downstream_sha256"] == deployment["downstream_sha256"], "ACTIVATION_DOWNSTREAM_MISMATCH")
        _require(state_bytes == (state_dir / "yc-transitional-guard.json").read_bytes()
                 and shim == (bin_dir / "yc.cmd").read_bytes(), "ACTIVATION_RACE")
        yield


def _cwd_identity() -> dict[str, str]:
    cwd = Path.cwd().resolve()
    try:
        stat = cwd.stat()
        object_identity = f"fs:{stat.st_dev}:{stat.st_ino}"
    except OSError:
        object_identity = "path:" + os.path.normcase(str(cwd))
    return {
        "lexical": str(cwd),
        "object_identity": object_identity,
        "follow_mode": "target",
    }


def build_fact(argv: list[str], deployment: dict[str, Any]) -> dict[str, Any]:
    downstream = Path(deployment["downstream_path"]).resolve()
    identity = "sha256:" + deployment["downstream_sha256"]
    return {
        "schema": "parsed-simple/v1",
        "platform": "windows",
        "parser": {"status": "exact", "profile": "yc-managed-entry-v1"},
        "executable": {
            "invoked": "yc",
            "resolved_path": str(downstream),
            "object_identity": identity,
        },
        "argv": ["yc", *argv],
        "cwd": _cwd_identity(),
        "targets": [],
        "redirects": [],
        "stdin": {"kind": "none"},
    }


def run_guard(
    argv: list[str],
    *,
    release: Path,
    executor: Callable[[str, list[str]], int] | None = None,
) -> int:
    artifact, deployment, _manifest = validate_runtime(release)
    try:
        with _activation_gate(release, deployment, _manifest):
            return _dispatch_guard(argv, artifact=artifact, deployment=deployment, executor=executor)
    except Exception as exc:
        print(f"YC guard: fail-closed activation ({type(exc).__name__}); команда не выполнялась.", file=sys.stderr)
        return FAIL_CLOSED_EXIT


def _dispatch_guard(argv: list[str], *, artifact: Path, deployment: dict[str, Any], executor: Callable[[str, list[str]], int] | None = None) -> int:
    runtime_dir = artifact / "runtime"
    sys.path.insert(0, str(runtime_dir))
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        from yc_transitional_adapter import dispatch
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if sys.path and sys.path[0] == str(runtime_dir):
            sys.path.pop(0)

    fact = build_fact(argv, deployment)
    trusted = {
        "invoked": "yc",
        "resolved_path": deployment["downstream_path"],
        "object_identity": "sha256:" + deployment["downstream_sha256"],
    }

    def execute(path: str, tail: list[str]) -> int:
        if executor is not None:
            return int(executor(path, tail))
        completed = subprocess.run([path, *tail], check=False, shell=False)
        return int(completed.returncode)

    outcome = dispatch(
        fact,
        trusted_execution_target=trusted,
        executor=execute,
        caller_context=None,
    )
    if not outcome["execute"]:
        reasons = ",".join(outcome.get("reason_codes") or [])
        print(
            f"YC guard: {outcome['decision']} ({reasons}); команда не выполнялась.",
            file=sys.stderr,
        )
        return DENY_EXIT if outcome["decision"] == "DENY" else ASK_EXIT
    return int(outcome["execution_result"])


def main(argv: list[str] | None = None) -> int:
    try:
        return run_guard(list(sys.argv[1:] if argv is None else argv), release=Path(__file__).resolve().parent)
    except Exception as exc:
        print(f"YC guard: fail-closed ({type(exc).__name__}); команда не выполнялась.", file=sys.stderr)
        return FAIL_CLOSED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
