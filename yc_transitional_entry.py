#!/usr/bin/env python3
"""Self-contained runtime entrypoint for the managed transitional YC guard."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable

ARTIFACT_FORMAT = "opencode-permissions-yc-transitional-artifact/v1"
ARTIFACT_OWNER = "dilukhin/opencode_permissions"
ARTIFACT_DOMAIN = b"opencode_permissions.yc_transitional_artifact.v1\n"
DEPLOYMENT_FILE = "deployment.json"
DENY_EXIT = 77
ASK_EXIT = 78
FAIL_CLOSED_EXIT = 79


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
