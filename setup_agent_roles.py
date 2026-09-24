"""Managed OpenCode agent-role resources and semantic model ownership."""
from __future__ import annotations

import hashlib
import json
import re
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
    atomic_write,
    backup_file,
    reconcile_file,
    sha256_bytes,
)

AGENT_MODEL_MODE = "agent-frontmatter-model-v1"
_MANAGED_FIELDS = "managed_fields"
_MODEL_HASH_FIELD = "model"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:@/+\-]+$")

ADOPTABLE_MODEL_ROLES = (
    "luna",
    "luna-safe-worker",
    "sol-specialist",
    "astra-reviewer",
)

MANAGED_ROLE_TEMPLATES = (
    "docs-researcher",
    "code-reviewer",
    "evidence-auditor",
    "code-worker",
    "test-runner",
)


def model_component(role: str) -> str:
    return f"OpenCode agent model {role}"


def role_component(role: str) -> str:
    return f"OpenCode agent {role}"


def _model_hash(model: str) -> str:
    payload = json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def desired_role_models(config: dict[str, Any]) -> dict[str, str]:
    defaults = config.get("config_defaults")
    agents = defaults.get("agent_models") if isinstance(defaults, dict) else None
    if not isinstance(agents, dict):
        return {}
    result: dict[str, str] = {}
    for role in ADOPTABLE_MODEL_ROLES:
        value = agents.get(role)
        if isinstance(value, str) and _MODEL_ID_RE.fullmatch(value):
            result[role] = value
    return result


def _frontmatter_bounds(lines: list[str]) -> tuple[int, int] | None:
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return 0, index
    return None


def _parse_scalar(raw: str) -> str | None:
    value = raw.strip()
    if not value or value in {"|", ">"}:
        return None
    if "#" in value:
        # Comments make exact preservation ambiguous; fail closed instead.
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value if value else None


def inspect_frontmatter_model(data: bytes) -> tuple[str | None, str | None]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "agent file is not UTF-8"
    lines = text.splitlines(keepends=True)
    bounds = _frontmatter_bounds(lines)
    if bounds is None:
        return None, "YAML front matter is missing or not closed"
    _start, end = bounds
    matches: list[str] = []
    for line in lines[1:end]:
        if line.startswith((" ", "\t")):
            continue
        body = line.rstrip("\r\n")
        if not body.startswith("model:"):
            continue
        scalar = _parse_scalar(body.split(":", 1)[1])
        if scalar is None:
            return None, "top-level model uses unsupported YAML syntax"
        matches.append(scalar)
    if len(matches) != 1:
        return None, f"expected exactly one top-level model field, found {len(matches)}"
    model = matches[0]
    if _MODEL_ID_RE.fullmatch(model) is None:
        return None, "top-level model is not a supported provider/model id"
    return model, None


def _replace_frontmatter_model(data: bytes, desired_model: str) -> tuple[bytes | None, str | None]:
    if _MODEL_ID_RE.fullmatch(desired_model) is None:
        return None, "desired model is not a supported provider/model id"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "agent file is not UTF-8"
    lines = text.splitlines(keepends=True)
    bounds = _frontmatter_bounds(lines)
    if bounds is None:
        return None, "YAML front matter is missing or not closed"
    _start, end = bounds
    matches: list[int] = []
    for index in range(1, end):
        line = lines[index]
        if line.startswith((" ", "\t")):
            continue
        body = line.rstrip("\r\n")
        if body.startswith("model:"):
            scalar = _parse_scalar(body.split(":", 1)[1])
            if scalar is None:
                return None, "top-level model uses unsupported YAML syntax"
            matches.append(index)
    if len(matches) != 1:
        return None, f"expected exactly one top-level model field, found {len(matches)}"
    index = matches[0]
    old = lines[index]
    newline = "\r\n" if old.endswith("\r\n") else ("\n" if old.endswith("\n") else "")
    lines[index] = f"model: {desired_model}{newline}"
    return "".join(lines).encode("utf-8"), None


def _semantic_record(*, role: str, destination: Path, desired_model: str) -> dict[str, Any]:
    return {
        "path": str(destination),
        "source": f"agent-toolchain:agent-model-policy:{role}",
        "mode": AGENT_MODEL_MODE,
        "role": role,
        _MANAGED_FIELDS: {_MODEL_HASH_FIELD: _model_hash(desired_model)},
    }


def adopt_agent_model(
    *,
    role: str,
    destination: Path,
    desired_model: str,
    expected_current_sha: str,
    manifest: dict[str, Any],
    reporter: Reporter,
    state_dir: Path,
) -> bool:
    component = model_component(role)
    if role not in ADOPTABLE_MODEL_ROLES:
        reporter.add(component, STATE_CONFLICT, f"role is not eligible for model-only adoption: {role}")
        return False
    if _SHA256_RE.fullmatch(expected_current_sha) is None:
        reporter.add(component, STATE_CONFLICT, "expected current sha256 must be exactly 64 lowercase hex characters")
        return False
    managed = manifest.get("managed_files")
    if not isinstance(managed, dict):
        reporter.add(component, STATE_CONFLICT, "manifest managed_files is not an object")
        return False
    previous = managed.get(component)
    if previous is not None:
        reporter.add(component, STATE_CONFLICT, "agent model is already managed; use ordinary check/apply")
        return False
    if destination.is_symlink() or not destination.is_file():
        reporter.add(component, STATE_CONFLICT, f"agent definition is not a regular non-symlink file: {destination}")
        return False
    try:
        current_data = destination.read_bytes()
    except OSError as exc:
        reporter.add(component, STATE_FAILED, f"cannot read agent definition: {exc}")
        return False
    current_sha = sha256_bytes(current_data)
    if current_sha != expected_current_sha:
        reporter.add(
            component,
            STATE_CONFLICT,
            f"explicit adoption hash mismatch: expected_sha256={expected_current_sha}; current_sha256={current_sha}; file preserved",
        )
        return False
    current_model, error = inspect_frontmatter_model(current_data)
    if error or current_model is None:
        reporter.add(component, STATE_CONFLICT, error or "cannot inspect model")
        return False
    updated_data, patch_error = _replace_frontmatter_model(current_data, desired_model)
    if patch_error or updated_data is None:
        reporter.add(component, STATE_CONFLICT, patch_error or "cannot build agent model target")
        return False

    try:
        backup = backup_file(destination, state_dir, component)
        if updated_data != current_data:
            atomic_write(destination, updated_data)
        managed[component] = _semantic_record(
            role=role,
            destination=destination,
            desired_model=desired_model,
        )
    except OSError as exc:
        reporter.add(component, STATE_FAILED, f"model adoption failed while persisting agent definition: {exc}")
        return False

    detail = (
        f"model-only ownership adopted from exact sha256={current_sha}; "
        f"prompt/description/permissions preserved; backup: {backup}"
    )
    if current_model != desired_model:
        detail += f"; model updated {current_model} -> {desired_model}"
    else:
        detail += "; model already matched target"
    reporter.add(component, STATE_CONFIGURED, detail)
    return True


def reconcile_adopted_agent_models(
    *,
    config_dir: Path,
    desired_models: dict[str, str],
    manifest: dict[str, Any],
    reporter: Reporter,
    check: bool,
    force: bool,
    state_dir: Path,
) -> bool:
    managed = manifest.get("managed_files")
    if not isinstance(managed, dict):
        reporter.add("OpenCode agent models", STATE_CONFLICT, "manifest managed_files is not an object")
        return False
    changed = False
    for component, record in list(managed.items()):
        if not isinstance(record, dict) or record.get("mode") != AGENT_MODEL_MODE:
            continue
        role = record.get("role")
        if not isinstance(role, str) or role not in ADOPTABLE_MODEL_ROLES:
            reporter.add(str(component), STATE_CONFLICT, f"invalid managed agent model role: {role!r}")
            continue
        desired_model = desired_models.get(role)
        if desired_model is None:
            reporter.add(str(component), STATE_CONFLICT, f"current policy has no model target for {role}")
            continue
        destination = config_dir / "agents" / f"{role}.md"
        if record.get("path") != str(destination):
            reporter.add(str(component), STATE_CONFLICT, "manifest points to a different agent definition path")
            continue
        if destination.is_symlink() or not destination.is_file():
            reporter.add(str(component), STATE_CONFLICT, f"managed agent definition is missing or not regular: {destination}")
            continue
        try:
            current_data = destination.read_bytes()
        except OSError as exc:
            reporter.add(str(component), STATE_FAILED, f"cannot read managed agent definition: {exc}")
            continue
        current_model, error = inspect_frontmatter_model(current_data)
        if error or current_model is None:
            reporter.add(str(component), STATE_CONFLICT, error or "cannot inspect managed model")
            continue
        fields = record.get(_MANAGED_FIELDS)
        expected_hash = fields.get(_MODEL_HASH_FIELD) if isinstance(fields, dict) else None
        if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
            reporter.add(str(component), STATE_CONFLICT, "managed model evidence is missing or invalid")
            continue
        drift = _model_hash(current_model) != expected_hash
        if drift and not force:
            reporter.add(
                str(component),
                STATE_CONFLICT,
                f"managed model was changed to {current_model}; review before explicit --force repair",
            )
            continue
        if current_model == desired_model and not drift:
            reporter.add(str(component), STATE_OK, f"{destination}; model={desired_model}; other fields user-owned")
            continue
        if check:
            if drift:
                reporter.add(str(component), STATE_CONFLICT, f"--force would restore model={desired_model}; other fields preserved")
            else:
                reporter.add(str(component), STATE_OUTDATED, f"ordinary apply will update model {current_model} -> {desired_model}; other fields preserved")
            continue
        updated_data, patch_error = _replace_frontmatter_model(current_data, desired_model)
        if patch_error or updated_data is None:
            reporter.add(str(component), STATE_CONFLICT, patch_error or "cannot build model repair target")
            continue
        try:
            backup = backup_file(destination, state_dir, str(component))
            atomic_write(destination, updated_data)
            managed[str(component)] = _semantic_record(
                role=role,
                destination=destination,
                desired_model=desired_model,
            )
        except OSError as exc:
            reporter.add(str(component), STATE_FAILED, f"cannot update managed model: {exc}")
            continue
        changed = True
        reporter.add(
            str(component),
            STATE_CONFIGURED,
            f"model updated {current_model} -> {desired_model}; prompt/description/permissions preserved; backup: {backup}",
        )
    return changed


def reconcile_managed_role_templates(
    *,
    repo_root: Path,
    config_dir: Path,
    manifest: dict[str, Any],
    reporter: Reporter,
    check: bool,
    force: bool,
    state_dir: Path,
) -> bool:
    changed = False
    agents_dir = config_dir / "agents"
    if agents_dir.exists() and agents_dir.is_symlink():
        reporter.add("OpenCode managed agents", STATE_CONFLICT, f"agents directory is a symlink: {agents_dir}")
        return False
    for role in MANAGED_ROLE_TEMPLATES:
        component = role_component(role)
        destination = agents_dir / f"{role}.md"
        source = repo_root / "templates" / "agents" / f"{role}.md"
        if not source.is_file():
            reporter.add(component, STATE_CONFLICT, f"managed role template is missing: {source}")
            continue
        previous = manifest.get("managed_files", {}).get(component)
        if destination.exists() and previous is None:
            reporter.add(component, STATE_CONFLICT, f"existing unowned agent definition is preserved: {destination}")
            continue
        changed |= reconcile_file(
            component=component,
            destination=destination,
            source_data=source.read_bytes(),
            source_label=f"agent-toolchain:templates/agents/{role}.md",
            manifest=manifest,
            reporter=reporter,
            check=check,
            force=force,
            state_dir=state_dir,
        )
    return changed
