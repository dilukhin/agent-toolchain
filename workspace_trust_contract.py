#!/usr/bin/env python3
"""Pure WorkspaceTrustFact validation/matching.

This module does not establish authenticity and does not read trust state from disk,
environment, tool arguments, or model-controlled input. A future integration layer
must supply facts through a separately proven trusted provider.
"""
from __future__ import annotations

import copy
import ntpath
import posixpath
from typing import Any


SCHEMA = "workspace-trust-fact/v1"
TRUST_CLASS = "development"
SUPPORTED_PLATFORMS = {"linux", "windows"}
SUPPORTED_SCOPES = {"build", "test", "static_check", "git_read"}
FACT_KEYS = {"schema", "trust_class", "workspace", "scopes"}
WORKSPACE_KEYS = {"platform", "requested_root", "resolved_root", "object_identity"}


class WorkspaceTrustError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_absolute(platform: str, value: str) -> bool:
    if platform == "linux":
        return posixpath.isabs(value)
    if platform == "windows":
        return ntpath.isabs(value)
    return False


def validate_workspace_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != WORKSPACE_KEYS:
        raise WorkspaceTrustError("workspace.invalid_shape")

    platform = value.get("platform")
    requested = value.get("requested_root")
    resolved = value.get("resolved_root")
    object_identity = value.get("object_identity")

    if platform not in SUPPORTED_PLATFORMS:
        raise WorkspaceTrustError("workspace.platform_unsupported")
    if not _nonempty_string(requested) or not _is_absolute(platform, requested):
        raise WorkspaceTrustError("workspace.requested_root_invalid")
    if not _nonempty_string(resolved) or not _is_absolute(platform, resolved):
        raise WorkspaceTrustError("workspace.resolved_root_invalid")
    if not _nonempty_string(object_identity):
        raise WorkspaceTrustError("workspace.object_identity_missing")

    return {
        "platform": platform,
        "requested_root": requested,
        "resolved_root": resolved,
        "object_identity": object_identity,
    }


def validate_workspace_trust_fact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FACT_KEYS:
        raise WorkspaceTrustError("fact.invalid_shape")
    if value.get("schema") != SCHEMA:
        raise WorkspaceTrustError("fact.schema_unsupported")
    if value.get("trust_class") != TRUST_CLASS:
        raise WorkspaceTrustError("fact.trust_class_unsupported")

    workspace = validate_workspace_identity(value.get("workspace"))
    scopes = value.get("scopes")
    if not isinstance(scopes, list) or not scopes or not all(_nonempty_string(item) for item in scopes):
        raise WorkspaceTrustError("fact.scopes_invalid")
    if len(scopes) != len(set(scopes)):
        raise WorkspaceTrustError("fact.scopes_duplicate")
    unknown = sorted(set(scopes) - SUPPORTED_SCOPES)
    if unknown:
        raise WorkspaceTrustError("fact.scope_unsupported")

    return {
        "schema": SCHEMA,
        "trust_class": TRUST_CLASS,
        "workspace": copy.deepcopy(workspace),
        "scopes": sorted(scopes),
    }


def match_workspace_trust_fact(value: Any, observed_workspace: Any) -> dict[str, Any]:
    """Fail-closed exact match. Validation never proves provider authenticity."""
    try:
        fact = validate_workspace_trust_fact(value)
        observed = validate_workspace_identity(observed_workspace)
    except WorkspaceTrustError as exc:
        return {"matched": False, "scopes": [], "reason": exc.code}

    for key in ("platform", "requested_root", "resolved_root", "object_identity"):
        if fact["workspace"][key] != observed[key]:
            return {"matched": False, "scopes": [], "reason": f"workspace.{key}_mismatch"}

    return {
        "matched": True,
        "scopes": list(fact["scopes"]),
        "reason": "workspace.exact_match",
    }
