from __future__ import annotations

import base64
import copy
import importlib.util
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import routerai_prices as prices
from scripts import publish_routerai_prices as publisher
from scripts import update_routerai_catalog as catalog

CHECKED = "2026-10-28T12:00:00Z"
AT = "2026-10-28T12:01:00Z"
SHA = "a" * 40


def fixture():
    live = {"synthetic/asr": {"architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]}, "pricing": {"seconds": "0.00012345678901234567890123456789"}, "pricing_units": {"seconds": "second"}}}
    endpoint = {"tag": "test-provider", "pricing": {"seconds": "0.00012345678901234567890123456789"}, "pricing_units": {"seconds": "second"}, "supported_apis": ["audio/transcriptions"], "variable_pricings": []}
    endpoints = {"synthetic/asr": {"data": {"id": "synthetic/asr", "endpoints": [endpoint]}}}
    policy = {"schema": 1, "models": {"synthetic/asr": {"api": "audio/transcriptions", "ceilings": {"seconds": {"unit": "second", "rub": "0.01"}}}}}
    return live, endpoints, policy


class FakeAPI:
    def __init__(self, previous=None):
        self.calls = []
        self.candidate = SHA
        self.price_head = "old" if previous else None
        self.previous = previous
        self.blobs, self.trees, self.commits = {}, {}, {}
        if previous:
            self.blobs["old-blob"] = json.dumps(previous)
            self.trees["old-tree"] = [{"path": prices.FILE, "mode": "100644", "type": "blob", "sha": "old-blob"}]
            self.commits["old"] = {"tree": "old-tree"}
        self.race = None
        self.validation_sha = SHA
        self.fail_ref = False
        self.bad_tree = False
        self.job_conclusion = "success"

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "actions/workflows/validate.yml":
            return {"id": 1}
        if path == "actions/runs/7":
            return {"workflow_id": 1, "head_sha": self.validation_sha, "status": "completed", "conclusion": "success"}
        if path.startswith("actions/runs/7/jobs"):
            return {"total_count": 2, "jobs": [{"name": name, "status": "completed", "conclusion": self.job_conclusion} for name in ("windows", "linux")]}
        if path == "git/ref/heads/automation/routerai-catalog":
            return {"object": {"sha": self.candidate}}
        if method == "GET" and path == f"git/ref/heads/{prices.BRANCH}":
            if not self.price_head:
                raise publisher.Missing()
            return {"object": {"sha": self.price_head}}
        if method == "GET" and path.startswith("git/commits/"):
            return {"tree": {"sha": self.commits[path.split('/')[-1]]["tree"]}}
        if method == "GET" and path.startswith("git/trees/"):
            entries = self.trees[path.split('/')[-1].split('?')[0]]
            return {"tree": entries + ([{"path": "foreign.txt"}] if self.bad_tree else []), "truncated": False}
        if method == "GET" and path.startswith("git/blobs/"):
            raw = self.blobs[path.split('/')[-1]].encode()
            return {"content": base64.b64encode(raw).decode(), "encoding": "base64", "size": len(raw)}
        if method == "POST" and path == "git/blobs":
            self.blobs["new-blob"] = body["content"]
            return {"sha": "new-blob"}
        if method == "POST" and path == "git/trees":
            self.trees["new-tree"] = body["tree"]
            return {"sha": "new-tree"}
        if method == "POST" and path == "git/commits":
            self.commits["new"] = body
            if self.race == "candidate":
                self.candidate = "b" * 40
            if self.race == "prices":
                self.price_head = "foreign"
            return {"sha": "new"}
        if method in {"POST", "PATCH"} and (path == "git/refs" or path.startswith("git/refs/heads/")):
            if self.fail_ref:
                raise prices.PriceError("synthetic ref conflict")
            if method == "PATCH":
                assert body["force"] is False
                assert self.commits[body["sha"]]["parents"] == [self.price_head]
            self.price_head = body["sha"]
            return {}
        raise AssertionError((method, path))


class ContractTests(unittest.TestCase):
    def candidate(self):
        return prices.build_candidate(*fixture(), checked_at=CHECKED)

    def test_decimal_units_and_multi_provider_max_inputs_survive(self):
        live, endpoints, policy = fixture()
        second = copy.deepcopy(endpoints["synthetic/asr"]["data"]["endpoints"][0])
        second["tag"] = "expensive"
        second["pricing"]["seconds"] = "0.001"
        endpoints["synthetic/asr"]["data"]["endpoints"].append(second)
        value = prices.build_candidate(live, endpoints, policy, checked_at=CHECKED)
        record = value["content"]["models"]["synthetic/asr"]
        self.assertEqual(record["status"], "available")
        self.assertEqual(record["endpoints"][1]["pricing"]["seconds"], "0.00012345678901234567890123456789")
        prices.verify(value, published=False)

    def test_unit_is_not_guessed_from_price_key(self):
        for unit in (None, "minute", "request", "token"):
            live, endpoints, policy = fixture()
            endpoints["synthetic/asr"]["data"]["endpoints"][0]["pricing_units"]["seconds"] = unit
            value = prices.build_candidate(live, endpoints, policy, checked_at=CHECKED)
            self.assertEqual(value["content"]["models"]["synthetic/asr"]["status"], "unpriced")

    def test_token_and_request_prices(self):
        for key, unit in (("prompt", "token"), ("operation", "request")):
            live, endpoints, policy = fixture()
            ep = endpoints["synthetic/asr"]["data"]["endpoints"][0]
            ep["pricing"], ep["pricing_units"] = {key: Decimal("0.0000123456789")}, {key: unit}
            policy["models"]["synthetic/asr"]["ceilings"] = {key: {"unit": unit, "rub": "1"}}
            value = prices.build_candidate(live, endpoints, policy, checked_at=CHECKED)
            self.assertEqual(value["content"]["models"]["synthetic/asr"]["status"], "available")

    def test_unknown_api_variable_missing_model_fail_closed(self):
        for mutation in ("api", "variable", "missing", "unknown-component", "negative"):
            live, endpoints, policy = fixture()
            ep = endpoints["synthetic/asr"]["data"]["endpoints"][0]
            if mutation == "api": ep["supported_apis"] = []
            elif mutation == "variable": ep["variable_pricings"] = [{"threshold": 10}]
            elif mutation == "missing": live = {"synthetic/other": {}}
            elif mutation == "unknown-component": ep["pricing"]["extra"] = "0.1"
            else: ep["pricing"]["seconds"] = "-1"
            value = prices.build_candidate(live, endpoints, policy, checked_at=CHECKED)
            self.assertEqual(value["content"]["models"]["synthetic/asr"]["status"], "unpriced")

    def test_spike_is_rejected_without_changing_previous(self):
        live, endpoints, policy = fixture()
        endpoints["synthetic/asr"]["data"]["endpoints"][0]["pricing"]["seconds"] = "100"
        with self.assertRaises(prices.PriceError):
            prices.build_candidate(live, endpoints, policy, checked_at=CHECKED)

    def test_normalizer_keeps_units_and_rejects_nonfinite_negative_float_bool(self):
        raw = {"data": [{"id": "synthetic/asr", "pricing": {"s": "0.00000000000000000001", "negative": "-1", "nan": "NaN", "inf": "Infinity", "bool": True, "float": 0.1}, "pricing_units": {"s": "second"}}]}
        record = catalog.normalize_catalog(raw)["synthetic/asr"]
        self.assertEqual(record["pricing"], {"s": "0.00000000000000000001"})
        self.assertEqual(record["pricing_units"], {"s": "second"})
        self.assertTrue(record["pricing_incomplete"])

    def test_checked_heartbeat_is_bound_to_same_hash(self):
        first = prices.approve(self.candidate(), None, candidate_sha=SHA, run_id=7, at=AT)
        candidate = self.candidate()
        candidate["source_checked_at"] = "2026-10-29T12:00:00Z"
        second = prices.approve(candidate, first, candidate_sha=SHA, run_id=7, at="2026-10-29T12:01:00Z")
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["content_changed_at"], second["content_changed_at"])
        self.assertNotEqual(first["source_checked_at"], second["source_checked_at"])
        with self.assertRaises(prices.PriceError):
            prices.approve(self.candidate(), second, candidate_sha=SHA, run_id=7, at=AT)

    def test_foreign_candidate_and_hash_are_rejected(self):
        for field, value in (("state", "published"), ("owner", "foreign"), ("snapshot_id", "sha256:wrong"), ("schema", True)):
            candidate = self.candidate()
            candidate[field] = value
            with self.assertRaises(prices.PriceError): prices.verify(candidate, published=False)

    def test_collect_is_bounded_and_skips_absent_models(self):
        live, endpoints, policy = fixture()
        calls = []
        result = prices.collect_endpoints(policy, lambda url: calls.append(url) or {}, live={})
        self.assertEqual((result, calls), ({}, []))
        bad = copy.deepcopy(policy)
        bad["models"]["../evil"] = bad["models"].pop("synthetic/asr")
        with self.assertRaises(prices.PriceError): prices.collect_endpoints(bad, lambda url: {})

    def test_oversize_policy_and_invalid_timestamp(self):
        with self.assertRaises(prices.PriceError): prices.timestamp("2026-99-28T12:00:00Z")
        for raw in (True, 0.1, "-1", "NaN", "Infinity", "1e6"):
            with self.assertRaises(prices.PriceError): prices.amount(raw)

    def test_publish_and_read_back_orphan(self):
        api = FakeAPI()
        head, document = publisher.publish(api, self.candidate(), sha=SHA, run_id=7, at=AT)
        self.assertEqual(head, "new")
        self.assertEqual(document["state"], "published")
        self.assertEqual(api.commits["new"]["parents"], [])

    def test_publish_fast_forward_and_partial_retry(self):
        previous = prices.approve(self.candidate(), None, candidate_sha=SHA, run_id=7, at=AT)
        candidate = self.candidate()
        candidate["source_checked_at"] = "2026-10-29T12:00:00Z"
        api = FakeAPI(previous)
        api.fail_ref = True
        with self.assertRaises(prices.PriceError): publisher.publish(api, candidate, sha=SHA, run_id=7, at="2026-10-29T12:01:00Z")
        self.assertEqual(api.price_head, "old")
        api.fail_ref = False
        publisher.publish(api, candidate, sha=SHA, run_id=7, at="2026-10-29T12:01:00Z")
        self.assertEqual(api.price_head, "new")

    def test_races_validation_and_foreign_tree_prevent_mutable_write(self):
        for condition in ("candidate", "prices", "bad-sha", "jobs-skipped", "foreign-tree"):
            previous = prices.approve(self.candidate(), None, candidate_sha=SHA, run_id=7, at=AT)
            candidate = self.candidate()
            candidate["source_checked_at"] = "2026-10-29T12:00:00Z"
            api = FakeAPI(previous)
            if condition in ("candidate", "prices"): api.race = condition
            elif condition == "bad-sha": api.validation_sha = "b" * 40
            elif condition == "jobs-skipped": api.job_conclusion = "skipped"
            else: api.bad_tree = True
            with self.subTest(condition=condition), self.assertRaises(prices.PriceError):
                publisher.publish(api, candidate, sha=SHA, run_id=7, at="2026-10-29T12:01:00Z")
            self.assertFalse(any(method in {"POST", "PATCH"} and path.startswith("git/refs") for method, path, _ in api.calls))

    def test_retry_after_published_ref_is_idempotent(self):
        candidate = self.candidate()
        previous = prices.approve(candidate, None, candidate_sha=SHA, run_id=7, at=AT)
        api = FakeAPI(previous)
        head, actual = publisher.publish(api, candidate, sha=SHA, run_id=7, at="2026-10-28T12:02:00Z")
        self.assertEqual((head, actual), ("old", previous))
        self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in api.calls))

    def test_exponent_bomb_and_duplicate_json_cannot_be_used(self):
        payload = {"data": [{"id": "synthetic/asr", "pricing": {"seconds": "1e999999999"}}]}
        record = catalog.normalize_catalog(payload)["synthetic/asr"]
        self.assertNotIn("pricing", record)
        self.assertTrue(record["pricing_incomplete"])
        with self.assertRaises(catalog.CatalogError):
            catalog._unique_object([("pricing", {}), ("pricing", {})])

    def test_loss_of_previously_verified_price_preserves_last_good(self):
        candidate = self.candidate()
        previous = prices.approve(candidate, None, candidate_sha=SHA, run_id=7, at=AT)
        live, endpoints, policy = fixture()
        endpoints["synthetic/asr"]["data"]["endpoints"][0]["pricing_units"] = {}
        unavailable = prices.build_candidate(live, endpoints, policy, checked_at="2026-10-29T12:00:00Z")
        self.assertEqual(unavailable["content"]["models"]["synthetic/asr"]["status"], "unpriced")
        api = FakeAPI(previous)
        with self.assertRaises(prices.PriceError):
            publisher.publish(api, unavailable, sha=SHA, run_id=7, at="2026-10-29T12:01:00Z")
        self.assertEqual(api.price_head, "old")
        self.assertFalse(any(method in {"POST", "PATCH"} and path.startswith("git/refs") for method, path, _ in api.calls))

    def test_fixture_matches_documented_contract(self):
        path = ROOT / "tests/fixtures/routerai_prices_published.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document, prices.approve(self.candidate(), None, candidate_sha=SHA, run_id=7, at=AT))


if __name__ == "__main__":
    unittest.main()
