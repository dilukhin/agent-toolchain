from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_routerai_catalog.py"
SPEC = importlib.util.spec_from_file_location("update_routerai_catalog", SCRIPT)
assert SPEC and SPEC.loader
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class RouterAiCatalogTests(unittest.TestCase):
    def _policy(self, model_id: str = "qwen/qwen3.6-plus") -> dict:
        return {
            "models": {
                model_id: {
                    "display_name": "Qwen 3.6 Plus" if model_id.startswith("qwen/") else "Missing",
                    "role": "основная" if model_id.startswith("qwen/") else "роль",
                    "description": "Основная" if model_id.startswith("qwen/") else "Описание",
                    "legacy_names": ["Qwen 3.6 Plus" if model_id.startswith("qwen/") else "Missing"],
                }
            }
        }

    def test_normalize_and_generate_price_label(self) -> None:
        payload = {
            "data": [
                {
                    "id": "qwen/qwen3.6-plus",
                    "name": "Qwen 3.6 Plus",
                    "context_length": 262144,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "0.0000314", "completion": "0.0001804"},
                },
                {
                    "id": "vendor/new-model",
                    "name": "New Model",
                    "pricing": {"prompt": "0.00001", "completion": "0.00002"},
                },
            ]
        }
        policy = self._policy()
        previous_snapshot = {"managed_names": {"qwen/qwen3.6-plus": ["Qwen 3.6 Plus [основная, 30/180 ₽]"]}}
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "Qwen 3.6 Plus [основная, 30/180 ₽]",
                    "description": "old",
                    "price_input_rub_per_1m": 30,
                    "price_output_rub_per_1m": 180,
                    "price_cache_read_rub_per_1m": 5,
                }
            }
        }
        template = {"provider": {"routerai": {"models": {}}}}
        snapshot, generated_config, generated_template, missing = catalog.build_outputs(
            payload, policy, previous_snapshot, config, template, observed_at="2026-08-27T00:00:00Z"
        )
        self.assertFalse(missing)
        spec = generated_config["models"]["qwen/qwen3.6-plus"]
        self.assertEqual(spec["price_input_rub_per_1m"], 31)
        self.assertEqual(spec["price_output_rub_per_1m"], 180)
        self.assertNotIn("price_cache_read_rub_per_1m", spec)
        self.assertEqual(spec["name"], "Qwen 3.6 Plus [основная, 31/180 ₽]")
        self.assertEqual(
            generated_template["provider"]["routerai"]["models"]["qwen/qwen3.6-plus"]["name"],
            spec["name"],
        )
        self.assertIn("vendor/new-model", snapshot["models"])
        self.assertIn("Qwen 3.6 Plus [основная, 30/180 ₽]", snapshot["managed_names"]["qwen/qwen3.6-plus"])

    def test_missing_policy_model_keeps_model_but_drops_stale_price(self) -> None:
        payload = {"data": [{"id": "other/model", "pricing": {"prompt": "0.1", "completion": "0.2"}}]}
        policy = self._policy("missing/model")
        config = {
            "models": {
                "missing/model": {
                    "name": "Missing [роль, 1/2 ₽]",
                    "price_input_rub_per_1m": 1,
                    "price_output_rub_per_1m": 2,
                }
            }
        }
        template = {"provider": {"routerai": {"models": {}}}}
        snapshot, generated_config, generated_template, missing = catalog.build_outputs(
            payload, policy, {"managed_names": {}}, config, template, observed_at="2026-08-27T00:00:00Z"
        )
        self.assertEqual(missing, ["missing/model"])
        spec = generated_config["models"]["missing/model"]
        self.assertEqual(spec["name"], "Missing [роль, цена недоступна]")
        self.assertNotIn("price_input_rub_per_1m", spec)
        self.assertNotIn("price_output_rub_per_1m", spec)
        self.assertIn("missing/model", generated_template["provider"]["routerai"]["models"])
        self.assertIn("other/model", snapshot["models"])

    def test_incomplete_live_pricing_does_not_keep_stale_price(self) -> None:
        payload = {
            "data": [
                {
                    "id": "qwen/qwen3.6-plus",
                    "pricing": {"prompt": "0.00003"},
                }
            ]
        }
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "Qwen 3.6 Plus [основная, 30/180 ₽]",
                    "price_input_rub_per_1m": 30,
                    "price_output_rub_per_1m": 180,
                }
            }
        }
        snapshot, generated_config, _template, missing = catalog.build_outputs(
            payload,
            self._policy(),
            {"managed_names": {}},
            config,
            {"provider": {"routerai": {"models": {}}}},
            observed_at="2026-08-27T00:00:00Z",
        )
        self.assertFalse(missing)
        spec = generated_config["models"]["qwen/qwen3.6-plus"]
        self.assertEqual(spec["name"], "Qwen 3.6 Plus [основная, цена недоступна]")
        self.assertNotIn("price_input_rub_per_1m", spec)
        self.assertNotIn("price_output_rub_per_1m", spec)
        self.assertEqual(snapshot["models"]["qwen/qwen3.6-plus"]["pricing"]["prompt"], "0.00003")


if __name__ == "__main__":
    unittest.main()
