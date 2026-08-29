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
    def _policy(self, role: str = "основная") -> dict:
        return {
            "models": {
                "m": {
                    "display_name": "M",
                    "role": role,
                    "description": "Описание",
                    "legacy_names": ["M"],
                }
            }
        }

    def _snapshot(self) -> dict:
        return {
            "schema": 1,
            "source_state": "live",
            "observed_at": "2026-08-27T00:00:00Z",
            "models": {"m": {"pricing": {"prompt": "0.000001", "completion": "0.000002"}}},
            "managed_names": {},
        }

    def _state(self, policy: dict | None = None):
        policy = policy or self._policy()
        snapshot = self._snapshot()
        config, template, _missing = guard.catalog.build_generated_from_snapshot(
            policy,
            snapshot,
            {"models": {}},
            {"provider": {"routerai": {"models": {}}}},
        )
        return policy, snapshot, config, template

    def test_unrelated_config_change_is_allowed(self) -> None:
        policy, snapshot, config, template = self._state()
        base_config = {**config, "managed_environment": {"x": 1}}
        head_config = {**config, "managed_environment": {"x": 2}}
        self.assertEqual(
            guard.owned_section_violations(
                base_policy=policy, head_policy=policy,
                base_config=base_config, head_config=head_config,
                base_template=template, head_template=template,
                base_snapshot=snapshot, head_snapshot=snapshot,
            ),
            [],
        )

    def test_manual_generated_change_without_policy_change_is_rejected(self) -> None:
        policy, snapshot, config, template = self._state()
        head_config = {**config, "models": {"m": {**config["models"]["m"], "name": "manual"}}}
        violations = guard.owned_section_violations(
            base_policy=policy, head_policy=policy,
            base_config=config, head_config=head_config,
            base_template=template, head_template=template,
            base_snapshot=snapshot, head_snapshot=snapshot,
        )
        self.assertIn("config_data.json -> models", violations)
        self.assertTrue(any("без изменения" in item for item in violations))

    def test_policy_change_with_exact_offline_generated_state_is_allowed(self) -> None:
        base_policy, snapshot, base_config, base_template = self._state(self._policy("основная"))
        head_policy = self._policy("архитектор")
        head_config, head_template, _missing = guard.catalog.build_generated_from_snapshot(
            head_policy, snapshot, base_config, base_template
        )
        self.assertEqual(
            guard.owned_section_violations(
                base_policy=base_policy, head_policy=head_policy,
                base_config=base_config, head_config=head_config,
                base_template=base_template, head_template=head_template,
                base_snapshot=snapshot, head_snapshot=snapshot,
            ),
            [],
        )

    def test_policy_change_without_sync_is_rejected(self) -> None:
        base_policy, snapshot, config, template = self._state(self._policy("основная"))
        violations = guard.owned_section_violations(
            base_policy=base_policy, head_policy=self._policy("архитектор"),
            base_config=config, head_config=config,
            base_template=template, head_template=template,
            base_snapshot=snapshot, head_snapshot=snapshot,
        )
        self.assertIn("config_data.json -> models", violations)

    def test_snapshot_change_is_always_rejected_for_ordinary_pr(self) -> None:
        policy, snapshot, config, template = self._state()
        changed_snapshot = {**snapshot, "observed_at": "2026-08-28T00:00:00Z"}
        violations = guard.owned_section_violations(
            base_policy=policy, head_policy=policy,
            base_config=config, head_config=config,
            base_template=template, head_template=template,
            base_snapshot=snapshot, head_snapshot=changed_snapshot,
        )
        self.assertEqual(violations, ["templates/routerai_catalog.generated.json -> весь файл"])


if __name__ == "__main__":
    unittest.main()
