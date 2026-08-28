#!/usr/bin/env python3
"""Reject ordinary PR changes to RouterAI automation-owned repository sections."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_lib import parse_jsonc_object  # noqa: E402

AUTOMATION_BRANCH = "automation/routerai-catalog"
DOC = "docs/routerai_refresh_status_design_ru.md"
MANUAL_REFRESH_COMMAND = (
    "gh workflow run routerai_catalog.yml --repo dilukhin/agent-toolchain --ref main"
)


class GuardError(RuntimeError):
    pass


def _git_show(ref: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GuardError(f"не удалось прочитать {path} из {ref}: {detail}")
    return completed.stdout


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"{label}: некорректный JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardError(f"{label}: ожидается JSON object")
    return value


def _jsonc_object(data: bytes, label: str) -> dict[str, Any]:
    value, error, _features = parse_jsonc_object(data)
    if error or value is None:
        raise GuardError(f"{label}: некорректный JSONC: {error or 'unknown parse error'}")
    return value


def owned_section_violations(
    *,
    base_config: dict[str, Any],
    head_config: dict[str, Any],
    base_template: dict[str, Any],
    head_template: dict[str, Any],
    base_snapshot: bytes,
    head_snapshot: bytes,
) -> list[str]:
    violations: list[str] = []
    if base_config.get("models") != head_config.get("models"):
        violations.append("config_data.json -> models")
    try:
        base_models = base_template["provider"]["routerai"]["models"]
        head_models = head_template["provider"]["routerai"]["models"]
    except (KeyError, TypeError) as exc:
        raise GuardError("templates/opencode.jsonc: отсутствует provider.routerai.models") from exc
    if base_models != head_models:
        violations.append("templates/opencode.jsonc -> provider.routerai.models")
    if base_snapshot != head_snapshot:
        violations.append("templates/routerai_catalog.generated.json -> весь файл")
    return violations


def check_refs(base_ref: str, head_ref: str, head_branch: str) -> list[str]:
    if head_branch == AUTOMATION_BRANCH:
        return []
    base_config = _json_object(_git_show(base_ref, "config_data.json"), "base config_data.json")
    head_config = _json_object(_git_show(head_ref, "config_data.json"), "head config_data.json")
    base_template = _jsonc_object(
        _git_show(base_ref, "templates/opencode.jsonc"),
        "base templates/opencode.jsonc",
    )
    head_template = _jsonc_object(
        _git_show(head_ref, "templates/opencode.jsonc"),
        "head templates/opencode.jsonc",
    )
    return owned_section_violations(
        base_config=base_config,
        head_config=head_config,
        base_template=base_template,
        head_template=head_template,
        base_snapshot=_git_show(base_ref, "templates/routerai_catalog.generated.json"),
        head_snapshot=_git_show(head_ref, "templates/routerai_catalog.generated.json"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверить, что обычный PR не меняет области, принадлежащие автоматизации RouterAI."
    )
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-branch", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        violations = check_refs(args.base_ref, args.head_ref, args.head_branch)
    except GuardError as exc:
        print(f"ОШИБКА проверки владения RouterAI: {exc}", file=sys.stderr)
        return 2
    if not violations:
        return 0

    print(
        "ОШИБКА: обычный PR изменяет область, принадлежащую автоматизации RouterAI.",
        file=sys.stderr,
    )
    for item in violations:
        print(f"  - {item}", file=sys.stderr)
    print(
        "Эти области не редактируются вручную. Ручную политику меняйте в "
        "templates/routerai_model_policy.json.\n"
        f"Штатный ручной запуск обновления:\n  {MANUAL_REFRESH_COMMAND}\n"
        f"Подробности и причины ограничения: {DOC}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
