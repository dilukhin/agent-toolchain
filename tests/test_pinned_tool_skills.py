from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_core  # noqa: E402
import setup_core_adapter as core_adapter  # noqa: E402
import setup_manifest  # noqa: E402
import setup_tool_skills_impl as skills  # noqa: E402
from setup_lib import Reporter, STATE_OK, STATE_SKIPPED  # noqa: E402
from setup_tools import parse_tool_spec  # noqa: E402


class PinnedToolSkillTests(unittest.TestCase):
    REF = "1" * 40

    def _spec(self):
        spec, error = parse_tool_spec("ssh_relay", {
            "source": "git",
            "repo": "https://example.invalid/ssh_relay.git",
            "ref": self.REF,
            "project_directory": "ssh_relay",
            "runtime": "python-venv",
            "update_policy": "pinned-tested",
            "entrypoints": ["ssh_relay"],
            "health_contract": [{"argv": ["ssh_relay", "doctor"]}],
            "platforms": ["windows", "linux"],
        })
        self.assertIsNone(error)
        assert spec is not None
        return spec

    def _env_cfg(self):
        return {
            "dependencies": {
                "ssh_relay": {
                    "directory": "ssh_relay",
                    "skill": "opencode/skills/ssh-relay/SKILL.md",
                }
            }
        }

    def _fake_run(self, commands: list[list[str]]):
        def fake_run(cmd: list[str], cwd=None, env=None):
            commands.append(cmd)
            if cmd[:3] == ["git", "init", "--quiet"]:
                Path(cmd[3]).mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) >= 4 and cmd[:2] == ["git", "-C"] and cmd[3:6] == ["remote", "add", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) >= 4 and cmd[:2] == ["git", "-C"] and cmd[3] == "fetch":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) >= 4 and cmd[:2] == ["git", "-C"] and cmd[3] == "checkout":
                source = Path(cmd[2])
                skill = source / "opencode" / "skills" / "ssh-relay" / "SKILL.md"
                skill.parent.mkdir(parents=True, exist_ok=True)
                skill.write_text(
                    "---\nname: ssh-relay\ndescription: Pinned test skill.\ncompatibility: opencode\n---\n# pinned\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) >= 5 and cmd[:2] == ["git", "-C"] and cmd[3:5] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, self.REF + "\n", "")
            raise AssertionError(f"unexpected command: {cmd}")
        return fake_run

    def test_check_is_read_only_and_apply_uses_exact_ref_independent_of_tracking_checkout(self) -> None:
        spec = self._spec()
        env_cfg = self._env_cfg()
        specs = {spec.name: spec}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            state = root / "state"
            destination = root / "skills"
            dirty_checkout = root / "projects" / "ssh_relay"
            dirty_checkout.mkdir(parents=True)
            (dirty_checkout / "LOCAL.txt").write_text("developer work", encoding="utf-8")
            manifest = setup_manifest.empty_manifest()
            commands: list[list[str]] = []
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "OPENCODE_PROJECTS_DIR": str(root / "projects"),
            }
            with mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch.object(skills, "run", self._fake_run(commands)), \
                    mock.patch.object(skills.shutil, "which", return_value="/fake/git"):
                check_reporter = Reporter()
                changed_check = skills.reconcile_pinned_tool_skills(
                    env_cfg, specs, manifest, check_reporter,
                    skills_dir=destination, state_dir=state,
                    check=True, force=False, skip_install=False,
                )
                self.assertFalse(changed_check)
                self.assertFalse(data.exists())
                self.assertFalse(destination.exists())
                self.assertEqual(commands, [])

                apply_reporter = Reporter()
                changed_apply = skills.reconcile_pinned_tool_skills(
                    env_cfg, specs, manifest, apply_reporter,
                    skills_dir=destination, state_dir=state,
                    check=False, force=False, skip_install=False,
                )
                command_count = len(commands)
                repeat_reporter = Reporter()
                changed_repeat = skills.reconcile_pinned_tool_skills(
                    env_cfg, specs, manifest, repeat_reporter,
                    skills_dir=destination, state_dir=state,
                    check=False, force=False, skip_install=False,
                )

            self.assertTrue(changed_apply)
            self.assertFalse(changed_repeat)
            self.assertEqual(len(commands), command_count, "repeat apply must not fetch the pinned ref again")
            installed = destination / "ssh-relay" / "SKILL.md"
            self.assertIn("# pinned", installed.read_text(encoding="utf-8"))
            record = manifest["managed_files"]["skill ssh-relay"]
            self.assertEqual(
                record["source"],
                f"tool:ssh_relay@{self.REF}:opencode/skills/ssh-relay/SKILL.md",
            )
            marker = data / "tools" / "ssh_relay" / "skill-releases" / self.REF / skills._MARKER
            self.assertIn(self.REF, marker.read_text(encoding="utf-8"))
            self.assertEqual((dirty_checkout / "LOCAL.txt").read_text(encoding="utf-8"), "developer work")


class ToolchainCoreAdapterTests(unittest.TestCase):
    def test_toolchain_mode_bypasses_tracking_repositories_and_external_skill_reconciliation(self) -> None:
        core_adapter.install()
        before_repo = setup_core.reconcile_repo
        before_safe_repo = setup_core.reconcile_agent_safe_repo
        before_validate = setup_core.validate_skill
        before_reconcile_file = setup_core.reconcile_file
        observed: dict[str, object] = {}

        def fake_main(argv=None):
            assert argv is not None
            projects = Path(argv[argv.index("--projects-dir") + 1])
            observed["projects"] = projects
            reporter = Reporter()
            ok, state = setup_core.reconcile_repo(
                component="ssh_relay repository", path=projects / "ssh_relay",
                url="ignored", branch="main", reporter=reporter, check=False,
            )
            self.assertTrue(ok)
            self.assertEqual(state, STATE_OK)
            source = projects / "ssh_relay" / "opencode" / "skills" / "ssh-relay" / "SKILL.md"
            valid, _ = setup_core.validate_skill(source, "ssh-relay")
            self.assertTrue(valid)
            changed = setup_core.reconcile_file(
                component="skill ssh-relay",
                destination=projects / "dest" / "SKILL.md",
                source_data=source.read_bytes(), source_label="legacy-tracking-checkout",
                manifest=setup_manifest.empty_manifest(), reporter=reporter,
                check=False, force=False, state_dir=projects / "state",
            )
            self.assertFalse(changed)
            self.assertTrue(any(r.state == STATE_SKIPPED for r in reporter.results))
            return 0

        user_projects = Path("/developer/checkout/that/must/not/be-used")
        argv = ["--projects-dir", str(user_projects)]
        with mock.patch.object(core_adapter, "_ORIGINAL_MAIN", side_effect=fake_main), \
                mock.patch.dict(os.environ, {core_adapter._ENV: "1"}, clear=False):
            self.assertEqual(setup_core.main(argv), 0)

        self.assertNotEqual(observed["projects"], user_projects)
        self.assertIs(setup_core.reconcile_repo, before_repo)
        self.assertIs(setup_core.reconcile_agent_safe_repo, before_safe_repo)
        self.assertIs(setup_core.validate_skill, before_validate)
        self.assertIs(setup_core.reconcile_file, before_reconcile_file)


if __name__ == "__main__":
    unittest.main()
