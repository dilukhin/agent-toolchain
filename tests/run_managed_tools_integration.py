from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_manifest  # noqa: E402
from setup_lib import Reporter, STATE_CONFLICT, STATE_FAILED  # noqa: E402
from setup_managed_tools import reconcile_tool_specs  # noqa: E402
from setup_tool_skills_impl import reconcile_pinned_tool_skills, tool_skill_bindings  # noqa: E402
from setup_tools import load_effective_config, parse_tool_specs  # noqa: E402

_BAD_STATES = {STATE_CONFLICT, STATE_FAILED}


def _assert_clean_report(reporter: Reporter, phase: str) -> None:
    bad = [item for item in reporter.results if item.state in _BAD_STATES]
    if not bad:
        return
    reporter.render()
    detail = "; ".join(f"{item.component}: {item.state}: {item.detail}" for item in bad)
    raise AssertionError(f"{phase} reported blocking states: {detail}")


def _snapshot(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    config = load_effective_config(ROOT, profile="dilukhin")
    env_cfg = config["managed_environment"]
    specs, error = parse_tool_specs(env_cfg)
    if error:
        raise AssertionError(error)
    if set(specs) != {"ssh_relay", "agent-safe"}:
        raise AssertionError(f"unexpected managed tool registry: {sorted(specs)}")

    bindings, error = tool_skill_bindings(env_cfg, specs)
    if error:
        raise AssertionError(error)

    with tempfile.TemporaryDirectory(prefix="agent-toolchain-integration-") as td:
        root = Path(td)
        data = root / "data"
        bin_dir = root / "bin"
        skills_dir = root / "skills"
        state_dir = root / "state"
        env = {
            "AGENT_TOOLCHAIN_DATA_DIR": str(data),
            "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        }

        manifest = setup_manifest.empty_manifest()
        with mock.patch.dict(os.environ, env, clear=False):
            check_reporter = Reporter()
            check_changed = reconcile_tool_specs(
                specs,
                sys.executable,
                check_reporter,
                check=True,
                skip_install=False,
                manifest=manifest,
            )
            check_changed |= reconcile_pinned_tool_skills(
                env_cfg,
                specs,
                manifest,
                check_reporter,
                skills_dir=skills_dir,
                state_dir=state_dir,
                check=True,
                force=False,
                skip_install=False,
            )
            _assert_clean_report(check_reporter, "read-only check")
            if check_changed:
                raise AssertionError("read-only check must not report mutations")
            for path in (data, bin_dir, skills_dir, state_dir):
                if path.exists():
                    raise AssertionError(f"read-only check unexpectedly created {path}")

            first_reporter = Reporter()
            first_changed = reconcile_tool_specs(
                specs,
                sys.executable,
                first_reporter,
                check=False,
                skip_install=False,
                manifest=manifest,
            )
            first_changed |= reconcile_pinned_tool_skills(
                env_cfg,
                specs,
                manifest,
                first_reporter,
                skills_dir=skills_dir,
                state_dir=state_dir,
                check=False,
                force=False,
                skip_install=False,
            )
            _assert_clean_report(first_reporter, "first apply")
            if not first_changed:
                raise AssertionError("first apply unexpectedly reported no changes")

            for tool_name, spec in specs.items():
                record = manifest["managed_tools"].get(tool_name)
                if not isinstance(record, dict):
                    raise AssertionError(f"missing managed_tools record for {tool_name}")
                if record.get("source_ref") != spec.ref:
                    raise AssertionError(f"{tool_name} manifest ref mismatch: {record.get('source_ref')} != {spec.ref}")
                for command in spec.entrypoints:
                    suffix = ".cmd" if os.name == "nt" else ""
                    public = bin_dir / f"{command}{suffix}"
                    if not (public.exists() or public.is_symlink()):
                        raise AssertionError(f"missing public entrypoint: {public}")

                for skill_name, relative in bindings.get(tool_name, {}).items():
                    destination = skills_dir / skill_name / "SKILL.md"
                    if not destination.is_file():
                        raise AssertionError(f"missing pinned skill: {destination}")
                    file_record = manifest["managed_files"].get(f"skill {skill_name}")
                    expected_source = f"tool:{tool_name}@{spec.ref}:{relative}"
                    if not isinstance(file_record, dict) or file_record.get("source") != expected_source:
                        raise AssertionError(
                            f"{skill_name} source mismatch: {file_record!r}; expected {expected_source}"
                        )

            manifest_before_repeat = _snapshot(manifest)
            second_reporter = Reporter()
            second_changed = reconcile_tool_specs(
                specs,
                sys.executable,
                second_reporter,
                check=False,
                skip_install=False,
                manifest=manifest,
            )
            second_changed |= reconcile_pinned_tool_skills(
                env_cfg,
                specs,
                manifest,
                second_reporter,
                skills_dir=skills_dir,
                state_dir=state_dir,
                check=False,
                force=False,
                skip_install=False,
            )
            _assert_clean_report(second_reporter, "repeat apply")
            if second_changed:
                raise AssertionError("repeat apply must be a no-op")
            if _snapshot(manifest) != manifest_before_repeat:
                raise AssertionError("repeat apply changed ownership manifest metadata")

        print("PASS managed helper exact-ref integration")
        for tool_name, spec in sorted(specs.items()):
            print(f"  {tool_name}: {spec.ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
