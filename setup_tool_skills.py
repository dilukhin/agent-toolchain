"""Pinned tool-skill reconciliation exposed to toolchainctl.

Importing this module installs the explicit adapter that prevents the legacy core
from treating developer tracking checkouts as production dependencies.
"""
from setup_core_adapter import install as _install_core_adapter

_install_core_adapter()

from setup_tool_skills_impl import reconcile_pinned_tool_skills, tool_skill_bindings

__all__ = ["reconcile_pinned_tool_skills", "tool_skill_bindings"]
