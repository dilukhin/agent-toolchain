"""Read-only inventory of OpenCode agent definition sources.

The inventory intentionally reports only safe metadata. It never prints prompt or
description contents, permission patterns, inline config payloads, or secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from setup_lib import parse_jsonc_object

INVENTORY_SCHEMA = 1
MAX_PARSE_BYTES = 512 * 1024
_CONFIG_NAMES = ("opencode.json", "opencode.jsonc")
_SAFE_SCALARS = ("model", "mode")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_scalar(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 256:
        return None
    return value


def _agent_mapping(data: dict[str, Any]) -> dict[str, Any]:
    # OpenCode v1 uses "agent". Accept "agents" as read-only forward-compatible
    # inventory input, but do not claim which schema is active.
    for key in ("agent", "agents"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _summarize_agent_spec(name: str, spec: object) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name}
    if not isinstance(spec, dict):
        record["status"] = "non-object"
        return record
    record["status"] = "ok"
    for field in _SAFE_SCALARS:
        value = _safe_scalar(spec.get(field))
        if value is not None:
            record[field] = value
    if isinstance(spec.get("hidden"), bool):
        record["hidden"] = bool(spec["hidden"])
    record["description_present"] = "description" in spec
    record["prompt_present"] = "prompt" in spec or "system" in spec
    permission = spec.get("permission", spec.get("permissions"))
    record["permission_present"] = permission is not None
    if isinstance(permission, dict):
        record["permission_keys"] = sorted(str(key) for key in permission)
    elif isinstance(permission, list):
        record["permission_rule_count"] = len(permission)
    tools = spec.get("tools")
    record["tools_present"] = tools is not None
    if isinstance(tools, dict):
        record["tool_keys"] = sorted(str(key) for key in tools)
    record["field_keys"] = sorted(str(key) for key in spec)
    return record


def _summarize_config_payload(data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    parsed, error, _features = parse_jsonc_object(data)
    if error or parsed is None:
        return [], error or "config cannot be parsed"
    return [
        _summarize_agent_spec(str(name), spec)
        for name, spec in sorted(_agent_mapping(parsed).items(), key=lambda item: str(item[0]))
    ], None


def _config_source(path: Path, *, layer: str, precedence: int, source_id: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": source_id,
        "kind": "config",
        "layer": layer,
        "precedence": precedence,
        "path": str(path),
    }
    if path.is_symlink():
        record["status"] = "symlink-not-read"
        return record
    if not path.exists():
        record["status"] = "missing"
        return record
    if not path.is_file():
        record["status"] = "not-a-regular-file"
        return record
    try:
        size = path.stat().st_size
        record["size"] = size
        record["sha256"] = _sha256_file(path)
        if size > MAX_PARSE_BYTES:
            record["status"] = "too-large-to-parse"
            return record
        payload = path.read_bytes()
    except OSError as exc:
        record["status"] = "unreadable"
        record["error"] = str(exc)
        return record
    agents, parse_error = _summarize_config_payload(payload)
    record["status"] = "ok" if parse_error is None else "parse-error"
    if parse_error is not None:
        record["error"] = parse_error
    record["agents"] = agents
    return record


def _inline_source(raw: str, *, precedence: int) -> dict[str, Any]:
    payload = raw.encode("utf-8", errors="surrogatepass")
    record: dict[str, Any] = {
        "id": "inline-config",
        "kind": "inline-config",
        "layer": "inline",
        "precedence": precedence,
        "status": "present",
        "sha256": _sha256_bytes(payload),
        "bytes": len(payload),
    }
    if len(payload) > MAX_PARSE_BYTES:
        record["status"] = "too-large-to-parse"
        return record
    agents, parse_error = _summarize_config_payload(payload)
    if parse_error is not None:
        record["status"] = "parse-error"
        record["error"] = parse_error
    record["agents"] = agents
    return record


_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")


def _strip_yaml_scalar(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value in {"|", ">"}:
        return None
    if len(value) > 256 or value[0] in "[{":
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value or None


def _markdown_metadata(payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="replace")
    lines = text.splitlines()
    keys: dict[str, str | None] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                body_start = index + 1
                break
            if lines[index][:1].isspace():
                continue
            match = _FRONTMATTER_KEY_RE.match(lines[index])
            if match:
                keys[match.group(1)] = match.group(2)
    body_present = any(line.strip() for line in lines[body_start:]) if body_start else bool(text.strip())
    record: dict[str, Any] = {
        "frontmatter_keys": sorted(keys),
        "description_present": "description" in keys,
        "permission_present": "permission" in keys or "permissions" in keys,
        "tools_present": "tools" in keys,
        "prompt_present": body_present or "prompt" in keys or "system" in keys,
    }
    for field in _SAFE_SCALARS:
        value = _strip_yaml_scalar(keys.get(field))
        if value is not None:
            record[field] = value
    hidden = _strip_yaml_scalar(keys.get("hidden"))
    if hidden in {"true", "false"}:
        record["hidden"] = hidden == "true"
    return record


def _markdown_sources(directory: Path, *, layer: str, precedence: int, source_prefix: str) -> list[dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        return []
    records: list[dict[str, Any]] = []
    try:
        paths = sorted(directory.rglob("*.md"))
    except OSError:
        return []
    for path in paths:
        relative = path.relative_to(directory).as_posix()
        agent_id = relative[:-3]
        record: dict[str, Any] = {
            "id": f"{source_prefix}:{agent_id}",
            "kind": "agent-markdown",
            "layer": layer,
            "precedence": precedence,
            "path": str(path),
            "agent_id": agent_id,
        }
        if path.is_symlink():
            record["status"] = "symlink-not-read"
            records.append(record)
            continue
        try:
            size = path.stat().st_size
            record["size"] = size
            record["sha256"] = _sha256_file(path)
            if size > MAX_PARSE_BYTES:
                record["status"] = "too-large-to-parse"
                records.append(record)
                continue
            payload = path.read_bytes()
        except OSError as exc:
            record["status"] = "unreadable"
            record["error"] = str(exc)
            records.append(record)
            continue
        record["status"] = "ok"
        record.update(_markdown_metadata(payload))
        records.append(record)
    return records


def _project_chain(start: Path) -> tuple[list[Path], Path | None]:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    chain: list[Path] = []
    git_root: Path | None = None
    while True:
        chain.append(current)
        git_marker = current / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            git_root = current
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return chain, git_root


def _managed_config_dir(environ: Mapping[str, str]) -> Path:
    if os.name == "nt":
        root = environ.get("ProgramData") or environ.get("PROGRAMDATA")
        return (Path(root) if root else Path("C:/ProgramData")) / "opencode"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/opencode")
    return Path("/etc/opencode")


def collect_agent_inventory(
    *,
    project: Path,
    config_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    chain, git_root = _project_chain(project)
    sources: list[dict[str, Any]] = []

    sources.append({
        "id": "remote-config",
        "kind": "remote-config",
        "layer": "remote",
        "precedence": 10,
        "status": "not-inspected",
        "reason": "network/provider authentication is intentionally not used by this read-only inventory",
    })

    for index, name in enumerate(_CONFIG_NAMES):
        sources.append(_config_source(
            config_dir / name,
            layer="global",
            precedence=20,
            source_id=f"global-config:{index}",
        ))
    sources.extend(_markdown_sources(
        config_dir / "agents",
        layer="global-agents",
        precedence=25,
        source_prefix="global-agent",
    ))

    custom_config = env.get("OPENCODE_CONFIG")
    if custom_config:
        sources.append(_config_source(
            Path(custom_config).expanduser().resolve(),
            layer="custom-config",
            precedence=30,
            source_id="custom-config",
        ))
    else:
        sources.append({
            "id": "custom-config",
            "kind": "config",
            "layer": "custom-config",
            "precedence": 30,
            "status": "unset",
            "env": "OPENCODE_CONFIG",
        })

    # OpenCode searches from cwd upward to the nearest Git directory. Report
    # every candidate rather than pretending to resolve undocumented merge details.
    for depth, directory in enumerate(reversed(chain)):
        for name in _CONFIG_NAMES:
            path = directory / name
            if path.exists() or path.is_symlink():
                sources.append(_config_source(
                    path,
                    layer="project",
                    precedence=40 + depth,
                    source_id=f"project-config:{depth}:{name}",
                ))
        sources.extend(_markdown_sources(
            directory / ".opencode" / "agents",
            layer="project-agents",
            precedence=50 + depth,
            source_prefix=f"project-agent:{depth}",
        ))

    custom_dir = env.get("OPENCODE_CONFIG_DIR")
    if custom_dir:
        directory = Path(custom_dir).expanduser().resolve()
        sources.extend(_markdown_sources(
            directory / "agents",
            layer="custom-config-dir",
            precedence=60,
            source_prefix="custom-dir-agent",
        ))
        sources.append({
            "id": "custom-config-dir",
            "kind": "directory",
            "layer": "custom-config-dir",
            "precedence": 60,
            "path": str(directory),
            "status": "ok" if directory.is_dir() and not directory.is_symlink() else "missing-or-unsupported",
        })
    else:
        sources.append({
            "id": "custom-config-dir",
            "kind": "directory",
            "layer": "custom-config-dir",
            "precedence": 60,
            "status": "unset",
            "env": "OPENCODE_CONFIG_DIR",
        })

    inline = env.get("OPENCODE_CONFIG_CONTENT")
    if inline is not None:
        sources.append(_inline_source(inline, precedence=70))
    else:
        sources.append({
            "id": "inline-config",
            "kind": "inline-config",
            "layer": "inline",
            "precedence": 70,
            "status": "unset",
            "env": "OPENCODE_CONFIG_CONTENT",
        })

    managed_dir = _managed_config_dir(env)
    for index, name in enumerate(_CONFIG_NAMES):
        sources.append(_config_source(
            managed_dir / name,
            layer="system-managed",
            precedence=80,
            source_id=f"system-managed:{index}",
        ))

    agent_origins: dict[str, list[str]] = {}
    for source in sources:
        source_id = str(source.get("id"))
        for agent in source.get("agents", []):
            if isinstance(agent, dict) and isinstance(agent.get("name"), str):
                agent_origins.setdefault(agent["name"], []).append(source_id)
        agent_id = source.get("agent_id")
        if isinstance(agent_id, str):
            agent_origins.setdefault(agent_id, []).append(source_id)

    collisions = {
        name: origins
        for name, origins in sorted(agent_origins.items())
        if len(origins) > 1
    }

    return {
        "schema": INVENTORY_SCHEMA,
        "project": str(project.resolve()),
        "git_root": str(git_root) if git_root is not None else None,
        "network_used": False,
        "effective_runtime_queried": False,
        "sources": sorted(sources, key=lambda item: (int(item.get("precedence", 999)), str(item.get("id")))),
        "agent_origins": dict(sorted(agent_origins.items())),
        "collisions": collisions,
        "limitations": [
            "prompt and description contents are never emitted",
            "permission patterns and inline config contents are never emitted",
            "remote organizational config is not fetched",
            "this source inventory does not replace 'opencode agent list' effective-runtime evidence",
            "project/config precedence is reported from documented layers; ambiguous same-layer merge details are not guessed",
        ],
    }


def render_inventory(inventory: dict[str, Any]) -> str:
    lines = [
        "OpenCode agent source inventory (read-only)",
        f"project: {inventory.get('project')}",
        f"git root: {inventory.get('git_root') or 'not found'}",
        "network used: no",
        "effective runtime queried: no",
    ]
    for source in inventory.get("sources", []):
        if not isinstance(source, dict):
            continue
        status = source.get("status", "unknown")
        path = source.get("path")
        suffix = f"  {path}" if isinstance(path, str) else ""
        lines.append(
            f"[{int(source.get('precedence', 999)):02d}] {source.get('layer')} {source.get('kind')} "
            f"{source.get('id')}: {status}{suffix}"
        )
        for agent in source.get("agents", []):
            if not isinstance(agent, dict):
                continue
            fields = [
                f"agent={agent.get('name')}",
                f"model={agent.get('model', 'unset')}",
                f"mode={agent.get('mode', 'unset')}",
                f"description={'yes' if agent.get('description_present') else 'no'}",
                f"prompt={'yes' if agent.get('prompt_present') else 'no'}",
                f"permission={'yes' if agent.get('permission_present') else 'no'}",
                f"tools={'yes' if agent.get('tools_present') else 'no'}",
            ]
            lines.append("    " + " ".join(fields))
        if source.get("kind") == "agent-markdown":
            fields = [
                f"agent={source.get('agent_id')}",
                f"model={source.get('model', 'unset')}",
                f"mode={source.get('mode', 'unset')}",
                f"description={'yes' if source.get('description_present') else 'no'}",
                f"prompt={'yes' if source.get('prompt_present') else 'no'}",
                f"permission={'yes' if source.get('permission_present') else 'no'}",
                f"tools={'yes' if source.get('tools_present') else 'no'}",
            ]
            lines.append("    " + " ".join(fields))
    collisions = inventory.get("collisions")
    if isinstance(collisions, dict) and collisions:
        lines.append("name collisions / multiple definition sources:")
        for name, origins in collisions.items():
            lines.append(f"  - {name}: {', '.join(str(item) for item in origins)}")
    else:
        lines.append("name collisions / multiple definition sources: none observed")
    lines.append("limitations:")
    for item in inventory.get("limitations", []):
        lines.append(f"  - {item}")
    return "\n".join(lines) + "\n"


def add_cli_parser(subparsers) -> None:
    agents = subparsers.add_parser("agents", help="read-only OpenCode agent source diagnostics")
    agent_sub = agents.add_subparsers(dest="agents_command", required=True)
    inspect = agent_sub.add_parser("inspect", help="inspect agent definition sources without mutation or network access")
    inspect.add_argument("--project", help="project/cwd to inspect; defaults to current directory")
    inspect.add_argument("--json", action="store_true", help="emit machine-readable safe metadata")


def run_cli(args, *, config_dir: Path) -> int:
    if args.agents_command != "inspect":
        return 2
    project = Path(args.project).expanduser() if args.project else Path.cwd()
    try:
        inventory = collect_agent_inventory(project=project, config_dir=config_dir)
    except OSError as exc:
        print(f"agent inventory failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_inventory(inventory))
    return 0
