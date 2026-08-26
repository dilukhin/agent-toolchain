from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_lib import Reporter, STATE_OK, sha256_bytes  # noqa: E402
from setup_migration import reconcile_opencode_config  # noqa: E402


class SiblingProviderIdempotencyTests(unittest.TestCase):
    def test_clean_sibling_provider_is_up_to_date_in_check_and_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            state_dir = root / "state"
            source_label = "opencode_setup:managed-merge:templates/opencode.jsonc"
            key_ref = "{file:/tmp/routerai-key.txt}"

            existing = {
                "$schema": "https://opencode.ai/config.json",
                "permission": {"bash": "ask"},
                "provider": {
                    "qwen": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": "https://portal.qwen.ai/v1"},
                        "models": {},
                    },
                    "routerai": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "RouterAI",
                        "options": {
                            "baseURL": "https://routerai.ru/api/v1",
                            "apiKey": key_ref,
                        },
                        "models": {},
                    },
                },
                "autoupdate": "notify",
            }
            current_data = (json.dumps(existing, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            destination.write_bytes(current_data)

            desired = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "routerai": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "RouterAI",
                        "options": {
                            "baseURL": "https://routerai.ru/api/v1",
                            "apiKey": key_ref,
                        },
                        "models": {},
                    }
                },
                "model": "routerai/example",
                "small_model": "routerai/example-small",
                "autoupdate": "notify",
            }
            desired_data = (json.dumps(desired, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            manifest = {
                "managed_files": {
                    "OpenCode config": {
                        "path": str(destination),
                        "sha256": sha256_bytes(current_data),
                        "source": source_label,
                        "mode": "merged-json-sibling-provider",
                    }
                }
            }
            stable_manifest = json.loads(json.dumps(manifest))

            check_reporter = Reporter()
            check_changed = reconcile_opencode_config(
                destination=destination,
                desired_data=desired_data,
                source_label=source_label,
                manifest=manifest,
                reporter=check_reporter,
                check=True,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(check_changed)
            check_row = next(row for row in check_reporter.results if row.component == "OpenCode config")
            self.assertEqual(check_row.state, STATE_OK)
            self.assertEqual(manifest, stable_manifest)
            self.assertEqual(destination.read_bytes(), current_data)

            apply_reporter = Reporter()
            apply_changed = reconcile_opencode_config(
                destination=destination,
                desired_data=desired_data,
                source_label=source_label,
                manifest=manifest,
                reporter=apply_reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(apply_changed)
            apply_row = next(row for row in apply_reporter.results if row.component == "OpenCode config")
            self.assertEqual(apply_row.state, STATE_OK)
            self.assertEqual(manifest, stable_manifest)
            self.assertEqual(destination.read_bytes(), current_data)


if __name__ == "__main__":
    unittest.main()
