from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_yc_transitional_guard as ycsetup  # noqa: E402
import yc_transitional_entry as entry  # noqa: E402


def make_runtime(root: Path, downstream: Path) -> Path:
    adapter = b"""
def dispatch(fact, *, trusted_execution_target, executor, caller_context=None):
    argv = fact["argv"]
    if "deny-me" in argv:
        return {"decision":"DENY","execute":False,"reason_codes":["fixture.deny"],"execution_result":None}
    if "ask-me" in argv:
        return {"decision":"ASK_USER","execute":False,"reason_codes":["fixture.ask"],"execution_result":None}
    return {
        "decision":"ALLOW",
        "execute":True,
        "reason_codes":["fixture.allow"],
        "execution_result":executor(trusted_execution_target["resolved_path"], argv[1:]),
    }
"""
    artifact = ROOT / "tests"  # placeholder only to reuse helper-free local creation
    temp = root / "artifact-temp"
    files = {
        "policy/yc/yc_policy.v1.json": b'{"schema":"yc-policy/v1","version":"1.0.0","provider":"yandex-cloud"}\n',
        "runtime/yc_transitional_adapter.py": adapter,
        "runtime/classifier_yc.py": b"# unused fixture\n",
        "runtime/classifier_core.py": b"# unused fixture\n",
        "runtime/normalized_operation_identity.py": b"# unused fixture\n",
    }
    for relative, data in files.items():
        path = temp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    records = [
        {"path": relative, "sha256": ycsetup._sha256_bytes(data), "size": len(data)}
        for relative, data in sorted(files.items())
    ]
    policy_record = next(x for x in records if x["path"] == "policy/yc/yc_policy.v1.json")
    manifest = {
        "schema": 1,
        "artifact_format": ycsetup.ARTIFACT_FORMAT,
        "artifact_id": "",
        "status": "transitional_ready",
        "owner": ycsetup.ARTIFACT_OWNER,
        "target": {"provider": "yandex-cloud", "platform": "windows", "mode": "transitional"},
        "policy": {
            "schema": "yc-policy/v1",
            "version": "1.0.0",
            "relative_path": "policy/yc/yc_policy.v1.json",
            "sha256": policy_record["sha256"],
        },
        "files": records,
        "constraints": {
            "canonical_policy_only": True,
            "setup_semantic_rewrite": False,
            "developer_checkout_dependency": False,
            "hard_deny_terminal": True,
            "unknown_or_opaque_not_allow": True,
            "runtime_path_resolution": False,
            "downstream_executable_identity_required": True,
            "caller_approval_flags_trusted": False,
            "real_executor_packaged": False,
            "transitional_exact_mutation_auto_allow": True,
            "full_authorization_binding_complete": False,
        },
        "transitional_auto_allow_mutations": [],
        "artifact_path_segment": "",
    }
    manifest["artifact_id"] = ycsetup._artifact_id(manifest)
    manifest["artifact_path_segment"] = ycsetup._segment(manifest["artifact_id"])
    artifact_dir = root / manifest["artifact_path_segment"]
    temp.rename(artifact_dir)
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    release = root / "release"
    release.mkdir()
    (release / "artifact").mkdir()
    # copy exact artifact tree
    import shutil
    shutil.rmtree(release / "artifact")
    shutil.copytree(artifact_dir, release / "artifact")
    deployment = {
        "schema": 1,
        "owner": "agent-toolchain",
        "component": "yc-transitional-guard",
        "artifact_id": manifest["artifact_id"],
        "source_repository": ycsetup.SOURCE_REPOSITORY,
        "source_ref": ycsetup.SOURCE_REF,
        "artifact_relative_path": "artifact",
        "downstream_path": str(downstream.resolve()),
        "downstream_sha256": ycsetup._sha256_file(downstream),
    }
    (release / "deployment.json").write_text(json.dumps(deployment, indent=2) + "\n", encoding="utf-8")
    return release


class YcTransitionalEntryTests(unittest.TestCase):
    def test_allow_uses_exact_pinned_downstream_and_argv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            downstream = root / "yc.exe"
            downstream.write_bytes(b"fixture executable")
            release = make_runtime(root, downstream)
            calls = []

            def executor(path, tail):
                calls.append((path, list(tail)))
                return 23

            rc = entry.run_guard(["compute", "instance", "list"], release=release, executor=executor)
            self.assertEqual(rc, 23)
            self.assertEqual(calls, [(str(downstream.resolve()), ["compute", "instance", "list"])])

    def test_ask_and_deny_never_reach_executor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            downstream = root / "yc.exe"
            downstream.write_bytes(b"fixture executable")
            release = make_runtime(root, downstream)
            for token, expected in (("ask-me", entry.ASK_EXIT), ("deny-me", entry.DENY_EXIT)):
                calls = []
                with self.subTest(token=token):
                    rc = entry.run_guard([token], release=release, executor=lambda p, a: calls.append((p, a)) or 0)
                    self.assertEqual(rc, expected)
                    self.assertEqual(calls, [])

    def test_downstream_tamper_fails_closed_before_executor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            downstream = root / "yc.exe"
            downstream.write_bytes(b"fixture executable")
            release = make_runtime(root, downstream)
            downstream.write_bytes(b"tampered")
            calls = []
            with self.assertRaises(entry.RuntimeGuardError):
                entry.run_guard([], release=release, executor=lambda p, a: calls.append((p, a)) or 0)
            self.assertEqual(calls, [])

    def test_unlisted_artifact_file_fails_closed_before_executor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            downstream = root / "yc.exe"
            downstream.write_bytes(b"fixture executable")
            release = make_runtime(root, downstream)
            extra = release / "artifact" / "runtime" / "extra.py"
            extra.write_text("# unexpected\n", encoding="utf-8")
            calls = []
            with self.assertRaises(entry.RuntimeGuardError) as ctx:
                entry.run_guard([], release=release, executor=lambda p, a: calls.append((p, a)) or 0)
            self.assertEqual(str(ctx.exception), "ARTIFACT_FILESET_MISMATCH")
            self.assertEqual(calls, [])

    def test_artifact_tamper_fails_closed_before_executor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            downstream = root / "yc.exe"
            downstream.write_bytes(b"fixture executable")
            release = make_runtime(root, downstream)
            adapter = release / "artifact" / "runtime" / "yc_transitional_adapter.py"
            adapter.write_text("tampered\n", encoding="utf-8")
            calls = []
            with self.assertRaises(entry.RuntimeGuardError):
                entry.run_guard([], release=release, executor=lambda p, a: calls.append((p, a)) or 0)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
