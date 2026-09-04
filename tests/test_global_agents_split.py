from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import setup_lib as lib


PRE_SPLIT_AGENTS = b"""# Global OpenCode instructions

- Never expose secrets, tokens, passwords, API keys, or credential files.
- Do not scan `.git`, `node_modules`, build output, caches, or logs without a reason.
- When work uses `ssh_relay`, load the `ssh-relay` skill first.
- Before builds, CMake, CTest, integration/load tests, long scripts, or other long-running operations, load `remote-long-running`.
- Before risky state-changing actions or work in an unfamiliar subsystem, load the relevant agent-safe skill: `risk-gate`, `safe-cli`, `unknown-system-safety`, or `recovery-mode`.
- Do not preload specialized skills unless the current task needs them.
"""

MANAGED_INSTRUCTIONS = b"""# agent-toolchain managed OpenCode instructions

> This file is managed by `agent-toolchain`. Do not edit it directly. Put machine-specific or user-specific persistent instructions in `../AGENTS.md` outside the `agent-toolchain:managed` markers.

- Never expose secrets, tokens, passwords, API keys, or credential files.
- Do not scan `.git`, `node_modules`, build output, caches, or logs without a reason.
- When work uses `ssh_relay`, load the `ssh-relay` skill first.
- Before builds, CMake, CTest, integration/load tests, long scripts, or other long-running operations, load `remote-long-running`.
- Before risky state-changing actions or work in an unfamiliar subsystem, load the relevant agent-safe skill: `risk-gate`, `safe-cli`, `unknown-system-safety`, or `recovery-mode`.
- Do not preload specialized skills unless the current task needs them.
"""

ILUKHIN_LOCAL_RULE = (
    b"- Shared durable memory is stored in `/home/dilukhin/.agents/memory/`. Before changing Cinnamon, Muffin, "
    b"the active desktop/window manager, display manager, graphical session, or desktop input settings, read "
    b"`/home/dilukhin/.agents/memory/host-safety.md` and follow its mandatory prevention rules.\n"
)


def _result(reporter: lib.Reporter, component: str):
    return [item for item in reporter.results if item.component == component][-1]


def _old_block() -> bytes:
    body = PRE_SPLIT_AGENTS.decode("utf-8").splitlines()[2:]
    return (
        "<!-- opencode_setup:managed:start -->\n"
        "## OpenCode managed environment\n"
        + "\n".join(body)
        + "\n<!-- opencode_setup:managed:end -->\n"
    ).encode("utf-8")


class GlobalAgentsSplitTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        config = root / "config"
        agents = config / "AGENTS.md"
        managed = config / "agent-toolchain" / "managed-instructions.md"
        state = root / "state"
        return agents, managed, state

    def _apply(self, *, agents: Path, state: Path, manifest: dict, force: bool = False) -> lib.Reporter:
        reporter = lib.Reporter()
        lib.reconcile_agents_file(
            destination=agents,
            template_data=MANAGED_INSTRUCTIONS,
            source_label="opencode_setup:templates/AGENTS.md",
            manifest=manifest,
            reporter=reporter,
            check=False,
            force=force,
            state_dir=state,
        )
        return reporter

    def _check(self, *, agents: Path, state: Path, manifest: dict) -> lib.Reporter:
        reporter = lib.Reporter()
        lib.reconcile_agents_file(
            destination=agents,
            template_data=MANAGED_INSTRUCTIONS,
            source_label="opencode_setup:templates/AGENTS.md",
            manifest=manifest,
            reporter=reporter,
            check=True,
            force=False,
            state_dir=state,
        )
        return reporter

    def test_existing_user_file_gets_bootstrap_and_later_user_edits_are_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents, managed, state = self._paths(Path(tmp))
            agents.parent.mkdir(parents=True)
            original = b"# My global rules\n\n- keep this user rule\n"
            agents.write_bytes(original)
            manifest = {"managed_files": {}}

            before = agents.read_bytes()
            checked = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), before)
            self.assertFalse(managed.exists())
            self.assertEqual(_result(checked, "global AGENTS.md").state, lib.STATE_OUTDATED)

            applied = self._apply(agents=agents, state=state, manifest=manifest)
            self.assertEqual(managed.read_bytes(), MANAGED_INSTRUCTIONS)
            text = agents.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(original.decode("utf-8")))
            self.assertIn("<!-- agent-toolchain:managed:start:v1 -->", text)
            self.assertIn(str(managed), text)
            self.assertEqual(manifest["managed_files"]["global AGENTS.md"]["mode"], "bootstrap-block-v1")
            self.assertNotIn("sha256", manifest["managed_files"]["global AGENTS.md"])
            self.assertEqual(_result(applied, "global AGENTS.md").state, lib.STATE_CONFIGURED)

            with agents.open("ab") as stream:
                stream.write(b"\n- agent-added machine rule\n")
            edited = agents.read_bytes()
            checked_again = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), edited)
            self.assertEqual(_result(checked_again, "global AGENTS.md").state, lib.STATE_OK)
            self.assertEqual(_result(checked_again, "OpenCode managed instructions").state, lib.STATE_OK)

            applied_again = self._apply(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), edited)
            self.assertEqual(_result(applied_again, "global AGENTS.md").state, lib.STATE_OK)

    def test_ilukhin_modified_whole_file_migrates_and_preserves_host_safety_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents, managed, state = self._paths(Path(tmp))
            agents.parent.mkdir(parents=True)
            current = PRE_SPLIT_AGENTS + ILUKHIN_LOCAL_RULE
            agents.write_bytes(current)
            manifest = {
                "managed_files": {
                    "global AGENTS.md": {
                        "path": str(agents),
                        "sha256": lib.sha256_bytes(PRE_SPLIT_AGENTS),
                        "source": "opencode_setup:templates/AGENTS.md",
                    }
                }
            }

            snapshot = agents.read_bytes()
            checked = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), snapshot)
            self.assertFalse(managed.exists())
            self.assertEqual(_result(checked, "global AGENTS.md").state, lib.STATE_OUTDATED)

            applied = self._apply(agents=agents, state=state, manifest=manifest)
            migrated = agents.read_bytes()
            self.assertIn(ILUKHIN_LOCAL_RULE, migrated)
            self.assertEqual(migrated.count(ILUKHIN_LOCAL_RULE), 1)
            self.assertIn(b"<!-- agent-toolchain:managed:start:v1 -->", migrated)
            self.assertNotIn(b"<!-- opencode_setup:managed:start -->", migrated)
            self.assertEqual(managed.read_bytes(), MANAGED_INSTRUCTIONS)
            entry = manifest["managed_files"]["global AGENTS.md"]
            self.assertEqual(entry["mode"], "bootstrap-block-v1")
            self.assertIn("block_sha256", entry)
            self.assertNotIn("sha256", entry)
            self.assertEqual(_result(applied, "global AGENTS.md").state, lib.STATE_CONFIGURED)
            backups = list(state.rglob("AGENTS.md"))
            self.assertTrue(backups)
            self.assertIn(current, [path.read_bytes() for path in backups])

            final = agents.read_bytes()
            checked_again = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), final)
            self.assertEqual(_result(checked_again, "global AGENTS.md").state, lib.STATE_OK)

    def test_ambiguous_modified_legacy_whole_file_fails_closed_without_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents, managed, state = self._paths(Path(tmp))
            agents.parent.mkdir(parents=True)
            modified = PRE_SPLIT_AGENTS.replace(
                b"Never expose secrets, tokens, passwords, API keys, or credential files.",
                b"Never expose secrets except during debugging.",
            ) + ILUKHIN_LOCAL_RULE
            agents.write_bytes(modified)
            manifest = {
                "managed_files": {
                    "global AGENTS.md": {
                        "path": str(agents),
                        "sha256": lib.sha256_bytes(PRE_SPLIT_AGENTS),
                        "source": "opencode_setup:templates/AGENTS.md",
                    }
                }
            }
            original_manifest = {"managed_files": {"global AGENTS.md": dict(manifest["managed_files"]["global AGENTS.md"])}}

            applied = self._apply(agents=agents, state=state, manifest=manifest, force=True)
            self.assertEqual(agents.read_bytes(), modified)
            self.assertFalse(managed.exists())
            self.assertEqual(manifest, original_manifest)
            self.assertEqual(_result(applied, "global AGENTS.md").state, lib.STATE_CONFLICT)

    def test_legacy_block_migrates_while_surrounding_text_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents, managed, state = self._paths(Path(tmp))
            agents.parent.mkdir(parents=True)
            old_block = _old_block()
            prefix = b"# User header\n\n- before managed block\n\n"
            suffix = b"\n- after managed block\n"
            current = prefix + old_block + suffix
            agents.write_bytes(current)
            manifest = {
                "managed_files": {
                    "global AGENTS.md": {
                        "path": str(agents),
                        "sha256": lib.sha256_bytes(current),
                        "source": "opencode_setup:templates/AGENTS.md",
                        "mode": "block",
                        "block_sha256": lib.sha256_bytes(old_block),
                    }
                }
            }

            self._apply(agents=agents, state=state, manifest=manifest)
            migrated = agents.read_bytes()
            self.assertTrue(migrated.startswith(prefix))
            self.assertTrue(migrated.endswith(suffix))
            self.assertIn(b"<!-- agent-toolchain:managed:start:v1 -->", migrated)
            self.assertNotIn(b"<!-- opencode_setup:managed:start -->", migrated)
            self.assertEqual(managed.read_bytes(), MANAGED_INSTRUCTIONS)
            self.assertEqual(manifest["managed_files"]["global AGENTS.md"]["mode"], "bootstrap-block-v1")

    def test_changes_inside_new_bootstrap_or_managed_file_remain_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents, managed, state = self._paths(Path(tmp))
            agents.parent.mkdir(parents=True)
            agents.write_text("# User rules\n", encoding="utf-8")
            manifest = {"managed_files": {}}
            self._apply(agents=agents, state=state, manifest=manifest)

            original_agents = agents.read_text(encoding="utf-8")
            agents.write_text(original_agents.replace("Never expose secrets", "Never expose selected secrets"), encoding="utf-8")
            changed_agents = agents.read_bytes()
            checked = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(agents.read_bytes(), changed_agents)
            self.assertEqual(_result(checked, "global AGENTS.md").state, lib.STATE_CONFLICT)

            agents.write_text(original_agents, encoding="utf-8")
            managed.write_bytes(MANAGED_INSTRUCTIONS + b"\nlocal edit\n")
            changed_managed = managed.read_bytes()
            checked_managed = self._check(agents=agents, state=state, manifest=manifest)
            self.assertEqual(managed.read_bytes(), changed_managed)
            self.assertEqual(_result(checked_managed, "OpenCode managed instructions").state, lib.STATE_CONFLICT)


if __name__ == "__main__":
    unittest.main()
