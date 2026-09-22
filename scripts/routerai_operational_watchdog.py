#!/usr/bin/env python3
"""Evaluate RouterAI refresh health and idempotently plan one operational issue.

The script is deliberately side-effect free: GitHub reads/writes stay in the workflow.
It consumes snapshots of workflow runs, managed refresh status and repository issues,
then emits a deterministic reconciliation decision.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT_RUN_NAME = "RouterAI catalog refresh contract-v1"
STATUS_SCHEMA = 1
STATUS_OWNER = "agent-toolchain:routerai-status:v1"
ISSUE_MARKER = "<!-- agent-toolchain:routerai-operational-alert:v1 -->"
ISSUE_TITLE = "Operational: деградация обновления RouterAI"
EXPECTED_ISSUE_AUTHOR = "github-actions[bot]"
DEFAULT_STALE_HOURS = 72


class WatchdogError(RuntimeError):
    pass


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchdogError(f"cannot read JSON {path}: {exc}") from exc


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _flatten_pages(value: Any, key: str | None = None) -> list[dict[str, Any]]:
    """Accept one GitHub object, an array of objects, or gh --slurp pages."""
    if key is None and isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return list(value)
    pages = value if isinstance(value, list) else [value]
    result: list[dict[str, Any]] = []
    for page in pages:
        if key and isinstance(page, dict):
            items = page.get(key)
        elif key is None and isinstance(page, dict):
            items = [page]
        else:
            items = page
        if isinstance(items, list):
            result.extend(item for item in items if isinstance(item, dict))
    return result


def _contract_runs(raw: Any) -> list[dict[str, Any]]:
    runs = _flatten_pages(raw, "workflow_runs")
    selected = [
        run for run in runs
        if run.get("display_title") == CONTRACT_RUN_NAME
        and run.get("status") == "completed"
        and isinstance(run.get("id"), int)
    ]
    selected.sort(
        key=lambda run: _parse_time(run.get("updated_at") or run.get("created_at"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return selected


def _valid_status(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != STATUS_SCHEMA or raw.get("owner") != STATUS_OWNER:
        return None
    attempt = raw.get("last_attempt")
    if not isinstance(attempt, dict):
        return None
    return raw


def _status_run_id(status: dict[str, Any] | None) -> int | None:
    if not status:
        return None
    attempt = status.get("last_attempt")
    value = attempt.get("run_id") if isinstance(attempt, dict) else None
    return value if isinstance(value, int) else None


def evaluate(raw_runs: Any, raw_status: Any, *, now: datetime, stale_hours: int) -> dict[str, Any]:
    runs = _contract_runs(raw_runs)
    status = _valid_status(raw_status)
    latest = runs[0] if runs else None
    successes = [run for run in runs if run.get("conclusion") == "success"]
    last_success = successes[0] if successes else None
    reasons: list[dict[str, Any]] = []

    if latest is None:
        reasons.append({
            "code": "no-contract-run",
            "summary": "После включения нового контракта ещё не наблюдался завершённый полный refresh.",
        })
    elif latest.get("conclusion") != "success":
        reasons.append({
            "code": "latest-run-failed",
            "summary": f"Последний полный refresh завершился как {latest.get('conclusion') or 'unknown'}.",
            "run_id": latest.get("id"),
        })

    if last_success is None:
        reasons.append({
            "code": "no-full-success",
            "summary": "Нет подтверждённого успешного полного refresh по текущему контракту.",
        })
    else:
        success_at = _parse_time(last_success.get("updated_at") or last_success.get("created_at"))
        if success_at is None:
            reasons.append({
                "code": "invalid-success-time",
                "summary": "У последнего успешного refresh отсутствует корректное время завершения.",
                "run_id": last_success.get("id"),
            })
        else:
            age_hours = (now - success_at).total_seconds() / 3600
            if age_hours > stale_hours:
                reasons.append({
                    "code": "stale-full-success",
                    "summary": f"Успешного полного refresh не было более {stale_hours} часов.",
                    "run_id": last_success.get("id"),
                    "last_success_at": success_at.isoformat().replace("+00:00", "Z"),
                })

    if status is None:
        reasons.append({
            "code": "status-unavailable",
            "summary": "Managed RouterAI status отсутствует или не соответствует ожидаемому ownership/schema.",
        })
    elif latest is not None and _status_run_id(status) != latest.get("id"):
        reasons.append({
            "code": "status-out-of-sync",
            "summary": "Managed RouterAI status не соответствует последнему завершённому refresh run.",
            "run_id": latest.get("id"),
            "status_run_id": _status_run_id(status),
        })

    return {
        "healthy": not reasons,
        "reasons": reasons,
        "latest_run": _run_summary(latest),
        "last_full_success": _run_summary(last_success),
        "stale_hours": stale_hours,
    }


def _run_summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.get("id"),
        "attempt": run.get("run_attempt"),
        "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "html_url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
    }


def issue_body(evaluation: dict[str, Any]) -> str:
    reasons = evaluation.get("reasons") if isinstance(evaluation.get("reasons"), list) else []
    lines = [
        ISSUE_MARKER,
        "Этот issue принадлежит автоматике `agent-toolchain` и отражает только operational-состояние обновления RouterAI.",
        "",
        "## Активные причины",
        "",
    ]
    for reason in reasons:
        if not isinstance(reason, dict):
            continue
        lines.append(f"- `{reason.get('code', 'unknown')}` — {reason.get('summary', 'нет описания')}")
    latest = evaluation.get("latest_run") if isinstance(evaluation.get("latest_run"), dict) else None
    success = evaluation.get("last_full_success") if isinstance(evaluation.get("last_full_success"), dict) else None
    lines += ["", "## Доказательства", ""]
    if latest:
        lines.append(
            f"- Последний завершённый refresh: run `{latest.get('id')}`, attempt `{latest.get('attempt')}`, "
            f"result `{latest.get('conclusion')}`, SHA `{latest.get('head_sha')}`."
        )
    else:
        lines.append("- Завершённый refresh по текущему контракту не найден.")
    if success:
        lines.append(
            f"- Последний полный успех: run `{success.get('id')}`, завершён `{success.get('updated_at') or success.get('created_at')}`."
        )
    else:
        lines.append("- Полный успех по текущему контракту ещё не подтверждён.")
    lines += [
        f"- Бюджет отсутствия полного успеха: `{evaluation.get('stale_hoursg)}` ч.",
        "",
        "Полные технические журналы остаются в GitHub Actions; секреты и raw RouterAI payload сюда не копируются.",
        "Issue закрывается только после подтверждённого полного успеха и устранения всех активных причин.",
    ]
    return "\n".join(lines) + "\n"


def _issue_candidates(raw: Any) -> list[dict[str, Any]]:
    issues = _flatten_pages(raw)
    return [issue for issue in issues if ISSUE_MARKER in str(issue.get("body") or "")]


def reconcile(evaluation: dict[str, Any], raw_issues: Any) -> dict[str, Any]:
    candidates = _issue_candidates(raw_issues)
    if len(candidates) > 1:
        return {"action": "conflict", "reason": "multiple-owned-operational-issues"}
    issue = candidates[0] if candidates else None
    if issue is not None:
        author = issue.get("user") or issue.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if login != EXPECTED_ISSUE_AUTHOR:
            return {"action": "conflict", "reason": "owned-marker-on-foreign-issue", "number": issue.get("number")}

    if evaluation.get("healthy") is True:
        if issue is not None and str(issue.get("state") or "").lower() == "open":
            return {"action": "close", "number": issue.get("number")}
        return {"action": "noop"}

    desired_body = issue_body(evaluation)
    if issue is None:
        return {"action": "create", "title": ISSUE_TITLE, "body": desired_body}

    state = str(issue.get("state") or "").lower()
    exact = issue.get("title") == ISSUE_TITLE and str(issue.get("body") or "") == desired_body
    if state == "closed":
        return {
            "action": "reopen",
            "number": issue.get("number"),
            "title": ISSUE_TITLE,
            "body": desired_body,
        }
    if exact:
        return {"action": "noop", "number": issue.get("number")}
    return {
        "action": "update",
        "number": issue.get("number"),
        "title": ISSUE_TITLE,
        "body": desired_body,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--stale-hours", type=int, default=DEFAULT_STALE_HOURS)
    parser.add_argument("--decision-output", type=Path, required=True)
    parser.add_argument("--body-output", type=Path)
    args = parser.parse_args(argv)

    now = _parse_time(args.now)
    if now is None:
        print("invalid --now timestamp", file=sys.stderr)
        return 2
    if args.stale_hours <= 0:
        print("--stale-hours must be positive", file=sys.stderr)
        return 2
    try:
        evaluation = evaluate(_load(args.runs), _load(args.status), now=now, stale_hours=args.stale_hours)
        decision = reconcile(evaluation, _load(args.issues))
    except WatchdogError as exc:
        print(f"RouterAI watchdog input error: {exc}", file=sys.stderr)
        return 2
    decision["evaluation"] = evaluation
    args.decision_output.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.body_output and decision.get("body"):
        args.body_output.write_text(str(decision["body"]), encoding="utf-8")
    if decision.get("action") == "conflict":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
