#!/usr/bin/env python3
"""Refresh RouterAI objective model data and regenerate managed OpenCode labels."""
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
POLICY_PATH = ROOT / "templates" / "routerai_model_policy.json"
SNAPSHOT_PATH = ROOT / "templates" / "routerai_catalog.generated.json"
CONFIG_PATH = ROOT / "config_data.json"
TEMPLATE_PATH = ROOT / "templates" / "opencode.jsonc"
MODELS_URL = "https://routerai.ru/api/v1/models"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class CatalogError(RuntimeError):
    pass


def _json_load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path} must contain a JSON object")
    return value


def _plain_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _plain_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain_json(v) for v in value]
    return value


def _fetch_models() -> dict[str, Any]:
    request = urllib.request.Request(
        MODELS_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "agent-toolchain-routerai-catalog/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CatalogError(f"RouterAI catalog request failed: {exc}") from exc
    if len(data) > MAX_RESPONSE_BYTES:
        raise CatalogError(f"RouterAI catalog exceeds {MAX_RESPONSE_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"RouterAI catalog is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError("RouterAI catalog root is not an object")
    return value


def _model_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise CatalogError("RouterAI catalog has no data array")
    result: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result.append(item)
    if not result:
        raise CatalogError("RouterAI catalog contains no valid model records")
    return result


def _pricing_strings(pricing: Any) -> dict[str, str]:
    if not isinstance(pricing, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in pricing.items():
        if isinstance(value, (int, Decimal)):
            result[str(key)] = format(Decimal(value), "f")
        elif isinstance(value, str):
            try:
                result[str(key)] = format(Decimal(value), "f")
            except InvalidOperation:
                continue
    return result


def normalize_catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for item in _model_list(payload):
        model_id = item["id"]
        entry: dict[str, Any] = {}
        for key in ("name", "created", "context_length"):
            value = item.get(key)
            if isinstance(value, (str, int)):
                entry[key] = value
        architecture = item.get("architecture")
        if isinstance(architecture, dict):
            clean_arch: dict[str, Any] = {}
            for key in ("input_modalities", "output_modalities", "tokenizer"):
                value = architecture.get(key)
                if isinstance(value, str):
                    clean_arch[key] = value
                elif isinstance(value, list) and all(isinstance(x, str) for x in value):
                    clean_arch[key] = value
            if clean_arch:
                entry["architecture"] = clean_arch
        pricing = _pricing_strings(item.get("pricing"))
        if pricing:
            entry["pricing"] = pricing
        normalized[model_id] = entry
    return dict(sorted(normalized.items()))


def _per_million(pricing: dict[str, str], key: str) -> int | None:
    raw = pricing.get(key)
    if raw is None:
        return None
    try:
        value = Decimal(raw) * Decimal(1_000_000)
    except InvalidOperation:
        return None
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str) and value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def build_outputs(
    payload: dict[str, Any],
    policy: dict[str, Any],
    previous_snapshot: dict[str, Any],
    config: dict[str, Any],
    template: dict[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    live_models = normalize_catalog(payload)
    policy_models = policy.get("models")
    current_models = config.get("models")
    if not isinstance(policy_models, dict):
        raise CatalogError("RouterAI policy models is not an object")
    if not isinstance(current_models, dict):
        raise CatalogError("config_data.json models is not an object")

    old_managed = previous_snapshot.get("managed_names", {})
    if not isinstance(old_managed, dict):
        old_managed = {}

    new_models: dict[str, Any] = {}
    managed_names: dict[str, list[str]] = {}
    missing: list[str] = []

    for model_id, policy_spec in policy_models.items():
        if not isinstance(policy_spec, dict):
            raise CatalogError(f"policy model is not an object: {model_id}")
        display_name = policy_spec.get("display_name")
        role = policy_spec.get("role")
        description = policy_spec.get("description")
        if not all(isinstance(x, str) and x for x in (display_name, role, description)):
            raise CatalogError(f"policy model has invalid display_name/role/description: {model_id}")

        previous = current_models.get(model_id)
        previous = dict(previous) if isinstance(previous, dict) else {}
        live = live_models.get(model_id)
        target = dict(previous)
        target["description"] = description

        if live is None:
            missing.append(model_id)
            if "name" not in target:
                target["name"] = f"{display_name} [{role}, цена недоступна]"
        else:
            pricing = live.get("pricing", {})
            pricing = pricing if isinstance(pricing, dict) else {}
            input_price = _per_million(pricing, "prompt")
            output_price = _per_million(pricing, "completion")
            if input_price is not None and output_price is not None:
                target["price_input_rub_per_1m"] = input_price
                target["price_output_rub_per_1m"] = output_price
                target["name"] = f"{display_name} [{role}, {input_price}/{output_price} ₽]"
            elif "name" not in target:
                target["name"] = f"{display_name} [{role}, цена недоступна]"

        new_models[model_id] = target
        aliases: list[Any] = []
        previous_aliases = old_managed.get(model_id, [])
        if isinstance(previous_aliases, list):
            aliases.extend(previous_aliases)
        legacy = policy_spec.get("legacy_names", [])
        if isinstance(legacy, list):
            aliases.extend(legacy)
        if isinstance(previous.get("name"), str):
            aliases.append(previous["name"])
        if isinstance(target.get("name"), str):
            aliases.append(target["name"])
        managed_names[model_id] = _dedupe_strings(aliases)

    new_config = json.loads(json.dumps(_plain_json(config), ensure_ascii=False))
    new_config["models"] = new_models

    new_template = json.loads(json.dumps(_plain_json(template), ensure_ascii=False))
    try:
        template_models = new_template["provider"]["routerai"]["models"]
    except (KeyError, TypeError) as exc:
        raise CatalogError("templates/opencode.jsonc has no provider.routerai.models object") from exc
    if not isinstance(template_models, dict):
        raise CatalogError("templates/opencode.jsonc provider.routerai.models is not an object")
    new_template["provider"]["routerai"]["models"] = {
        model_id: {"name": spec["name"]} for model_id, spec in new_models.items()
    }

    prior_live = previous_snapshot.get("models")
    prior_names = previous_snapshot.get("managed_names")
    catalog_changed = prior_live != live_models or prior_names != managed_names or previous_snapshot.get("source_state") != "live"
    new_snapshot = {
        "schema": 1,
        "source_url": MODELS_URL,
        "source_state": "live",
        "observed_at": observed_at if catalog_changed else previous_snapshot.get("observed_at"),
        "models": live_models,
        "managed_names": managed_names,
    }
    return new_snapshot, new_config, new_template, missing


def _render(value: dict[str, Any]) -> str:
    return json.dumps(_plain_json(value), ensure_ascii=False, indent=2) + "\n"


def _changed(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") != text
    except OSError:
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh RouterAI model catalog and managed OpenCode labels.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report whether generated data is stale; do not write")
    mode.add_argument("--write", action="store_true", help="write regenerated catalog/config/template")
    parser.add_argument("--input", type=Path, help="read RouterAI /models JSON from a local fixture instead of the network")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = _json_load(POLICY_PATH)
        previous_snapshot = _json_load(SNAPSHOT_PATH)
        config = _json_load(CONFIG_PATH)
        template = _json_load(TEMPLATE_PATH)
        payload = _json_load(args.input) if args.input else _fetch_models()
        observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snapshot, new_config, new_template, missing = build_outputs(
            payload, policy, previous_snapshot, config, template, observed_at=observed_at
        )
    except CatalogError as exc:
        print(f"RouterAI catalog update failed: {exc}", file=sys.stderr)
        return 2

    outputs = {
        SNAPSHOT_PATH: _render(snapshot),
        CONFIG_PATH: _render(new_config),
        TEMPLATE_PATH: _render(new_template),
    }
    changed = [path for path, text in outputs.items() if _changed(path, text)]
    print(
        f"RouterAI catalog: {len(snapshot['models'])} live model(s); "
        f"{len(policy['models'])} curated model(s); {len(changed)} file(s) changed"
    )
    if missing:
        print("Policy models missing from current RouterAI catalog: " + ", ".join(missing), file=sys.stderr)

    if args.check:
        for path in changed:
            print(f"outdated: {path.relative_to(ROOT)}")
        return 1 if changed else 0

    for path, text in outputs.items():
        if path in changed:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"updated: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
