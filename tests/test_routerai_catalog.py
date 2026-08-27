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
        policy = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "display_name": "Qwen 3.6 Plus",
                    "role": "основная",
                    "description": "Основная",
                    "legacy_names": ["Qwen 3.6 Plus"],
                }
            }
        }
        previous_snapshot = {"managed_names": {"qwen/qwen3.6-plus": ["Qwen 3.6 Plus [основная, 30/180 ₽]"]}}
        config = {
            "models": {
                "qwen/qwen3.6-plus": {
                    "name": "Qwen 3.6 Plus [основная, 30/180 ₽]",
                    "description": "old",
                    "price_input_rub_per_1m": 30,
                    "price_output_rub_per_1m": 180,
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
        self.assertEqual(spec["name"], "Qwen 3.6 Plus [основная, 31/180 ₽]")
        self.assertEqual(
            generated_template["provider"]["routerai"]["models"]["qwen/qwen3.6-plus"]["name"],
            spec["name"],
        )
        self.assertIn("vendor/new-model", snapshot["models"])
        self.assertIn("Qwen 3.6 Plus [основная, 30/180 ₽]", snapshot["managed_names"]["qwen/qwen3.6-plus"])

    def test_missing_policy_model_preserves_previous_config(self) -> None:
        payload = {"data": [{"id": "other/model", "pricing": {"prompt": "0.1", "completion": "0.2"}}]}
        policy = {
            "models": {
                "missing/model": {
                    "display_name": "Missing",
                    "role": "роль",
                    "description": "Описание",
                    "legacy_names": ["Missing"],
                }
            }
        }
        config = {"models": {"missing/model": {"name": "Missing [роль, 1/2 ₽]", "price_input_rub_per_1m": 1}}}
        template = {"provider": {"routerai": {"models": {}}}}
        snapshot, generated_config, _template, missing = catalog.build_outputs(
            payload, policy, {"managed_names": {}}, config, template, observed_at="2026-08-27T00:00:00Z"
        )
        self.assertEqual(missing, ["missing/model"])
        self.assertEqual(generated_config["models"]["missing/model"]["name"], "Missing [роль, 1/2 ₽]")
        self.assertIn("other/model", snapshot["models"])


if __name__ == "__main__":
    unittest.main()
