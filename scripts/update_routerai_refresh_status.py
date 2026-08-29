#!/usr/bin/env python3
"""Build the owned RouterAI refresh status and human-readable PR summary."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1
OWNER = "agent-toolchain:routerai-status:v1"
DOC = "docs/routerai_refresh_status_design_ru.md"
STATUS_BRANCH = "automation/routerai-status"
CATALOG_BRANCH = "automation/routerai-catalog"
MANUAL_REFRESH_COMMAND = (
    "gh workflow run routerai_catalog.yml --repo dilukhin/agent-toolchain --ref main"
)
MANAGED_NOTICE = (
    "НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ. Этот файл и ветка automation/routerai-status "
    "полностью принадлежат автоматизации обновления RouterAI. Здесь хранится "
    "машинно-читаемое состояние последней попытки, последней успешной проверки, "
    "опубликованных данных и кандидата. "
    f"Подробности, решённые проблемы и схема повторного применения: {DOC}. "
    f"Штатный ручной запуск полного обновления: {MANUAL_REFRESH_COMMAND}"
)


class StatusError(RuntimeError):
    pass


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusError(f"{label}: не удалось прочитать JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise StatusError(f"{label}: ожидается JSON object")
    return value


def load_previous_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.is_file():
        raise StatusError(f"предыдущий status отсутствует: {path}")
    value = _read_object(path, "предыдущий RouterAI status")
    if value.get("schema") != SCHEMA:
        raise StatusError(
            f"неизвестная schema предыдущего status: {value.get('schema')!r}; отказ от усыновления"
        )
    if value.get("owner") != OWNER:
        raise StatusError("предыдущий status не содержит доказанного ownership; отказ от перезаписи")
    if value.get("_managed_notice") != MANAGED_NOTICE:
        raise StatusError("_managed_notice предыдущего status изменён; отказ от перезаписи")
    return value


def _snapshot_observed_at(path: Path | None) -> str | None:
    if path is None:
        return None
    value = _read_object(path, "RouterAI snapshot").get("observed_at")
    return value if isinstance(value, str) and value else None


def _bool_value(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _error_advice(code: str | None) -> str:
    return {
        "catalog-branch-sync": (
            "Проверьте конфликт automation/routerai-catalog с актуальным main, "
            "разрешите его осознанно и повторите штатное обновление."
        ),
        "routerai-refresh": (
            "Повторите штатное обновление. Если ошибка повторяется, проверьте "
            "доступность/ответ RouterAI и журнал GitHub Actions."
        ),
        "regressions": "Исправьте упавшие регрессионные проверки; не публикуйте кандидат вручную.",
        "candidate-publish": (
            "Проверьте automation/routerai-catalog и права workflow на запись, затем повторите обновление."
        ),
        "pull-request": "Проверьте открытый RouterAI PR и повторите штатное обновление.",
        "full-validation": (
            "Исправьте причину ошибки полного Windows/Linux validate и повторите штатное обновление."
        ),
    }.get(code, "Откройте журнал GitHub Actions и повторите обновление после устранения причины.")


def build_status(
    *, previous: dict[str, Any], attempt_at: str, trigger: str, attempt_status: str,
    phase: str, error_code: str | None, error_summary: str | None,
    source_check_at: str | None, catalog_changed: bool | None,
    published_catalog_observed_at: str | None, published_main_sha: str,
    candidate_catalog_observed_at: str | None, candidate_sha: str | None,
    candidate_pr: int | None, candidate_validation: str,
) -> dict[str, Any]:
    last_successful = previous.get("last_successful_check")
    if not isinstance(last_successful, dict):
        last_successful = None
    if source_check_at:
        last_successful = {"at": source_check_at, "catalog_changed": catalog_changed}

    previous_candidate = previous.get("candidate")
    previous_candidate = previous_candidate if isinstance(previous_candidate, dict) else None
    candidate: dict[str, Any] | None = None
    if candidate_catalog_observed_at and candidate_sha and candidate_pr is not None:
        validation = candidate_validation
        if validation == "unchanged":
            validation = (
                previous_candidate.get("validation")
                if previous_candidate and previous_candidate.get("branch_sha") == candidate_sha
                else "pending"
            )
        if validation not in {"pending", "success", "failed"}:
            validation = "pending"
        candidate = {
            "catalog_observed_at": candidate_catalog_observed_at,
            "branch_sha": candidate_sha,
            "pr_number": candidate_pr,
            "validation": validation,
        }

    error: dict[str, Any] | None = None
    if attempt_status == "failed":
        error = {
            "code": error_code or "unknown",
            "summary": error_summary or "Неизвестная ошибка обновления RouterAI.",
            "advice": _error_advice(error_code),
        }

    return {
        "_managed_notice": MANAGED_NOTICE,
        "schema": SCHEMA,
        "owner": OWNER,
        "updated_at": attempt_at,
        "published": {
            "catalog_observed_at": published_catalog_observed_at,
            "main_sha": published_main_sha,
        },
        "last_successful_check": last_successful,
        "last_attempt": {
            "at": attempt_at,
            "trigger": trigger,
            "status": attempt_status,
            "phase": phase,
            "error": error,
        },
        "candidate": candidate,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "нет"
    if value is True:
        return "да"
    if value is False:
        return "нет"
    return str(value)


def pr_body(status: dict[str, Any]) -> str:
    published = status.get("published") if isinstance(status.get("published"), dict) else {}
    successful = status.get("last_successful_check") if isinstance(status.get("last_successful_check"), dict) else {}
    attempt = status.get("last_attempt") if isinstance(status.get("last_attempt"), dict) else {}
    candidate = status.get("candidate") if isinstance(status.get("candidate"), dict) else None
    error = attempt.get("error") if isinstance(attempt.get("error"), dict) else None
    lines = [
        "Автоматическое обновление из публичного каталога RouterAI `GET /api/v1/models`.",
        "",
        "Автоматика обновляет объективные данные и производные подписи. Ручная политика — "
        "`templates/routerai_model_policy.json`.",
        "",
        "## Состояние обновления RouterAI",
        "",
        f"- Опубликованный каталог в `main`: `{_fmt(published.get('catalog_observed_at'))}`",
        f"- Последняя успешная проверка RouterAI: `{_fmt(successful.get('at'))}`",
        f"- На последней успешной проверке каталог изменился: `{_fmt(successful.get('catalog_changed'))}`",
        f"- Последняя попытка: `{_fmt(attempt.get('at'))}`",
        f"- Результат последней попытки: `{_fmt(attempt.get('status'))}`",
        f"- Этап: `{_fmt(attempt.get('phase'))}`",
    ]
    if error:
        lines += [
            f"- Причина: {error.get('summary') or 'неизвестно'}",
            f"- Рекомендация: {error.get('advice') or 'см. GitHub Actions'}",
        ]
    if candidate:
        lines += [
            f"- Кандидат: `{candidate.get('catalog_observed_at') or 'неизвестно'}`",
            f"- SHA кандидата: `{candidate.get('branch_sha') or 'неизвестно'}`",
            f"- Полная Windows/Linux проверка: `{candidate.get('validation') or 'неизвестно'}`",
        ]
    else:
        lines.append("- Кандидат на публикацию: нет")
    lines += [
        "", "Штатный ручной запуск полного обновления:", "", "```text",
        MANUAL_REFRESH_COMMAND, "```", "",
        f"Подробности, цели, решённые проблемы и переносимый шаблон: `{DOC}`.", "",
        "Полный технический вывод остаётся в GitHub Actions; здесь публикуется только безопасная диагностика.",
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--published-snapshot", type=Path, required=True)
    parser.add_argument("--published-main-sha", required=True)
    parser.add_argument("--candidate-snapshot", type=Path)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--candidate-pr", type=int)
    parser.add_argument("--candidate-validation", choices=("pending", "success", "failed", "unchanged", "none"), default="none")
    parser.add_argument("--attempt-at", required=True)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--attempt-status", choices=("success", "failed"), required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--error-code")
    parser.add_argument("--error-summary")
    parser.add_argument("--source-check-at")
    parser.add_argument("--catalog-changed", choices=("true", "false", "unknown"), default="unknown")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pr-body-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        previous = load_previous_status(args.previous)
        status = build_status(
            previous=previous,
            attempt_at=args.attempt_at,
            trigger=args.trigger,
            attempt_status=args.attempt_status,
            phase=args.phase,
            error_code=args.error_code,
            error_summary=args.error_summary,
            source_check_at=args.source_check_at,
            catalog_changed=_bool_value(args.catalog_changed),
            published_catalog_observed_at=_snapshot_observed_at(args.published_snapshot),
            published_main_sha=args.published_main_sha,
            candidate_catalog_observed_at=_snapshot_observed_at(args.candidate_snapshot),
            candidate_sha=args.candidate_sha,
            candidate_pr=args.candidate_pr,
            candidate_validation=args.candidate_validation,
        )
    except StatusError as exc:
        print(f"ОШИБКА RouterAI status ownership: {exc}", file=sys.stderr)
        return 2
    args.output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.pr_body_output:
        args.pr_body_output.write_text(pr_body(status), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
