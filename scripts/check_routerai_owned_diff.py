#!/usr/bin/env python3
"""Reject unreproducible ordinary-PR changes to RouterAI automation-owned sections."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_BRANCH = "automation/routerai-catalog"
DOC = "docs/routerai_refresh_status_design_ru.md"
OFFLINE_SYNC_COMMAND = "python3 scripts/update_routerai_catalog.py --sync-generated"
MANUAL_REFRESH_COMMAND = (
    "gh workflow run routerai_catalog.yml --repo dilukhin/agent-toolchain --ref main"
)

CATALOG_SCRIPT = ROOT / "scripts" / "update_routerai_catalog.py"
SPEC = importlib.util.spec_from_file_location("update_routerai_catalog_for_guard", CATALOG_SCRIPT)
if not SPEC or not SPEC.loader:
    raise RuntimeError(f"cannot load RouterAI generator: {CATALOG_SCRIPT}")
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


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


def _template_models(template: dict[str, Any]) -> Any:
    try:
        models = template["provider"]["routerai"]["models"]
    except (KeyError, TypeError) as exc:
        raise GuardError("templates/opencode.jsonc: отсутствует provider.routerai.models") from exc
    if not isinstance(models, dict):
        raise GuardError("templates/opencode.jsonc: provider.routerai.models не является object")
    return models


def owned_section_violations(
    *,
    base_policy: dict[str, Any],
    head_policy: dict[str, Any],
    base_config: dict[str, Any],
    head_config: dict[str, Any],
    base_template: dict[str, Any],
    head_template: dict[str, Any],
    base_snapshot: dict[str, Any],
    head_snapshot: dict[str, Any],
) -> list[str]:
    violations: list[str] = []

    # The external snapshot is owned exclusively by the catalog workflow. An ordinary
    # policy PR may regenerate derived config/template sections, but must not fabricate
    # or refresh objective RouterAI data.
    if base_snapshot != head_snapshot:
        violations.append("templates/routerai_catalog.generated.json -> весь файл")
        return violations

    try:
        generated_violations = catalog.verify_generated_state(
            head_policy,
            head_snapshot,
            head_config,
            head_template,
        )
    except catalog.CatalogError as exc:
        raise GuardError(f"не удалось проверить воспроизводимость RouterAI: {exc}") from exc
    violations.extend(generated_violations)

    policy_changed = base_policy != head_policy
    generated_changed = (
        base_config.get("models") != head_config.get("models")
        or base_config.get("_managed_notice") != head_config.get("_managed_notice")
        or _template_models(base_template) != _template_models(head_template)
    )
    if generated_changed and not policy_changed:
        violations.append(
            "производные области изменены без изменения templates/routerai_model_policy.json"
        )
    return list(dict.fromkeys(violations))


def check_refs(base_ref: str, head_ref: str, head_branch: str) -> tuple[list[str], bool]:
    if head_branch == AUTOMATION_BRANCH:
        return [], False

    base_policy_bytes = _git_show(base_ref, "templates/routerai_model_policy.json")
    head_policy_bytes = _git_show(head_ref, "templates/routerai_model_policy.json")
    base_policy = _json_object(base_policy_bytes, "base RouterAI policy")
    head_policy = _json_object(head_policy_bytes, "head RouterAI policy")
    base_config = _json_object(_git_show(base_ref, "config_data.json"), "base config_data.json")
    head_config = _json_object(_git_show(head_ref, "config_data.json"), "head config_data.json")
    base_template = _json_object(
        _git_show(base_ref, "templates/opencode.jsonc"),
        "base templates/opencode.jsonc",
    )
    head_template = _json_object(
        _git_show(head_ref, "templates/opencode.jsonc"),
        "head templates/opencode.jsonc",
    )
    base_snapshot = _json_object(
        _git_show(base_ref, "templates/routerai_catalog.generated.json"),
        "base RouterAI snapshot",
    )
    head_snapshot = _json_object(
        _git_show(head_ref, "templates/routerai_catalog.generated.json"),
        "head RouterAI snapshot",
    )
    violations = owned_section_violations(
        base_policy=base_policy,
        head_policy=head_policy,
        base_config=base_config,
        head_config=head_config,
        base_template=base_template,
        head_template=head_template,
        base_snapshot=base_snapshot,
        head_snapshot=head_snapshot,
    )
    return violations, base_policy_bytes != head_policy_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Проверить ownership RouterAI: обычный PR может менять ручную policy и только "
            "точно воспроизводимые из неё производные области; внешний snapshot менять нельзя."
        )
    )
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-branch", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        violations, policy_changed = check_refs(args.base_ref, args.head_ref, args.head_branch)
    except GuardError as exc:
        print(f"ОШИБКА проверки владения RouterAI: {exc}", file=sys.stderr)
        return 2
    if not violations:
        return 0

    print("ОШИБКА: нарушен контракт владения производными данными RouterAI.", file=sys.stderr)
    for item in violations:
        print(f"  - {item}", file=sys.stderr)
    if policy_changed:
        print(
            "Ручная policy изменена. Не правьте производные области вручную; пересоберите их "
            f"штатным офлайновым генератором:\n  {OFFLINE_SYNC_COMMAND}",
            file=sys.stderr,
        )
    else:
        print(
            "Производные области нельзя изменять напрямую. Смысловые изменения вносите в "
            "templates/routerai_model_policy.json.",
            file=sys.stderr,
        )
    print(
        f"Полное обновление внешнего каталога:\n  {MANUAL_REFRESH_COMMAND}\n"
        f"Подробности и причины ограничения: {DOC}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
