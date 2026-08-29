#!/usr/bin/env python3
"""Refresh RouterAI catalog and reproducibly generate managed model sections."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "templates/routerai_model_policy.json"
SNAPSHOT_PATH = ROOT / "templates/routerai_catalog.generated.json"
CONFIG_PATH = ROOT / "config_data.json"
TEMPLATE_PATH = ROOT / "templates/opencode.jsonc"
MODELS_URL = "https://routerai.ru/api/v1/models"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
STATUS_DOC = "docs/routerai_refresh_status_design_ru.md"
MANUAL_REFRESH_COMMAND = "gh workflow run routerai_catalog.yml --repo dilukhin/agent-toolchain --ref main"
OFFLINE_SYNC_COMMAND = "python3 scripts/update_routerai_catalog.py --sync-generated"
CONFIG_MANAGED_NOTICE = {
    "models": (
        "НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ. Раздел models принадлежит генератору RouterAI. "
        "Состав моделей, роли и описания изменяйте в templates/routerai_model_policy.json, "
        f"после чего пересоберите производные области без сети: {OFFLINE_SYNC_COMMAND}. "
        f"Подробности: {STATUS_DOC}. Полный ручной refresh: {MANUAL_REFRESH_COMMAND}"
    )
}
SNAPSHOT_MANAGED_NOTICE = (
    "НЕ РЕДАКТИРОВАТЬ ВРУЧНУЮ. Файл полностью формируется автоматизацией RouterAI "
    f"из {MODELS_URL}. Подробности и переносимый шаблон: {STATUS_DOC}. "
    f"Полный ручной refresh: {MANUAL_REFRESH_COMMAND}"
)
GENERATED_RESOURCES = {
    "templates/routerai_catalog.generated.json": "*",
    "config_data.json": "models",
    "templates/opencode.jsonc": "provider.routerai.models",
}


class CatalogError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path} must contain a JSON object")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _render(value: dict[str, Any]) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=2) + "\n"


def _fetch() -> dict[str, Any]:
    request = urllib.request.Request(
        MODELS_URL,
        headers={"Accept": "application/json", "User-Agent": "agent-toolchain-routerai-catalog/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CatalogError(f"RouterAI catalog request failed: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CatalogError(f"RouterAI catalog exceeds {MAX_RESPONSE_BYTES} bytes")
    try:
        value = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"RouterAI catalog is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError("RouterAI catalog root is not an object")
    return value


def _pricing(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        try:
            if isinstance(raw, (str, int, Decimal)):
                result[str(key)] = format(Decimal(raw), "f")
        except InvalidOperation:
            pass
    return result


def normalize_catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise CatalogError("RouterAI catalog has no data array")
    out: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        entry: dict[str, Any] = {}
        for key in ("name", "created", "context_length"):
            if isinstance(item.get(key), (str, int)):
                entry[key] = item[key]
        architecture = item.get("architecture")
        if isinstance(architecture, dict):
            clean: dict[str, Any] = {}
            for key in ("input_modalities", "output_modalities", "tokenizer"):
                value = architecture.get(key)
                if isinstance(value, str) or (isinstance(value, list) and all(isinstance(x, str) for x in value)):
                    clean[key] = value
            if clean:
                entry["architecture"] = clean
        prices = _pricing(item.get("pricing"))
        if prices:
            entry["pricing"] = prices
        out[item["id"]] = entry
    if not out:
        raise CatalogError("RouterAI catalog contains no valid model records")
    return dict(sorted(out.items()))


def _million(prices: dict[str, str], key: str) -> int | None:
    raw = prices.get(key)
    if raw is None:
        return None
    try:
        value = Decimal(raw) * Decimal(1_000_000)
    except InvalidOperation:
        return None
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result


def _generate(
    live: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    previous_snapshot: dict[str, Any],
    config: dict[str, Any],
    template: dict[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    policy_models = policy.get("models")
    current_models = config.get("models")
    if not isinstance(policy_models, dict):
        raise CatalogError("RouterAI policy models is not an object")
    if not isinstance(current_models, dict):
        raise CatalogError("config_data.json models is not an object")
    old_names = previous_snapshot.get("managed_names")
    old_names = old_names if isinstance(old_names, dict) else {}

    generated: dict[str, Any] = {}
    managed_names: dict[str, list[str]] = {}
    missing: list[str] = []
    for model_id, spec in policy_models.items():
        if not isinstance(spec, dict):
            raise CatalogError(f"policy model is not an object: {model_id}")
        display, role, description = spec.get("display_name"), spec.get("role"), spec.get("description")
        if not all(isinstance(x, str) and x for x in (display, role, description)):
            raise CatalogError(f"policy model has invalid display_name/role/description: {model_id}")
        target: dict[str, Any] = {"description": description}
        live_item = live.get(model_id)
        if live_item is None:
            missing.append(model_id)
            target["name"] = f"{display} [{role}, цена недоступна]"
        else:
            prices = live_item.get("pricing") if isinstance(live_item.get("pricing"), dict) else {}
            input_price, output_price = _million(prices, "prompt"), _million(prices, "completion")
            if input_price is None or output_price is None:
                target["name"] = f"{display} [{role}, цена недоступна]"
            else:
                target.update(
                    name=f"{display} [{role}, {input_price}/{output_price} ₽]",
                    price_input_rub_per_1m=input_price,
                    price_output_rub_per_1m=output_price,
                )
                cache = _million(prices, "input_cache_read")
                if cache is not None:
                    target["cache_read_rub_per_1m"] = cache
        generated[model_id] = target
        aliases: list[Any] = []
        if isinstance(old_names.get(model_id), list):
            aliases += old_names[model_id]
        if isinstance(spec.get("legacy_names"), list):
            aliases += spec["legacy_names"]
        previous = current_models.get(model_id)
        if isinstance(previous, dict):
            aliases.append(previous.get("name"))
        aliases.append(target["name"])
        managed_names[model_id] = _dedupe(aliases)

    plain_config = json.loads(json.dumps(_plain(config), ensure_ascii=False))
    plain_config.pop("_managed_notice", None)
    new_config = {"_managed_notice": CONFIG_MANAGED_NOTICE, **plain_config, "models": generated}
    new_template = json.loads(json.dumps(_plain(template), ensure_ascii=False))
    try:
        template_models = new_template["provider"]["routerai"]["models"]
    except (KeyError, TypeError) as exc:
        raise CatalogError("templates/opencode.jsonc has no provider.routerai.models object") from exc
    if not isinstance(template_models, dict):
        raise CatalogError("templates/opencode.jsonc provider.routerai.models is not an object")
    new_template["provider"]["routerai"]["models"] = {
        model_id: {"name": spec["name"]} for model_id, spec in generated.items()
    }

    changed = (
        previous_snapshot.get("models") != live
        or previous_snapshot.get("managed_names") != managed_names
        or previous_snapshot.get("source_state") != "live"
    )
    snapshot = {
        "_managed_notice": SNAPSHOT_MANAGED_NOTICE,
        "schema": 1,
        "source_url": MODELS_URL,
        "source_state": "live",
        "observed_at": observed_at if changed else previous_snapshot.get("observed_at"),
        "models": live,
        "managed_names": managed_names,
    }
    return snapshot, new_config, new_template, missing


def build_outputs(payload, policy, previous_snapshot, config, template, *, observed_at):
    return _generate(normalize_catalog(payload), policy, previous_snapshot, config, template, observed_at=observed_at)


def build_generated_from_snapshot(policy, snapshot, config, template):
    live = snapshot.get("models")
    if not isinstance(live, dict) or not live:
        raise CatalogError("generated RouterAI snapshot has no models object")
    _snapshot, new_config, new_template, missing = _generate(
        live, policy, snapshot, config, template,
        observed_at=str(snapshot.get("observed_at") or "1970-01-01T00:00:00Z"),
    )
    return new_config, new_template, missing


def verify_generated_state(policy, snapshot, config, template) -> list[str]:
    expected_config, expected_template, _missing = build_generated_from_snapshot(policy, snapshot, config, template)
    violations: list[str] = []
    if config.get("models") != expected_config.get("models"):
        violations.append("config_data.json -> models")
    try:
        actual_models = template["provider"]["routerai"]["models"]
        expected_models = expected_template["provider"]["routerai"]["models"]
    except (KeyError, TypeError) as exc:
        raise CatalogError("templates/opencode.jsonc has no provider.routerai.models object") from exc
    if actual_models != expected_models:
        violations.append("templates/opencode.jsonc -> provider.routerai.models")
    snapshot_notice, config_notice = snapshot.get("_managed_notice"), config.get("_managed_notice")
    if snapshot_notice is not None and snapshot_notice != SNAPSHOT_MANAGED_NOTICE:
        violations.append("templates/routerai_catalog.generated.json -> _managed_notice")
    if (snapshot_notice is not None or config_notice is not None) and config_notice != CONFIG_MANAGED_NOTICE:
        violations.append("config_data.json -> _managed_notice")
    return violations


def _changed(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") != text
    except OSError:
        return True


def _label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_repo():
    return _load(POLICY_PATH), _load(SNAPSHOT_PATH), _load(CONFIG_PATH), _load(TEMPLATE_PATH)


def _verify() -> int:
    try:
        violations = verify_generated_state(*_load_repo())
    except CatalogError as exc:
        print(f"Проверка производных данных RouterAI не выполнена: {exc}", file=sys.stderr)
        return 2
    if not violations:
        print("Производные данные RouterAI согласованы с сохранённым каталогом и ручной политикой.")
        return 0
    print("ОШИБКА: производная область RouterAI не воспроизводится штатным генератором.", file=sys.stderr)
    for item in violations:
        print(f"  - {item}", file=sys.stderr)
    print(
        f"После изменения policy выполните:\n  {OFFLINE_SYNC_COMMAND}\n"
        f"Полный refresh:\n  {MANUAL_REFRESH_COMMAND}\nПодробности: {STATUS_DOC}",
        file=sys.stderr,
    )
    return 1


def _sync() -> int:
    try:
        policy, snapshot, config, template = _load_repo()
        new_config, new_template, missing = build_generated_from_snapshot(policy, snapshot, config, template)
    except CatalogError as exc:
        print(f"Офлайновая пересборка RouterAI не выполнена: {exc}", file=sys.stderr)
        return 2
    outputs = {CONFIG_PATH: _render(new_config), TEMPLATE_PATH: _render(new_template)}
    changed = [path for path, text in outputs.items() if _changed(path, text)]
    for path in changed:
        path.write_text(outputs[path], encoding="utf-8", newline="\n")
        print(f"updated: {_label(path)}")
    if missing:
        print("Модели policy отсутствуют в сохранённом RouterAI snapshot: " + ", ".join(missing), file=sys.stderr)
    print(f"Офлайновая пересборка RouterAI: {len(changed)} file(s) changed; snapshot не изменялся.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh RouterAI catalog and managed model sections.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--sync-generated", action="store_true")
    mode.add_argument("--verify-generated", action="store_true")
    parser.add_argument("--input", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verify_generated:
        if args.input:
            print("--input нельзя использовать вместе с --verify-generated", file=sys.stderr)
            return 2
        return _verify()
    if args.sync_generated:
        if args.input:
            print("--input нельзя использовать вместе с --sync-generated", file=sys.stderr)
            return 2
        return _sync()
    try:
        policy, old_snapshot, config, template = _load_repo()
        payload = _load(args.input) if args.input else _fetch()
        observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snapshot, new_config, new_template, missing = build_outputs(
            payload, policy, old_snapshot, config, template, observed_at=observed_at
        )
    except CatalogError as exc:
        print(f"RouterAI catalog update failed: {exc}", file=sys.stderr)
        return 2
    outputs = {SNAPSHOT_PATH: _render(snapshot), CONFIG_PATH: _render(new_config), TEMPLATE_PATH: _render(new_template)}
    changed = [path for path, text in outputs.items() if _changed(path, text)]
    policy_models = policy.get("models")
    print(
        f"RouterAI catalog: {len(snapshot['models'])} live model(s); "
        f"{len(policy_models) if isinstance(policy_models, dict) else 0} curated model(s); "
        f"{len(changed)} file(s) changed"
    )
    if missing:
        print("Policy models missing from current RouterAI catalog: " + ", ".join(missing), file=sys.stderr)
    if args.check:
        for path in changed:
            print(f"outdated: {_label(path)}")
        return 1 if changed else 0
    for path in changed:
        path.write_text(outputs[path], encoding="utf-8", newline="\n")
        print(f"updated: {_label(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
