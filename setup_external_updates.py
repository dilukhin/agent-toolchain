"Read-only external CLI update advisories and RouterAI refresh status cache."
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from setup_inventory import ExternalCliInventory, common_external_cli_inventory
from setup_lib import atomic_write, run

CACHE_SCHEMA = 1
DEFAULT_TTL = 24 * 60 * 60
OPENCODE_LATEST_URL = "https://opencode.ai/update/api/latest/cli/npm"
OPENCODE_LATEST_MAX_BYTES = 64 * 1024


def _version(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 120:
        return None
    match = re.fullmatch(
        r"(?:(?:opencode|codex|codex-cli)\s+)?v?"
        r"([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)",
        value.strip(),
    )
    return match.group(1) if match else None


def _version_key(value: str) -> tuple:
    release, _, prerelease = value.split("+", 1)[0].partition("-")
    core = tuple(int(part) for part in release.split("."))
    parts = tuple((0, int(part)) if part.isdigit() else (1, part) for part in prerelease.split("."))
    return core, (0, parts) if prerelease else (1, ())


def _standalone_v2(item: ExternalCliInventory) -> bool:
    if not item.active or item.spec.command != "opencode" or item.active.provider != "standalone":
        return False
    version = _version(item.active.version)
    return version is not None and version.startswith("2.") and "-" not in version


def _lookup_supported(item: ExternalCliInventory) -> bool:
    return bool(item.active and (
        (item.active.provider == "npm" and item.active.package)
        or item.active.provider == "chocolatey" or _standalone_v2(item)
    ))


def _standalone_latest(timeout: float) -> tuple[str | None, str | None]:
    # Same read-only metadata endpoint as the official OpenCode V2 installer.
    request = urllib.request.Request(OPENCODE_LATEST_URL, headers={
        "Accept": "application/json", "User-Agent": "agent-toolchain-updates/1",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(OPENCODE_LATEST_MAX_BYTES + 1)
        if len(raw) > OPENCODE_LATEST_MAX_BYTES:
            return None, "OpenCode release metadata exceeds size limit"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return None, "OpenCode returned malformed release metadata"
        version = _version(data.get("version"))
        metadata = data.get("metadata")
        package = metadata.get("package") if isinstance(metadata, dict) else None
        if (not version or not version.startswith("2.") or "-" in version
                or package not in ("@opencode/cli", "@opencode-ai/cli")):
            return None, "OpenCode returned unsupported release metadata"
        return version, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, "OpenCode release lookup failed; check network access"
    except (UnicodeDecodeError, ValueError):
        return None, "OpenCode returned malformed release metadata"

ROUTERAI_STATUS_SCHEMA = 1
ROUTERAI_STATUS_CACHE_SCHEMA = 1
ROUTERAI_STATUS_URL = (
    "https://raw.githubusercontent.com/dilukhin/agent-toolchain/"
    "automation/routerai-status/routerai-refresh-status.json"
)
ROUTERAI_STATUS_TTL = 6 * 60 * 60
ROUTERAI_STATUS_MAX_BYTES = 256 * 1024
ROUTERAI_CURRENT_MAX_AGE = 36 * 60 * 60
ROUTERAI_STALE_MAX_AGE = 72 * 60 * 60


def cache_path() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_UPDATE_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "cache" / "external-tool-updates.json"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "agent-toolchain" / "external-tool-updates.json"


def load_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != CACHE_SCHEMA or not isinstance(data.get("tools"), dict):
            return {"schema": CACHE_SCHEMA, "tools": {}}
        return data
    except (OSError, ValueError, TypeError):
        return {"schema": CACHE_SCHEMA, "tools": {}}


def cache_fresh(record: dict[str, Any], now: float | None = None, ttl: int = DEFAULT_TTL) -> bool:
    try:
        current = time.time() if now is None else now
        return current - float(record["checked_at"]) < ttl
    except (KeyError, TypeError, ValueError):
        return False


def _latest(item: ExternalCliInventory, timeout: float) -> tuple[str | None, str | None]:
    if not item.active:
        return None, "executable not installed"
    if item.conflict:
        return None, "provider conflict; lookup suppressed"
    if _standalone_v2(item):
        return _standalone_latest(timeout)
    provider = item.active.provider
    if provider == "npm" and item.active.package:
        try:
            cp = run([shutil.which("npm") or "npm", "view", item.active.package, "version", "--json"], timeout=timeout)
        except (TimeoutError, subprocess.TimeoutExpired):
            return None, "npm lookup timed out"
        if cp.returncode == 0:
            try:
                return str(json.loads(cp.stdout)), None
            except (ValueError, TypeError):
                return None, "npm returned malformed version"
        return None, (cp.stderr or "npm lookup failed").strip()[-240:]
    if provider == "chocolatey":
        choco = shutil.which("choco")
        if not choco:
            return None, "choco executable not found"
        try:
            cp = run([choco, "search", item.spec.command, "--exact", "--limit-output"], timeout=timeout)
        except (TimeoutError, subprocess.TimeoutExpired):
            return None, "choco lookup timed out"
        if cp.returncode == 0:
            for line in cp.stdout.splitlines():
                fields = line.split("|")
                if len(fields) >= 2 and fields[0].lower() == item.spec.command.lower():
                    return fields[1], None
            return None, "choco exact search returned no matching package row"
        if cp.returncode == 2:
            return None, "choco exact search returned no results"
        return None, (cp.stderr or "choco lookup failed").strip()[-240:]
    return None, "no safe provider-native lookup available"


def refresh(*, path: Path | None = None, timeout: float = 3.0) -> dict[str, Any]:
    # setup_lib.run is intentionally kept as the bounded adapter seam for tests and callers.
    result: dict[str, Any] = {"schema": CACHE_SCHEMA, "tools": {}}
    now = time.time()
    for name, inventory in common_external_cli_inventory().items():
        installed = inventory.active.version if inventory.active else None
        latest, error, reason = None, None, None
        if not inventory.active:
            status = "not_installed"
            reason = "Программа не найдена в PATH; проверка обновлений пропущена."
        elif inventory.conflict:
            status = "conflict"
            reason = "Обнаружен конфликт способов установки; проверка обновлений пропущена."
        elif not _lookup_supported(inventory):
            status = "unsupported"
            reason = "Для этого способа установки или версии проверка обновлений пока не поддерживается."
        else:
            try:
                latest, error = _latest(inventory, timeout)
            except (OSError, TimeoutError, subprocess.TimeoutExpired):
                error = "Запрос версии не выполнен; проверьте доступность сети и менеджера пакетов."
            if not error:
                latest = _version(latest)
                if latest is None:
                    error = "Источник вернул некорректную версию."
            status = "error" if error else "ok"
        advice = None
        if status == "ok":
            advice = "opencode upgrade" if _standalone_v2(inventory) else inventory.update_advice
        result["tools"][name] = {
            "tool": name, "provider": inventory.active.provider if inventory.active else "unknown",
            "installed_version": installed, "latest_version": latest,
            "checked_at": now, "status": status,
            "error": error, "reason": reason, "advice": advice,
        }
    atomic_write(path or cache_path(), (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return result


def advisory(inventory: ExternalCliInventory, record: dict[str, Any] | None) -> str | None:
    if not record or not inventory.active:
        return None
    label = inventory.spec.display_name or inventory.spec.command
    if inventory.conflict:
        return f"{label}: конфликт способов установки; рекомендация обновления недоступна"
    if record.get("provider") != inventory.active.provider:
        return f"{label}: способ установки изменился; выполните toolchainctl updates refresh"
    if _version(record.get("installed_version")) != _version(inventory.active.version):
        return f"{label}: установленная версия изменилась; выполните toolchainctl updates refresh"
    if record.get("status") in {"not_installed", "conflict"}:
        return f"{label}: установка изменилась; выполните toolchainctl updates refresh"
    if record.get("status") == "unsupported":
        return f"{label}: проверка обновлений для этого способа установки или версии пока не поддерживается"
    if record.get("status") != "ok" or not record.get("latest_version"):
        return f"{label}: сведения об обновлении недоступны: " + str(record.get("error") or "неизвестная ошибка")
    installed, latest = _version(inventory.active.version), _version(record.get("latest_version"))
    if installed is None or latest is None:
        return f"{label}: не удалось сопоставить версии; проверьте их вручную"
    if _version_key(latest) > _version_key(installed):
        advice = record.get("advice")
        suffix = f"; {advice}" if advice else ""
        return f"{label}: доступно обновление {installed} -> {latest}{suffix}"
    return None


def routerai_status_cache_path() -> Path:
    override = os.environ.get("AGENT_TOOLCHAIN_ROUTERAI_STATUS_CACHE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "agent-toolchain" / "cache" / "routerai-status.json"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "agent-toolchain" / "routerai-status.json"


def _valid_routerai_status(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schema") != ROUTERAI_STATUS_SCHEMA:
        return None
    notice = value.get("_managed_notice")
    if not isinstance(notice, str) or not notice:
        return None
    return value


def load_routerai_status_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or routerai_status_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != ROUTERAI_STATUS_CACHE_SCHEMA:
            raise ValueError("cache schema mismatch")
        if not isinstance(data.get("fetched_at"), (int, float)):
            raise ValueError("cache timestamp missing")
        status = _valid_routerai_status(data.get("status"))
        if status is None:
            raise ValueError("cached status invalid")
        return {"schema": ROUTERAI_STATUS_CACHE_SCHEMA, "fetched_at": float(data["fetched_at"]), "status": status}
    except (OSError, ValueError, TypeError):
        return {"schema": ROUTERAI_STATUS_CACHE_SCHEMA, "fetched_at": 0.0, "status": None}


def routerai_status_cache_fresh(
    cache: dict[str, Any],
    now: float | None = None,
    ttl: int = ROUTERAI_STATUS_TTL,
) -> bool:
    try:
        current = time.time() if now is None else now
        return current - float(cache["fetched_at"]) < ttl and _valid_routerai_status(cache.get("status")) is not None
    except (KeyError, TypeError, ValueError):
        return False


def refresh_routerai_status(
    *,
    path: Path | None = None,
    timeout: float = 3.0,
    now: float | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        ROUTERAI_STATUS_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "agent-toolchain-routerai-status/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(ROUTERAI_STATUS_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"не удалось получить состояние RouterAI: {exc}") from exc
    if len(data) > ROUTERAI_STATUS_MAX_BYTES:
        raise RuntimeError("файл состояния RouterAI превышает допустимый размер")
    try:
        status = _valid_routerai_status(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"состояние RouterAI содержит некорректный JSON: {exc}") from exc
    if status is None:
        raise RuntimeError("состояние RouterAI не соответствует поддерживаемой схеме")
    wrapper = {
        "schema": ROUTERAI_STATUS_CACHE_SCHEMA,
        "fetched_at": time.time() if now is None else now,
        "status": status,
    }
    atomic_write(
        path or routerai_status_cache_path(),
        (json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return wrapper


def get_routerai_status(
    *,
    force_refresh: bool = False,
    path: Path | None = None,
    timeout: float = 3.0,
    now: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    current = time.time() if now is None else now
    cache = load_routerai_status_cache(path)
    error: str | None = None
    if force_refresh or not routerai_status_cache_fresh(cache, current):
        try:
            cache = refresh_routerai_status(path=path, timeout=timeout, now=current)
        except RuntimeError as exc:
            error = str(exc)
    status = _valid_routerai_status(cache.get("status"))
    return status, {
        "cache_path": str(path or routerai_status_cache_path()),
        "fetched_at": float(cache.get("fetched_at") or 0.0),
        "fetch_error": error,
    }


def _parse_utc(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _age_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "меньше минуты"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин."
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч."
    days = hours // 24
    remainder = hours % 24
    return f"{days} дн. {remainder} ч." if remainder else f"{days} дн."


def installed_routerai_catalog_observed_at(repo_root: Path) -> str | None:
    try:
        data = json.loads(
            (repo_root / "templates" / "routerai_catalog.generated.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("observed_at")
    return value if isinstance(value, str) and value else None


def routerai_status_advisory(
    status: dict[str, Any] | None,
    *,
    installed_observed_at: str | None,
    meta: dict[str, Any] | None = None,
    now: float | None = None,
) -> list[str]:
    current = time.time() if now is None else now
    meta = meta or {}
    if status is None:
        suffix = ""
        if meta.get("fetch_error"):
            suffix = f" Причина: {meta['fetch_error']}."
        return [
            "RouterAI: сведения об актуальности цен недоступны; запуск продолжается."
            + suffix
        ]

    lines: list[str] = []
    success = status.get("last_successful_check")
    success_at = _parse_utc(success.get("at")) if isinstance(success, dict) else None
    attempt = status.get("last_attempt")
    failed = isinstance(attempt, dict) and attempt.get("status") == "failed"

    if success_at is None:
        lines.append("RouterAI: нет подтверждённой даты последней успешной проверки цен.")
    else:
        age = current - success_at
        if failed:
            lines.append(
                "ВНИМАНИЕ: последняя попытка обновить цены RouterAI завершилась ошибкой; "
                f"последняя успешная проверка была {_age_text(age)} назад."
            )
        elif age <= ROUTERAI_CURRENT_MAX_AGE:
            lines.append(f"RouterAI: цены актуальны, каталог проверен {_age_text(age)} назад.")
        elif age <= ROUTERAI_STALE_MAX_AGE:
            lines.append(
                f"RouterAI: цены последний раз подтверждались {_age_text(age)} назад."
            )
        else:
            lines.append(
                "ВНИМАНИЕ: цены RouterAI давно не подтверждались; "
                f"последняя успешная проверка была {_age_text(age)} назад."
            )

    if failed and isinstance(attempt, dict):
        error = attempt.get("error")
        if isinstance(error, dict) and isinstance(error.get("summary"), str):
            lines.append(f"Причина последней ошибки: {error['summary']}")
        lines.append("Используются последние опубликованные цены; запуск продолжается.")

    published = status.get("published")
    published_at = (
        published.get("catalog_observed_at")
        if isinstance(published, dict) and isinstance(published.get("catalog_observed_at"), str)
        else None
    )
    candidate = status.get("candidate")
    if isinstance(candidate, dict):
        candidate_at = candidate.get("catalog_observed_at")
        if isinstance(candidate_at, str) and _parse_utc(candidate_at) is not None:
            if published_at is None or (_parse_utc(candidate_at) or 0) > (_parse_utc(published_at) or 0):
                pr = candidate.get("pr_number")
                validation = candidate.get("validation")
                suffix = f" PR #{pr}." if isinstance(pr, int) else "."
                if validation == "success":
                    lines.append("Обнаружены более новые цены RouterAI; кандидат проверен и ожидает слияния" + suffix)
                elif validation == "failed":
                    lines.append("Обнаружены более новые цены RouterAI, но кандидат не прошёл проверку" + suffix)
                else:
                    lines.append("Обнаружены более новые цены RouterAI; кандидат ещё проверяется" + suffix)

    installed_ts = _parse_utc(installed_observed_at)
    published_ts = _parse_utc(published_at)
    if installed_ts is not None and published_ts is not None and published_ts > installed_ts:
        lines.append(
            "В main уже опубликованы более новые цены. Обновить установленный toolchain: "
            "toolchainctl update --apply"
        )

    if meta.get("fetch_error") and status is not None:
        fetched_at = float(meta.get("fetched_at") or 0.0)
        cache_age = _age_text(current - fetched_at) if fetched_at else "неизвестного возраста"
        lines.append(
            "Не удалось обновить удалённое состояние RouterAI; используется локальный кэш "
            f"({cache_age})."
        )
    return lines
