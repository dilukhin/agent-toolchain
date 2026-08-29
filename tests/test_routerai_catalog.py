from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_routerai_catalog.py"
SPEC = importlib.util.spec_from_file_location("update_routerai_catalog", SCRIPT)
assert SPEC and SPEC.loader
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class RouterAiCatalogTests(unittest.TestCase):
    def _policy(self, model_id: str = "qwen/qwen3.6-plus", *, role: str | None = None) -> dict:
        is_qwen = model_id.startswith("qwen/")
        return {
            "models": {
                model_id: {
                    "display_name": "Qwen 3.6 Plus" if is_qwen else "Missing",
                    "role": role or ("основная" if is_qwen else "роль"),
                    "description": "Основная" if is_qwen else "Описание",
                    "legacy_names": ["Qwen 3.6 Plus" if is_qwen else "Missing"],
                }
            }
        }

    def _payload(self) -> dict:
        return {
            "data": [{
                "id": "qwen/qwen3.6-plus",
                "name": "Qwen 3.6 Plus",
                "context_length": 262144,
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {
                    "prompt": "0.0000314",
                    "completion": "0.0001804",
                    "input_cache_read": "0.0000054",
                },
            }]
        }

    def _template(self) -> dict:
        return {"provider": {"routerai": {"models": {}}}}

    def test_normalize_and_generate_price_label(self) -> None:
        payload = self._payload()
        payload["data"].append({
            "id": "vendor/new-model",
            "name": "New Model",
            "pricing": {"prompt": "0.00001", "completion": "0.00002"},
        })
        previous_snapshot = {
            "managed_names": {
                "qwen/qwen3.6-plus": ["Qwen 3.6 Plus [основная, 30/180 ₽]"]
            }
        }
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "Qwen 3.6 Plus [основная, 30/180 ₽]",
                    "description": "old",
                    "price_input_rub_per_1m": 30,
                    "price_output_rub_per_1m": 180,
                    "cache_read_rub_per_1m": 4,
                    "price_cache_read_rub_per_1m": 5,
                }
            }
        }
        snapshot, generated_config, generated_template, missing = catalog.build_outputs(
            payload, self._policy(), previous_snapshot, config, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        self.assertFalse(missing)
        spec = generated_config["models"]["qwen/qwen3.6-plus"]
        self.assertEqual(spec["price_input_rub_per_1m"], 31)
        self.assertEqual(spec["price_output_rub_per_1m"], 180)
        self.assertEqual(spec["cache_read_rub_per_1m"], 5)
        self.assertNotIn("price_cache_read_rub_per_1m", spec)
        self.assertEqual(spec["name"], "Qwen 3.6 Plus [основная, 31/180 ₽]")
        self.assertEqual(
            generated_template["provider"]["routerai"]["models"]["qwen/qwen3.6-plus"]["name"],
            spec["name"],
        )
        self.assertIn("vendor/new-model", snapshot["models"])
        self.assertIn("_managed_notice", snapshot)
        self.assertIn("_managed_notice", generated_config)
        self.assertIn(catalog.STATUS_DOC, snapshot["_managed_notice"])
        self.assertIn(catalog.OFFLINE_SYNC_COMMAND, generated_config["_managed_notice"]["models"])
        self.assertIn(catalog.MANUAL_REFRESH_COMMAND, generated_config["_managed_notice"]["models"])

    def test_missing_policy_model_keeps_model_but_drops_stale_price(self) -> None:
        payload = {"data": [{"id": "other/model", "pricing": {"prompt": "0.1", "completion": "0.2"}}]}
        config = {
            "models": {
                "missing/model": {
                    "name": "Missing [роль, 1/2 ₽]",
                    "price_input_rub_per_1m": 1,
                    "price_output_rub_per_1m": 2,
                    "cache_read_rub_per_1m": 1,
                }
            }
        }
        snapshot, generated_config, generated_template, missing = catalog.build_outputs(
            payload, self._policy("missing/model"), {"managed_names": {}}, config,
            self._template(), observed_at="2026-08-27T00:00:00Z",
        )
        self.assertEqual(missing, ["missing/model"])
        spec = generated_config["models"]["missing/model"]
        self.assertEqual(spec["name"], "Missing [роль, цена недоступна]")
        self.assertNotIn("price_input_rub_per_1m", spec)
        self.assertNotIn("price_output_rub_per_1m", spec)
        self.assertNotIn("cache_read_rub_per_1m", spec)
        self.assertIn("missing/model", generated_template["provider"]["routerai"]["models"])
        self.assertIn("other/model", snapshot["models"])

    def test_incomplete_live_pricing_does_not_keep_stale_price(self) -> None:
        payload = {"data": [{"id": "qwen/qwen3.6-plus", "pricing": {"prompt": "0.00003"}}]}
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "Qwen 3.6 Plus [основная, 30/180 ₽]",
                    "price_input_rub_per_1m": 30,
                    "price_output_rub_per_1m": 180,
                    "cache_read_rub_per_1m": 4,
                }
            }
        }
        snapshot, generated_config, _template, missing = catalog.build_outputs(
            payload, self._policy(), {"managed_names": {}}, config, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        self.assertFalse(missing)
        spec = generated_config["models"]["qwen/qwen3.6-plus"]
        self.assertEqual(spec["name"], "Qwen 3.6 Plus [основная, цена недоступна]")
        self.assertNotIn("price_input_rub_per_1m", spec)
        self.assertNotIn("price_output_rub_per_1m", spec)
        self.assertNotIn("cache_read_rub_per_1m", spec)
        self.assertEqual(snapshot["models"]["qwen/qwen3.6-plus"]["pricing"]["prompt"], "0.00003")

    def test_generated_models_do_not_preserve_manual_unknown_fields(self) -> None:
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "manual",
                    "description": "manual",
                    "unexpected_manual_field": "must not survive",
                }
            }
        }
        _snapshot, generated_config, _template, _missing = catalog.build_outputs(
            self._payload(), self._policy(), {"managed_names": {}}, config, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        self.assertNotIn("unexpected_manual_field", generated_config["models"]["qwen/qwen3.6-plus"])

    def test_offline_verification_detects_manual_edits(self) -> None:
        policy = self._policy()
        snapshot, generated_config, generated_template, _missing = catalog.build_outputs(
            self._payload(), policy, {"managed_names": {}}, {"models": {}}, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        self.assertEqual(catalog.verify_generated_state(policy, snapshot, generated_config, generated_template), [])
        generated_config["models"]["qwen/qwen3.6-plus"]["price_input_rub_per_1m"] = 999
        self.assertIn(
            "config_data.json -> models",
            catalog.verify_generated_state(policy, snapshot, generated_config, generated_template),
        )
        generated_config["models"]["qwen/qwen3.6-plus"]["price_input_rub_per_1m"] = 31
        generated_template["provider"]["routerai"]["models"]["qwen/qwen3.6-plus"]["name"] = "manual"
        self.assertIn(
            "templates/opencode.jsonc -> provider.routerai.models",
            catalog.verify_generated_state(policy, snapshot, generated_config, generated_template),
        )

    def test_offline_policy_sync_changes_derived_sections_but_not_snapshot(self) -> None:
        base_policy = self._policy(role="основная")
        snapshot, config, template, _missing = catalog.build_outputs(
            self._payload(), base_policy, {"managed_names": {}}, {"models": {}}, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        snapshot_before = json.loads(json.dumps(snapshot, ensure_ascii=False))
        new_policy = self._policy(role="архитектор")
        new_config, new_template, missing = catalog.build_generated_from_snapshot(
            new_policy, snapshot, config, template
        )
        self.assertFalse(missing)
        self.assertEqual(snapshot, snapshot_before)
        self.assertIn("[архитектор, 31/180 ₽]", new_config["models"]["qwen/qwen3.6-plus"]["name"])
        self.assertEqual(
            new_template["provider"]["routerai"]["models"]["qwen/qwen3.6-plus"]["name"],
            new_config["models"]["qwen/qwen3.6-plus"]["name"],
        )
        self.assertEqual(catalog.verify_generated_state(new_policy, snapshot, new_config, new_template), [])

    def test_sync_generated_cli_does_not_touch_snapshot(self) -> None:
        policy = self._policy(role="основная")
        snapshot, config, template, _missing = catalog.build_outputs(
            self._payload(), policy, {"managed_names": {}}, {"models": {}}, self._template(),
            observed_at="2026-08-27T00:00:00Z",
        )
        policy = self._policy(role="архитектор")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paths = {
                "POLICY_PATH": root / "policy.json",
                "SNAPSHOT_PATH": root / "snapshot.json",
                "CONFIG_PATH": root / "config.json",
                "TEMPLATE_PATH": root / "template.json",
            }
            paths["POLICY_PATH"].write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
            paths["SNAPSHOT_PATH"].write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            paths["CONFIG_PATH"].write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            paths["TEMPLATE_PATH"].write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
            before = paths["SNAPSHOT_PATH"].read_bytes()
            with mock.patch.multiple(catalog, **paths):
                self.assertEqual(catalog.main(["--sync-generated"]), 0)
            self.assertEqual(paths["SNAPSHOT_PATH"].read_bytes(), before)
            synced = json.loads(paths["CONFIG_PATH"].read_text(encoding="utf-8"))
            self.assertIn("[архитектор, 31/180 ₽]", synced["models"]["qwen/qwen3.6-plus"]["name"])


if __name__ == "__main__":
    unittest.main()
