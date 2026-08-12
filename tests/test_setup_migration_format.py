from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_lib import Reporter, STATE_CONFLICT, sha256_bytes  # noqa: E402
from setup_migration import reconcile_opencode_config  # noqa: E402


class FormatSensitiveJsoncTests(unittest.TestCase):
    def test_managed_jsonc_comments_are_not_reformatted_on_future_semantic_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "opencode.jsonc"
            original = b'''{
  // user comment must survive
  "provider": {
    "routerai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "RouterAI",
      "options": {
        "baseURL": "https://routerai.ru/api/v1",
        "apiKey": "{file:/tmp/key.txt}",
      },
      "models": {},
    },
  },
}
'''
            config.write_bytes(original)
            desired = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {"routerai": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "RouterAI",
                    "options": {
                        "baseURL": "https://routerai.ru/api/v1",
                        "apiKey": "{file:/tmp/key.txt}",
                    },
                    "models": {"new/managed-model": {"name": "Managed"}},
                }},
            }
            desired_data = (json.dumps(desired, indent=2) + "\n").encode("utf-8")
            manifest = {
                "schema": 1,
                "managed_files": {
                    "OpenCode config": {
                        "path": str(config),
                        "sha256": sha256_bytes(original),
                        "source": "test",
                        "mode": "merged-json",
                    }
                },
            }
            reporter = Reporter()
            changed = reconcile_opencode_config(
                destination=config,
                desired_data=desired_data,
                source_label="test",
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=root / "state",
            )
            self.assertFalse(changed)
            self.assertEqual(config.read_bytes(), original)
            self.assertTrue(any(r.state == STATE_CONFLICT for r in reporter.results))
            self.assertTrue(any("formatting loss" in r.detail for r in reporter.results))


if __name__ == "__main__":
    unittest.main()
