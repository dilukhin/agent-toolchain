from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_routerai_owned_diff.py"
SPEC = importlib.util.spec_from_file_location("check_routerai_owned_diff", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class RouterAiOwnedDiffTests(unittest.TestCase):
    def _config(self) -> dict:
        return {
            "models": {"m": {"name": "generated"}},
            "managed_environment": {"x": 1},
        }

    def _template(self) -> dict:
        return {"provider": {"routerai": {"models": {"m": {"name": "generated"}}}}}

    def test_unrelated_config_change_is_allowed(self) -> None:
        base = self._config()
        head = self._config()
        head["managed_environment"] = {"x": 2}
        self.assertEqual(
            guard.owned_section_violations(
                base_config=base,
                head_config=head,
                base_template=self._template(),
                head_template=self._template(),
                base_snapshot=b"same",
                head_snapshot=b"same",
            ),
            [],
        )

    def test_config_models_change_is_rejected(self) -> None:
        base = self._config()
        head = self._config()
        head["models"]["m"]["name"] = "manual"
        violations = guard.owned_section_violations(
            base_config=base,
            head_config=head,
            base_template=self._template(),
            head_template=self._template(),
            base_snapshot=b"same",
            head_snapshot=b"same",
        )
        self.assertIn("config_data.json -> models", violations)

    def test_template_models_change_is_rejected(self) -> None:
        base_template = self._template()
        head_template = self._template()
        head_template["provider"]["routerai"]["models"]["m"]["name"] = "manual"
        violations = guard.owned_section_violations(
            base_config=self._config(),
            head_config=self._config(),
            base_template=base_template,
            head_template=head_template,
            base_snapshot=b"same",
            head_snapshot=b"same",
        )
        self.assertIn("templates/opencode.jsonc -> provider.routerai.models", violations)

    def test_snapshot_change_is_rejected(self) -> None:
        violations = guard.owned_section_violations(
            base_config=self._config(),
            head_config=self._config(),
            base_template=self._template(),
            head_template=self._template(),
            base_snapshot=b"old",
            head_snapshot=b"new",
        )
        self.assertIn("templates/routerai_catalog.generated.json -> весь файл", violations)


if __name__ == "__main__":
    unittest.main()
