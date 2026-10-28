"""Versioned, fail-closed RouterAI price contract. No credentials or inference."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

SCHEMA = 1
OWNER = "agent-toolchain:routerai-prices:v1"
PUBLISHER = "dilukhin/agent-toolchain"
NOTICE = "Managed RouterAI prices; do not edit. See docs/routerai_prices_contract_ru.md."
SOURCE = "https://routerai.ru/api/v1/models"
BRANCH = "automation/routerai-prices"
FILE = "routerai-prices.json"
MAX_BYTES = 8 * 1024 * 1024
UNITS = {"token", "second", "request", "image", "megapixel", "search_unit"}
MODEL_ID = re.compile(r"~?[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.:+-]*\Z")
DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


class PriceError(ValueError):
    pass


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise PriceError("invalid UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PriceError("invalid UTC timestamp") from exc


def amount(value: Any) -> Decimal:
    if not isinstance(value, str) or len(value) > 80 or not DECIMAL.fullmatch(value):
        raise PriceError("price must be a nonnegative decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PriceError("invalid decimal") from exc
    if not result.is_finite():
        raise PriceError("non-finite price")
    return result


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def policy_models(policy: dict) -> dict:
    models = policy.get("models")
    if policy.get("schema") != 1 or not isinstance(models, dict) or not 1 <= len(models) <= 32:
        raise PriceError("invalid export policy")
    for model_id, spec in models.items():
        if not MODEL_ID.fullmatch(model_id) or not isinstance(spec, dict):
            raise PriceError("invalid export model")
        if not isinstance(spec.get("api"), str) or not spec["api"]:
            raise PriceError("missing API policy")
        limits = spec.get("ceilings", {})
        if not isinstance(limits, dict):
            raise PriceError("invalid price ceilings")
        for component, bound in limits.items():
            if not isinstance(component, str) or not isinstance(bound, dict) or bound.get("unit") not in UNITS:
                raise PriceError("invalid ceiling unit")
            amount(bound.get("rub"))
    return models


def collect_endpoints(policy: dict, fetch: Callable[[str], dict], *, live: dict | None = None) -> dict:
    """Extend the existing refresh, with a bounded number of public metadata GETs."""
    result = {}
    for model_id in policy_models(policy):
        if live is not None and model_id not in live:
            continue
        url = f"{SOURCE}/{model_id}/endpoints"
        # A transport error aborts publication. A missing model in /models is handled
        # separately by the builder, without promoting cached endpoint prices.
        result[model_id] = fetch(url)
    return result


def _rates(endpoint: dict) -> tuple[dict, dict, bool]:
    pricing, units = endpoint.get("pricing"), endpoint.get("pricing_units")
    if not isinstance(pricing, dict) or not isinstance(units, dict):
        return {}, {}, False
    clean, clean_units, complete = {}, {}, bool(pricing)
    for key, raw in pricing.items():
        if not isinstance(key, str) or len(key) > 64:
            complete = False
            continue
        try:
            # json decoding must use Decimal, never float, including endpoint data.
            if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
                raise PriceError("invalid price type")
            number = Decimal(raw)
            if not number.is_finite() or not -60 <= number.adjusted() <= 18 or len(number.as_tuple().digits) > 60:
                raise PriceError("price magnitude or precision unsupported")
            text = format(number, "f")
            amount(text)
        except (InvalidOperation, PriceError):
            complete = False
            continue
        clean[key] = text
        unit = units.get(key)
        if isinstance(unit, str) and len(unit) <= 32:
            clean_units[key] = unit
        if unit not in UNITS:
            complete = False
    return clean, clean_units, complete


def _model(model_id: str, live: dict | None, payload: dict | None, spec: dict | None) -> dict:
    architecture = live.get("architecture", {}) if isinstance(live, dict) else {}
    out = {
        "id": model_id, "status": "unpriced", "reason": "endpoints-not-checked",
        "architecture": {k: v for k, v in architecture.items() if k in {"input_modalities", "output_modalities"}},
        "catalog_pricing": live.get("pricing", {}) if live else {},
        "catalog_pricing_units": live.get("pricing_units", {}) if live else {},
        "api": spec["api"] if spec else None, "endpoints": [],
        "price_ceilings": spec.get("ceilings", {}) if spec else {},
    }
    if live is None:
        out["reason"] = "model-missing"
        return out
    if not spec:
        return out
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get("id") != model_id:
        raise PriceError("endpoint model identity mismatch")
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) > 64:
        raise PriceError("invalid endpoint list")
    reason = "endpoints-missing" if not endpoints else None
    if any(not isinstance(architecture.get(k), list) or not architecture[k] or not all(isinstance(x, str) for x in architecture[k]) for k in ("input_modalities", "output_modalities")):
        reason = "modalities-not-confirmed"
    tags = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise PriceError("invalid endpoint record")
        tag = endpoint.get("tag")
        if not isinstance(tag, str) or not re.fullmatch(r"[a-z0-9_.-]{1,100}", tag) or tag in tags:
            raise PriceError("invalid or duplicate provider identity")
        tags.add(tag)
        prices, units, complete = _rates(endpoint)
        apis = endpoint.get("supported_apis")
        apis = apis if isinstance(apis, list) and all(isinstance(x, str) for x in apis) else []
        variable = endpoint.get("variable_pricings")
        variable = not isinstance(variable, list) or bool(variable)
        out["endpoints"].append({
            "provider": tag, "pricing": prices, "pricing_units": units,
            "supported_apis": sorted(apis), "variable_pricing": variable,
            "source_url": f"{SOURCE}/{model_id}/endpoints",
        })
        if not complete:
            reason = "incomplete-pricing-units"
        elif variable:
            reason = "variable-pricing"
        elif spec["api"] not in apis:
            reason = "api-not-confirmed"
        for component, price in prices.items():
            ceiling = spec.get("ceilings", {}).get(component)
            if not ceiling or ceiling["unit"] != units.get(component):
                reason = "unapproved-price-component"
            elif amount(price) > amount(ceiling["rub"]):
                # This monetary publication gate is deliberately narrower than #28:
                # no statistical thresholds, watchdogs, alerts or curated model edits.
                raise PriceError("price exceeds reviewed publication ceiling")
    out["endpoints"].sort(key=lambda x: x["provider"])
    out["reason"] = reason
    out["status"] = "available" if reason is None else "unpriced"
    return out


def build_candidate(live: dict, endpoints: dict, policy: dict, *, checked_at: str) -> dict:
    timestamp(checked_at)
    specs = policy_models(policy)
    if not isinstance(live, dict) or not live or len(live) > 10000:
        raise PriceError("invalid model catalog")
    models = {}
    for model_id in sorted(live.keys() | specs.keys()):
        if not MODEL_ID.fullmatch(model_id):
            raise PriceError("invalid model id")
        models[model_id] = _model(model_id, live.get(model_id), endpoints.get(model_id), specs.get(model_id))
    content = {"currency": "RUB", "models": models}
    return {
        "schema": SCHEMA, "owner": OWNER, "publisher": PUBLISHER, "_managed_notice": NOTICE,
        "state": "candidate", "source_url": SOURCE, "source_checked_at": checked_at,
        "snapshot_id": digest(content), "content": content,
    }


def verify(value: dict, *, published: bool) -> dict:
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != SCHEMA:
        raise PriceError("incompatible price schema")
    for key, expected in (("owner", OWNER), ("publisher", PUBLISHER), ("_managed_notice", NOTICE),
                          ("source_url", SOURCE), ("state", "published" if published else "candidate")):
        if value.get(key) != expected:
            raise PriceError("untrusted price identity or state")
    checked = timestamp(value.get("source_checked_at"))
    content = value.get("content")
    if not isinstance(content, dict) or content.get("currency") != "RUB" or digest(content) != value.get("snapshot_id"):
        raise PriceError("currency or content hash mismatch")
    models = content.get("models")
    if not isinstance(models, dict) or not 1 <= len(models) <= 10000:
        raise PriceError("invalid published models")
    for model_id, model in models.items():
        if not MODEL_ID.fullmatch(model_id) or not isinstance(model, dict) or model.get("id") != model_id:
            raise PriceError("invalid published model identity")
        if model.get("status") not in {"available", "unpriced"}:
            raise PriceError("invalid model status")
        available = model["status"] == "available"
        if available and (model.get("reason") is not None or not isinstance(model.get("api"), str) or not model["api"] or not model.get("endpoints")):
            raise PriceError("incomplete available model")
        ceilings = model.get("price_ceilings")
        if not isinstance(ceilings, dict):
            raise PriceError("invalid published ceilings")
        for bound in ceilings.values():
            if not isinstance(bound, dict) or bound.get("unit") not in UNITS:
                raise PriceError("invalid published ceiling unit")
            amount(bound.get("rub"))
        endpoints = model.get("endpoints")
        if not isinstance(endpoints, list) or len(endpoints) > 64:
            raise PriceError("invalid published endpoints")
        providers = set()
        for ep in endpoints:
            if not isinstance(ep, dict) or not isinstance(ep.get("provider"), str) or ep["provider"] in providers:
                raise PriceError("invalid published provider")
            providers.add(ep["provider"])
            pricing, units = ep.get("pricing"), ep.get("pricing_units")
            if not isinstance(pricing, dict) or not isinstance(units, dict):
                raise PriceError("invalid published pricing")
            for key, price in pricing.items():
                amount(price)
                if available:
                    bound = ceilings.get(key)
                    if units.get(key) not in UNITS or not bound or bound["unit"] != units[key] or amount(price) > amount(bound["rub"]):
                        raise PriceError("unknown or unapproved published unit/price")
            apis = ep.get("supported_apis")
            if available and (not pricing or ep.get("variable_pricing") is not False
                              or not isinstance(apis, list) or not all(isinstance(x, str) for x in apis) or model["api"] not in apis):
                raise PriceError("unsafe available endpoint")
            if ep.get("source_url") != f"{SOURCE}/{model_id}/endpoints":
                raise PriceError("untrusted endpoint source")
    if published:
        published_at = timestamp(value.get("published_at"))
        changed_at = timestamp(value.get("content_changed_at"))
        validation = value.get("validation")
        if not isinstance(validation, dict) or validation.get("status") != "success" or validation.get("workflow") != "validate.yml":
            raise PriceError("missing validation evidence")
        if not isinstance(validation.get("candidate_sha"), str) or not SHA.fullmatch(validation["candidate_sha"]):
            raise PriceError("invalid validated SHA")
        if type(validation.get("run_id")) is not int or validation["run_id"] <= 0:
            raise PriceError("invalid validation run")
        if not changed_at <= published_at or not checked <= published_at:
            raise PriceError("inconsistent publication timestamps")
    return value


def approve(candidate: dict, previous: dict | None, *, candidate_sha: str, run_id: int, at: str) -> dict:
    verify(candidate, published=False)
    if not SHA.fullmatch(candidate_sha) or type(run_id) is not int or run_id <= 0:
        raise PriceError("missing exact validation identity")
    if previous is not None:
        verify(previous, published=True)
        for model_id, model in previous["content"]["models"].items():
            if model["status"] == "available" and candidate["content"]["models"].get(model_id, {}).get("status") != "available":
                raise PriceError("previously priced model lost verified coverage")
        if timestamp(candidate["source_checked_at"]) <= timestamp(previous["source_checked_at"]):
            raise PriceError("replayed or older source observation")
    out = {**candidate, "state": "published", "published_at": at, "content_changed_at": at,
           "validation": {"status": "success", "workflow": "validate.yml", "candidate_sha": candidate_sha, "run_id": run_id}}
    if previous and previous["snapshot_id"] == out["snapshot_id"]:
        out["content_changed_at"] = previous["content_changed_at"]
    return verify(out, published=True)
