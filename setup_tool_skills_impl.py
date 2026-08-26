"""Pinned skill reconciliation for managed tool repositories."""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from setup_lib import (
    Reporter,
    STATE_CONFIGURED,
    STATE_CONFLICT,
    STATE_FAILED,
    STATE_MISSING,
    STATE_OK,
    atomic_write,
    reconcile_file,
    run,
    sha256_bytes,
    validate_skill,
)
from setup_managed_tools import data_root
from setup_tools import ToolSpec

_MARKER = ".agent-toolchain-managed-skills.json"


def _remove_owned_tree(path: Path) -> None:
    """Remove a tree created by the current run, including read-only Git files on Windows."""
    def _onerror(function, name, exc_info):
        try:
            os.chmod(name, stat.S_IWRITE)
            function(name)
        except OSError:
            raise exc_info[1]

    shutil.rmtree(path, onerror=_onerror)


def _relative_skill_path(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or path.name != "SKILL.md":
        return None
    return path.as_posix()


def tool_skill_bindings(env_cfg: dict[str, Any], specs: dict[str, ToolSpec]) -> tuple[dict[str, dict[str, str]], str | None]:
    """Bind legacy dependency skill metadata to ToolSpecs by project_directory.

    Runtime version ownership remains in ToolSpec. Dependency metadata only supplies
    relative skill paths while source checkout tracking remains a separate concern.
    """
    dependencies = env_cfg.get("dependencies")
    if not isinstance(dependencies, dict):
        return {}, "managed_environment.dependencies must be an object"
    result: dict[str, dict[str, str]] = {}
    skill_owner: dict[str, str] = {}
    for tool_name, spec in specs.items():
        matches = [raw for raw in dependencies.values()
                   if isinstance(raw, dict) and raw.get("directory") == spec.project_directory]
        if len(matches) > 1:
            return {}, f"multiple dependency records match ToolSpec project_directory {spec.project_directory!r}"
        if not matches:
            result[tool_name] = {}
            continue
        raw = matches[0]
        bound: dict[str, str] = {}
        single = raw.get("skill")
        multiple = raw.get("skills")
        if single is not None:
            if not isinstance(single, str):
                return {}, f"dependency skill for {tool_name!r} must be a string"
            relative = _relative_skill_path(single)
            if relative is None:
                return {}, f"dependency skill path for {tool_name!r} is not a safe relative SKILL.md path"
            skill_name = PurePosixPath(relative).parent.name
            bound[skill_name] = relative
        if multiple is not None:
            if not isinstance(multiple, dict):
                return {}, f"dependency skills for {tool_name!r} must be an object"
            for skill_name, value in sorted(multiple.items()):
                if not isinstance(skill_name, str) or not skill_name or not isinstance(value, str):
                    return {}, f"dependency skills for {tool_name!r} contain an invalid entry"
                relative = _relative_skill_path(value)
                if relative is None:
                    return {}, f"dependency skill path for {skill_name!r} is not a safe relative SKILL.md path"
                bound[skill_name] = relative
        for skill_name in bound:
            previous = skill_owner.get(skill_name)
            if previous is not None:
                return {}, f"skill {skill_name!r} is declared by both {previous!r} and {tool_name!r}"
            skill_owner[skill_name] = tool_name
        result[tool_name] = bound
    return result, None


def _bundle_dir(spec: ToolSpec) -> Path:
    assert spec.ref is not None
    return data_root() / "tools" / spec.name / "skill-releases" / spec.ref.lower()


def _marker_payload(
    spec: ToolSpec,
    bindings: dict[str, str],
    payload_sha256: dict[str, str],
) -> dict[str, object]:
    return {
        "schema": 2,
        "owner": "agent-toolchain",
        "tool": spec.name,
        "repo": spec.repo,
        "source_ref": spec.ref.lower() if spec.ref else None,
        "skills": bindings,
        "payload_sha256": payload_sha256,
    }


def _payload_hashes(bundle: Path, bindings: dict[str, str]) -> dict[str, str] | None:
    payload_root = bundle / "payload"
    if payload_root.is_symlink():
        return None
    hashes: dict[str, str] = {}
    for skill_name in bindings:
        skill_dir = payload_root / skill_name
        payload = skill_dir / "SKILL.md"
        if skill_dir.is_symlink() or payload.is_symlink() or not payload.is_file():
            return None
        try:
            hashes[skill_name] = sha256_bytes(payload.read_bytes())
        except OSError:
            return None
    return hashes


def _owned_bundle(bundle: Path, spec: ToolSpec, bindings: dict[str, str]) -> bool:
    if bundle.is_symlink() or not bundle.is_dir():
        return False
    try:
        data = json.loads((bundle / _MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    stored_hashes = data.get("payload_sha256")
    if not isinstance(stored_hashes, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in stored_hashes.items()
    ):
        return False
    actual_hashes = _payload_hashes(bundle, bindings)
    if actual_hashes is None or actual_hashes != stored_hashes:
        return False
    return data == _marker_payload(spec, bindings, actual_hashes)


def _payload_path(bundle: Path, skill_name: str) -> Path:
    return bundle / "payload" / skill_name / "SKILL.md"


def _checkout_exact_ref(spec: ToolSpec, source: Path) -> tuple[bool, str]:
    assert spec.repo is not None and spec.ref is not None
    commands = (
        ["git", "init", "--quiet", str(source)],
        ["git", "-C", str(source), "remote", "add", "origin", spec.repo],
        ["git", "-C", str(source), "fetch", "--quiet", "--depth=1", "origin", spec.ref],
        ["git", "-C", str(source), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
    )
    for command in commands:
        cp = run(command)
        if cp.returncode != 0:
            tail = (cp.stderr or cp.stdout).strip()[-400:]
            return False, tail or f"command failed with exit {cp.returncode}"
    verify = run(["git", "-C", str(source), "rev-parse", "HEAD"])
    resolved = verify.stdout.strip().lower()
    if verify.returncode != 0 or resolved != spec.ref.lower():
        return False, f"checked out {resolved or 'unknown'} instead of pinned ref {spec.ref.lower()}"
    return True, resolved


def _publish_bundle(spec: ToolSpec, bindings: dict[str, str], reporter: Reporter) -> Path | None:
    bundle = _bundle_dir(spec)
    if bundle.exists() or bundle.is_symlink():
        if _owned_bundle(bundle, spec, bindings):
            return bundle
        reporter.add(f"{spec.name} skill source", STATE_CONFLICT,
                     f"skill bundle path exists but ownership is not proven: {bundle}")
        return None
    if not shutil.which("git"):
        reporter.add(f"{spec.name} skill source", STATE_FAILED,
                     "Git is required to obtain pinned skill sources")
        return None

    parent = bundle.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = Path(tempfile.mkdtemp(prefix=f".{spec.ref}.tmp-", dir=str(parent)))
    try:
        source = temporary / "source"
        ok, detail = _checkout_exact_ref(spec, source)
        if not ok:
            reporter.add(f"{spec.name} skill source", STATE_FAILED,
                         f"failed to obtain pinned ref {spec.ref[:12]} from {spec.repo}: {detail}")
            return None
        source_root = source.resolve()
        for skill_name, relative in bindings.items():
            candidate = (source / relative).resolve()
            if candidate != source_root and source_root not in candidate.parents:
                reporter.add(f"skill {skill_name}", STATE_FAILED,
                             f"pinned source path escapes repository root: {relative}")
                return None
            valid, validation = validate_skill(candidate, skill_name)
            if not valid:
                reporter.add(f"skill {skill_name}", STATE_FAILED,
                             f"pinned source {spec.ref[:12]} is invalid: {validation}")
                return None
            atomic_write(_payload_path(temporary, skill_name), candidate.read_bytes())
        try:
            _remove_owned_tree(source)
        except OSError as exc:
            reporter.add(f"{spec.name} skill source", STATE_FAILED,
                         f"failed to remove temporary pinned checkout: {exc}")
            return None
        payload_hashes = _payload_hashes(temporary, bindings)
        if payload_hashes is None:
            reporter.add(f"{spec.name} skill source", STATE_FAILED,
                         "validated skill payload could not be hashed before publication")
            return None
        marker = json.dumps(
            _marker_payload(spec, bindings, payload_hashes),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        atomic_write(temporary / _MARKER, marker.encode("utf-8"))
        if not _owned_bundle(temporary, spec, bindings):
            reporter.add(f"{spec.name} skill source", STATE_FAILED,
                         "staged skill bundle failed ownership/integrity validation")
            return None
        if bundle.exists() or bundle.is_symlink():
            reporter.add(f"{spec.name} skill source", STATE_FAILED,
                         f"skill bundle path appeared concurrently: {bundle}")
            return None
        os.replace(temporary, bundle)
        temporary = None
        reporter.add(f"{spec.name} skill source", STATE_CONFIGURED,
                     f"staged skills from exact ref {spec.ref[:12]}: {bundle}")
        return bundle
    finally:
        if temporary is not None and temporary.exists():
            try:
                _remove_owned_tree(temporary)
            except OSError:
                pass


def reconcile_pinned_tool_skills(
    env_cfg: dict[str, Any],
    specs: dict[str, ToolSpec],
    manifest: dict[str, Any],
    reporter: Reporter,
    *,
    skills_dir: Path,
    state_dir: Path,
    check: bool,
    force: bool,
    skip_install: bool,
) -> bool:
    bindings_by_tool, error = tool_skill_bindings(env_cfg, specs)
    if error:
        reporter.add("tool skill registry", STATE_CONFLICT, error)
        return False
    changed = False
    for tool_name, spec in sorted(specs.items()):
        bindings = bindings_by_tool.get(tool_name, {})
        if not bindings:
            continue
        if skip_install:
            for skill_name in bindings:
                reporter.add(f"skill {skill_name}", STATE_MISSING,
                             "pinned skill reconciliation skipped with dependency installation")
            continue
        bundle = _bundle_dir(spec)
        if (bundle.exists() or bundle.is_symlink()) and not _owned_bundle(bundle, spec, bindings):
            reporter.add(f"{spec.name} skill source", STATE_CONFLICT,
                         f"skill bundle path exists but ownership or payload integrity is not proven: {bundle}")
            continue
        if not bundle.exists():
            if check:
                for skill_name, relative in bindings.items():
                    reporter.add(
                        f"skill {skill_name}",
                        STATE_MISSING,
                        f"toolchainctl apply will fetch {relative} from pinned ref {spec.ref[:12]} of {spec.repo}",
                    )
                continue
            bundle = _publish_bundle(spec, bindings, reporter)
            if bundle is None:
                continue
            changed = True
        else:
            reporter.add(f"{spec.name} skill source", STATE_OK,
                         f"pinned ref {spec.ref[:12]} with verified payload hashes: {bundle}")

        for skill_name, relative in bindings.items():
            payload = _payload_path(bundle, skill_name)
            valid, validation = validate_skill(payload, skill_name)
            if not valid:
                reporter.add(f"skill {skill_name}", STATE_CONFLICT,
                             f"owned pinned payload is invalid: {validation}")
                continue
            changed |= reconcile_file(
                component=f"skill {skill_name}",
                destination=skills_dir / skill_name / "SKILL.md",
                source_data=payload.read_bytes(),
                source_label=f"tool:{spec.name}@{spec.ref}:{relative}",
                manifest=manifest,
                reporter=reporter,
                check=check,
                force=force,
                state_dir=state_dir,
            )
    return changed
