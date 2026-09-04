"""Declarative ToolSpec model, validation, and production-branch resolution."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

TOOL_SPEC_SCHEMA = 1
VALID_SOURCES = {"git", "builtin"}
VALID_UPDATE_POLICIES = {"latest", "pinned-tested", "follow-branch", "bundled-with-setup"}
VALID_RUNTIMES = {"python-venv", "python-builtin", "python", "go-binary", "binary", "external"}
VALID_PLATFORMS = {"windows", "linux"}
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class HealthCheckSpec:
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    source: str
    runtime: str
    update_policy: str
    entrypoints: tuple[str, ...]
    health_contract: tuple[HealthCheckSpec, ...]
    platforms: tuple[str, ...]
    repo: str | None = None
    ref: str | None = None
    project_directory: str | None = None
    module: str | None = None
    tracking_branch: str | None = None


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_string_list(value: Any, field: str, *, allow_empty: bool = False) -> tuple[tuple[str, ...] | None, str | None]:
    if not isinstance(value, list):
        return None, f"ToolSpec field {field!r} must be an array"
    if not allow_empty and not value:
        return None, f"ToolSpec field {field!r} must not be empty"
    if not all(_nonempty_string(item) for item in value):
        return None, f"ToolSpec field {field!r} must contain non-empty strings"
    normalized = tuple(item.strip() for item in value)
    if len(normalized) != len(set(normalized)):
        return None, f"ToolSpec field {field!r} contains duplicates"
    return normalized, None


def _parse_health(value: Any) -> tuple[tuple[HealthCheckSpec, ...] | None, str | None]:
    if not isinstance(value, list) or not value:
        return None, "ToolSpec field 'health_contract' must be a non-empty array"
    checks: list[HealthCheckSpec] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return None, f"health_contract[{index}] must be an object"
        argv, error = _parse_string_list(item.get("argv"), f"health_contract[{index}].argv")
        if error:
            return None, error
        assert argv is not None
        checks.append(HealthCheckSpec(argv=argv))
    return tuple(checks), None


def _valid_branch_name(value: str) -> bool:
    if not _BRANCH_RE.fullmatch(value):
        return False
    return (
        ".." not in value
        and not value.endswith(("/", ".", ".lock"))
        and not value.startswith("/")
        and "//" not in value
        and "@{" not in value
    )


def _github_repo_parts(repo: str) -> tuple[str, str] | None:
    parsed = urlsplit(repo)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not owner or not name:
        return None
    return owner, name


def _resolve_github_branch(repo: str, branch: str) -> str:
    if _github_repo_parts(repo) is None:
        raise ValueError("follow-branch currently requires an https://github.com/OWNER/REPO(.git) source")
    if not _valid_branch_name(branch):
        raise ValueError(f"invalid production branch name: {branch!r}")
    git = shutil.which("git")
    if not git:
        raise ValueError("git is required to resolve a git-sourced production branch")
    remote_ref = f"refs/heads/{branch}"
    try:
        completed = subprocess.run(
            [git, "ls-remote", "--refs", repo, remote_ref],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git ls-remote failed: {exc}") from exc
    if completed.returncode != 0:
        raise ValueError(f"git ls-remote failed with exit code {completed.returncode}")
    try:
        output = completed.stdout.decode("ascii").strip().splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("git ls-remote returned non-ASCII output") from exc
    if len(output) != 1:
        raise ValueError(f"production branch resolved to {len(output)} refs instead of exactly one")
    fields = output[0].split()
    if len(fields) != 2 or fields[1] != remote_ref:
        raise ValueError("git ls-remote returned an unexpected ref")
    sha = fields[0]
    if _SHA_RE.fullmatch(sha) is None:
        raise ValueError(f"git ls-remote returned an invalid commit SHA: {sha!r}")
    return sha.lower()


def _resolve_follow_branch(spec: ToolSpec) -> tuple[ToolSpec | None, str | None]:
    assert spec.repo is not None and spec.tracking_branch is not None
    try:
        sha = _resolve_github_branch(spec.repo, spec.tracking_branch)
    except ValueError as exc:
        return None, f"ToolSpec {spec.name!r}: cannot resolve production branch {spec.tracking_branch!r}: {exc}"
    # Product policy selects the moving production branch. The existing deployer then
    # consumes an exact immutable snapshot; this is execution identity, not a second
    # approval gate. Runtime and bound skills receive this same resolved SHA.
    return replace(spec, update_policy="pinned-tested", ref=sha), None


def parse_tool_spec(name: str, raw: Any) -> tuple[ToolSpec | None, str | None]:
    if not _nonempty_string(name):
        return None, "ToolSpec name must be a non-empty string"
    if not isinstance(raw, dict):
        return None, f"ToolSpec {name!r} must be an object"

    source = raw.get("source")
    runtime = raw.get("runtime")
    update_policy = raw.get("update_policy")
    if source not in VALID_SOURCES:
        return None, f"ToolSpec {name!r}: unsupported source {source!r}"
    if runtime not in VALID_RUNTIMES:
        return None, f"ToolSpec {name!r}: unsupported runtime {runtime!r}"
    if update_policy not in VALID_UPDATE_POLICIES:
        return None, f"ToolSpec {name!r}: unsupported update_policy {update_policy!r}"

    entrypoints, error = _parse_string_list(raw.get("entrypoints"), "entrypoints")
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    health, error = _parse_health(raw.get("health_contract"))
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    platforms, error = _parse_string_list(raw.get("platforms", ["windows", "linux"]), "platforms")
    if error:
        return None, f"ToolSpec {name!r}: {error}"
    assert entrypoints is not None and health is not None and platforms is not None
    if not set(platforms).issubset(VALID_PLATFORMS):
        unknown = sorted(set(platforms) - VALID_PLATFORMS)
        return None, f"ToolSpec {name!r}: unsupported platforms: {', '.join(unknown)}"

    repo = raw.get("repo")
    ref = raw.get("ref")
    branch = raw.get("branch")
    project_directory = raw.get("project_directory")
    module = raw.get("module")
    if source == "git" and not _nonempty_string(repo):
        return None, f"ToolSpec {name!r}: git source requires repo"
    if update_policy == "pinned-tested" and not _nonempty_string(ref):
        return None, f"ToolSpec {name!r}: pinned-tested requires an explicit ref"
    if update_policy == "follow-branch":
        if source != "git":
            return None, f"ToolSpec {name!r}: follow-branch requires git source"
        if not _nonempty_string(branch):
            return None, f"ToolSpec {name!r}: follow-branch requires an explicit branch"
        if ref is not None:
            return None, f"ToolSpec {name!r}: follow-branch must not define a fixed ref"
    elif branch is not None:
        return None, f"ToolSpec {name!r}: branch is only valid with update_policy='follow-branch'"
    if source == "git" and not _nonempty_string(project_directory):
        return None, f"ToolSpec {name!r}: git source requires project_directory"
    if source == "builtin" and any(value is not None for value in (repo, ref, branch, project_directory)):
        return None, f"ToolSpec {name!r}: builtin source must not define repo/ref/branch/project_directory"
    if module is not None and not _nonempty_string(module):
        return None, f"ToolSpec {name!r}: module must be a non-empty string"
    if source == "builtin" and runtime == "python-builtin" and not _nonempty_string(module):
        return None, f"ToolSpec {name!r}: python-builtin source requires module"
    if source != "builtin" and module is not None:
        return None, f"ToolSpec {name!r}: module is only valid for builtin source"

    spec = ToolSpec(
        name=name,
        source=source,
        runtime=runtime,
        update_policy=update_policy,
        entrypoints=entrypoints,
        health_contract=health,
        platforms=platforms,
        repo=repo.strip() if _nonempty_string(repo) else None,
        ref=ref.strip() if _nonempty_string(ref) else None,
        project_directory=project_directory.strip() if _nonempty_string(project_directory) else None,
        module=module.strip() if _nonempty_string(module) else None,
        tracking_branch=branch.strip() if _nonempty_string(branch) else None,
    )
    if update_policy == "follow-branch":
        return _resolve_follow_branch(spec)
    return spec, None


def parse_tool_specs(managed_environment: Any) -> tuple[dict[str, ToolSpec], str | None]:
    if not isinstance(managed_environment, dict):
        return {}, "managed_environment must be an object"
    schema = managed_environment.get("tool_spec_schema")
    if schema != TOOL_SPEC_SCHEMA:
        return {}, f"unsupported ToolSpec schema: {schema!r}"
    raw_tools = managed_environment.get("tools")
    if not isinstance(raw_tools, dict):
        return {}, "managed_environment.tools must be an object"

    parsed: dict[str, ToolSpec] = {}
    entrypoint_owner: dict[str, str] = {}
    for name in sorted(raw_tools):
        spec, error = parse_tool_spec(name, raw_tools[name])
        if error:
            return {}, error
        assert spec is not None
        for entrypoint in spec.entrypoints:
            previous = entrypoint_owner.get(entrypoint)
            if previous is not None:
                return {}, f"entrypoint {entrypoint!r} is declared by both {previous!r} and {name!r}"
            entrypoint_owner[entrypoint] = name
        parsed[name] = spec
    return parsed, None
