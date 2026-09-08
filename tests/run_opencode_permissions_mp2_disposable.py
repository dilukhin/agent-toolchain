#!/usr/bin/env python3
"""MP-2 disposable integration for the managed OpenCode Permissions P0 pilot."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import setup_opencode_permissions_pilot as pilot  # noqa: E402

SCENARIOS = {
    "native_allow": "pwd",
    "native_deny": "sudo MP2_NATIVE_DENY",
    "classifier_allow": "/usr/bin/grep MP2_NEEDLE fixture.txt",
    "residual_ask": "/usr/bin/wc -l fixture.txt",
    "classifier_failure": "/usr/bin/grep MP2_NEEDLE fixture.txt",
}
GITIGNORE = "node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore\n"
METRIC_COUNTERS = {
    "native_ask",
    "classifier_allow",
    "classifier_deny",
    "residual_ask",
    "classifier_error/fail_closed",
    "binding_reject",
}
METRIC_TOPLEVEL = {
    "schema",
    "scope",
    "opencode_version",
    "compatibility_profile",
    "native_policy_artifact_id",
    "pilot_artifact_id",
    "classifier_profile",
    "counters",
    "reasons",
    "families",
    "updated_at",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def segment(artifact_id: str) -> str:
    assert artifact_id.startswith("sha256:") and len(artifact_id) == 71
    return "sha256-" + artifact_id.split(":", 1)[1]


def load_current_contract(permissions_root: Path) -> dict[str, Any]:
    permissions_root = permissions_root.resolve()
    croot = permissions_root / "tests" / "compatibility"
    registry = load_json(croot / "registry.json")
    assert registry["selection"] == "exact_version_only"
    assert registry["nearest_version_fallback"] is False
    version = registry["current_target"]
    profile = load_json(croot / registry["profiles"][version])
    assert profile["opencode_version"] == version
    assert profile["overall_status"] == "DEPLOYABLE"
    assert profile["deployable"] is True
    assert profile["platform_status"]["linux"] == "RUNTIME_REVALIDATED"
    assert "linux" in profile["deployable_platforms"]

    native_id = profile["policy_artifacts"]["linux"]
    native_dir = permissions_root / "dist" / "opencode" / segment(native_id)

    builder_path = permissions_root / "tools" / "build_p0_pilot_artifact.py"
    spec = importlib.util.spec_from_file_location("mp2_p0_artifact_builder", builder_path)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    plan = builder.build_plan(permissions_root)
    assert plan["manifest"]["target"]["exact_version"] == version
    assert plan["manifest"]["native_policy_artifact_id"] == native_id
    pilot_dir = permissions_root / plan["artifact_path"]
    pilot_manifest = load_json(pilot_dir / "manifest.json")
    assert pilot_manifest == plan["manifest"]

    checked = pilot.validate_artifacts(
        pilot_bundle_dir=pilot_dir,
        native_artifact_dir=native_dir,
        installed_version=version,
        installed_platform="linux",
    )
    assert checked["pilot_artifact_id"] == pilot_manifest["artifact_id"]
    classifier_profile = load_json(pilot_dir / pilot_manifest["classifier_profile"]["relative_path"])
    assert pilot_manifest["constraints"]["auditor_enabled"] is False
    assert pilot_manifest["constraints"]["workspace_trust_enabled"] is False
    assert pilot_manifest["constraints"]["state_changing_classifier_enabled"] is False
    assert classifier_profile["constraints"]["auditor"] is False
    assert classifier_profile["constraints"]["workspace_trust"] is False
    assert classifier_profile["constraints"]["state_changing"] is False
    assert classifier_profile["allow_families"] == ["grep.single_nonsecret_workspace_file"]
    metrics = classifier_profile["metrics"]
    assert metrics["schema"] == "opencode-permissions-p0-metrics/v1"
    assert metrics["scope"] == "ask_path"
    assert metrics["required_for_classifier_allow"] is True
    assert metrics["raw_inputs"] is False
    assert metrics["storage"] == "per_process_aggregate_snapshot"
    assert metrics["state_resolution"] == "os_homedir_local_state"
    assert metrics["max_reason_buckets"] == 64
    assert set(metrics["counters"]) == METRIC_COUNTERS
    assert "native_allow" not in metrics["counters"]
    assert "native_deny" not in metrics["counters"]

    return {
        "permissions_root": permissions_root,
        "version": version,
        "profile": profile,
        "release": profile["release_artifacts"]["linux_x64"],
        "native_id": native_id,
        "native_dir": native_dir,
        "pilot_dir": pilot_dir,
        "pilot_manifest": pilot_manifest,
        "classifier_profile": classifier_profile,
    }


def load_dc4_helpers(permissions_root: Path):
    path = permissions_root / "tests" / "dc4_runtime" / "run_probe.py"
    spec = importlib.util.spec_from_file_location("mp2_dc4_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_release(opencode: Path, archive: Path, contract: dict[str, Any]) -> None:
    release = contract["release"]
    assert archive.name == release["name"]
    assert sha256_file(archive) == release["sha256"]
    version = subprocess.run(
        [str(opencode), "--version"], check=True, text=True, capture_output=True
    ).stdout.strip()
    assert version == contract["version"]
    assert contract["profile"]["upstream"]["tag"] == f"v{version}"


def pending_for_command(pending: Any, command: str) -> list[dict[str, Any]]:
    result = []
    for request in pending if isinstance(pending, list) else []:
        if not isinstance(request, dict):
            continue
        metadata = request.get("metadata") or {}
        if request.get("permission") == "bash" and metadata.get("command") == command:
            result.append(request)
    return result


def observe(dc4, base: str, directory: str, session_id: str, command: str, *, expect_pending: bool):
    deadline = time.monotonic() + 20
    parts: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        messages = dc4.http_json(
            "GET", base + f"/session/{session_id}/message", directory=directory, timeout=5
        )
        parts = dc4.tool_parts(messages)
        pending = pending_for_command(
            dc4.http_json("GET", base + "/permission", directory=directory, timeout=5),
            command,
        )
        states = [(part.get("state") or {}).get("status") for part in parts]
        if expect_pending and pending:
            assert "completed" not in states
            return parts, pending
        if not expect_pending and ("completed" in states or "error" in states):
            return parts, pending
        time.sleep(0.2)
    raise AssertionError(
        f"timeout command={command!r} states="
        f"{[(part.get('state') or {}).get('status') for part in parts]} pending={pending}"
    )


def configure_and_deploy(root: Path, provider_port: int, contract: dict[str, Any]):
    project = root / "project"
    home = root / "home"
    project.mkdir()
    home.mkdir()
    (project / "fixture.txt").write_text(
        "first line\nMP2_NEEDLE expected line\nlast line\n", encoding="utf-8"
    )
    config_dir = home / ".config" / "opencode"
    data_dir = home / ".local" / "share" / "agent-toolchain"
    state_dir = home / ".local" / "state" / "agent-toolchain"
    config_dir.mkdir(parents=True)
    config = {
        "$schema": "https://opencode.ai/config.json",
        "shell": "/bin/dash",
        "provider": {
            "mp2": {
                "name": "MP2 Local Mock",
                "npm": "@ai-sdk/openai-compatible",
                "api": f"http://127.0.0.1:{provider_port}/v1",
                "models": {
                    "mock": {
                        "name": "MP2 Mock",
                        "tool_call": True,
                        "limit": {"context": 32000, "output": 4096},
                    }
                },
                "options": {
                    "apiKey": "mp2-local-test",
                    "baseURL": f"http://127.0.0.1:{provider_port}/v1",
                },
            }
        },
    }
    (config_dir / "opencode.jsonc").write_bytes(pilot._pretty_json(config))
    deployed = pilot.apply_pilot(
        pilot_bundle_dir=contract["pilot_dir"],
        native_artifact_dir=contract["native_dir"],
        installed_version=contract["version"],
        config_dir=config_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        installed_platform="linux",
    )
    assert deployed["effective_readback"] == "PASS"
    assert deployed["artifact_id"] == contract["pilot_manifest"]["artifact_id"]
    assert deployed["native_artifact_id"] == contract["native_id"]

    plugin_dir = config_dir / "plugins"
    plugins = sorted(p.name for p in plugin_dir.iterdir() if p.is_file())
    assert plugins == [pilot.PLUGIN_NAME], plugins
    loader = (plugin_dir / pilot.PLUGIN_NAME).read_text(encoding="utf-8")
    assert "OpenCodePermissionsP0" in loader and "auditor" not in loader.lower()

    (config_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    os.chmod(config_dir, 0o555)

    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text(
        "#!/bin/sh\nprintf '%s\\n' MP2_NATIVE_DENY_EXECUTED > \"$MP2_DENY_MARKER\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o755)
    deny_marker = root / "native-deny-executed.txt"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "MP2_DENY_MARKER": str(deny_marker),
        }
    )
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    return project, home, config_dir, Path(deployed["runtime_dir"]), deny_marker, env


def metric_files(home: Path, contract: dict[str, Any]) -> list[Path]:
    directory = (
        home
        / ".local"
        / "state"
        / "opencode_permissions"
        / "p0-metrics"
        / contract["pilot_manifest"]["artifact_path_segment"]
    )
    if not directory.exists():
        return []
    assert directory.is_dir() and not directory.is_symlink()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    return sorted(directory.glob("process-*.json"))


def aggregate_metrics(
    home: Path,
    contract: dict[str, Any],
    *,
    command: str,
    project: Path,
    session_id: str,
) -> dict[str, int]:
    files = metric_files(home, contract)
    assert files, "expected P0 metrics snapshot"
    aggregate = {name: 0 for name in METRIC_COUNTERS}
    for path in files:
        assert path.is_file() and not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, dict)
        assert set(data) == METRIC_TOPLEVEL
        assert data["schema"] == "opencode-permissions-p0-metrics/v1"
        assert data["scope"] == "ask_path"
        assert data["opencode_version"] == contract["version"]
        assert data["compatibility_profile"] == contract["profile"]["profile_id"]
        assert data["native_policy_artifact_id"] == contract["native_id"]
        assert data["pilot_artifact_id"] == contract["pilot_manifest"]["artifact_id"]
        assert data["classifier_profile"] == contract["classifier_profile"]["classifier_profile_id"]
        assert set(data["counters"]) == METRIC_COUNTERS
        assert isinstance(data["reasons"], dict) and len(data["reasons"]) <= 64
        assert isinstance(data["families"], dict) and len(data["families"]) <= 64
        assert command not in raw
        assert str(project) not in raw
        assert session_id not in raw
        assert "fixture.txt" not in raw
        assert "MP2_NEEDLE" not in raw
        for name in METRIC_COUNTERS:
            value = data["counters"][name]
            assert isinstance(value, int) and value >= 0
            aggregate[name] += value
    return aggregate


def run_scenario(opencode: Path, contract: dict[str, Any], dc4, name: str) -> dict[str, Any]:
    command = SCENARIOS[name]
    with tempfile.TemporaryDirectory(prefix=f"mp2-{name}-") as td, dc4.mock_provider(command) as provider_port:
        root = Path(td)
        project, home, config_dir, runtime_dir, deny_marker, env = configure_and_deploy(
            root, provider_port, contract
        )
        port = dc4.free_port()
        server = subprocess.Popen(
            [str(opencode), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=project,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        metrics: dict[str, int] | None = None
        try:
            dc4.wait_server(base, str(project), server)
            if name == "classifier_failure":
                adapter = runtime_dir / "runtime" / "opencode_p0_adapter.py"
                assert adapter.is_file()
                adapter.unlink()

            session = dc4.http_json(
                "POST", base + "/session", directory=str(project), payload={"title": f"MP2 {name}"}
            )
            sid = session["id"]
            dc4.http_json(
                "POST",
                base + f"/session/{sid}/prompt_async",
                directory=str(project),
                payload={
                    "agent": "build",
                    "model": {"providerID": "mp2", "modelID": "mock"},
                    "parts": [{"type": "text", "text": f"MP2 disposable proof {name}"}],
                },
            )
            expect_pending = name in {"residual_ask", "classifier_failure"}
            parts, pending = observe(
                dc4, base, str(project), sid, command, expect_pending=expect_pending
            )
            states = [(part.get("state") or {}).get("status") for part in parts]
            completed = [
                part for part in parts if (part.get("state") or {}).get("status") == "completed"
            ]
            outputs = [str((part.get("state") or {}).get("output", "")) for part in completed]

            if name == "native_allow":
                assert "completed" in states and not pending
                assert any(str(project) in output for output in outputs)
                assert not metric_files(home, contract), "native ALLOW must not be inferred into ASK-path metrics"
            elif name == "native_deny":
                assert "error" in states and not pending
                assert not deny_marker.exists(), "native DENY command executed"
                assert not metric_files(home, contract), "native DENY must not be inferred into ASK-path metrics"
            elif name == "classifier_allow":
                assert "completed" in states and not pending
                assert any("MP2_NEEDLE expected line" in output for output in outputs)
                metrics = aggregate_metrics(
                    home, contract, command=command, project=project, session_id=sid
                )
                assert metrics["native_ask"] == 1
                assert metrics["classifier_allow"] == 1
                assert metrics["classifier_deny"] == 0
                assert metrics["residual_ask"] == 0
                assert metrics["classifier_error/fail_closed"] == 0
                assert metrics["binding_reject"] == 0
            elif name == "residual_ask":
                assert pending and "completed" not in states
                metrics = aggregate_metrics(
                    home, contract, command=command, project=project, session_id=sid
                )
                assert metrics["native_ask"] == 1
                assert metrics["classifier_allow"] == 0
                assert metrics["classifier_deny"] == 0
                assert metrics["residual_ask"] == 1
                assert metrics["classifier_error/fail_closed"] == 0
                assert metrics["binding_reject"] == 0
            elif name == "classifier_failure":
                assert pending and "completed" not in states
                metrics = aggregate_metrics(
                    home, contract, command=command, project=project, session_id=sid
                )
                assert metrics["native_ask"] == 1
                assert metrics["classifier_allow"] == 0
                assert metrics["classifier_deny"] == 0
                assert metrics["residual_ask"] == 1
                assert metrics["classifier_error/fail_closed"] == 1
                assert metrics["binding_reject"] == 0
            else:
                raise AssertionError(name)

            return {
                "scenario": name,
                "status": "PASS",
                "tool_states": states,
                "pending_permission": bool(pending),
                "metrics": metrics,
            }
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            try:
                os.chmod(config_dir, 0o755)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode", required=True)
    parser.add_argument("--release-archive", required=True)
    parser.add_argument("--permissions-root", required=True)
    args = parser.parse_args()

    contract = load_current_contract(Path(args.permissions_root))
    dc4 = load_dc4_helpers(contract["permissions_root"])
    opencode = Path(args.opencode).resolve()
    verify_release(opencode, Path(args.release_archive).resolve(), contract)

    results = [run_scenario(opencode, contract, dc4, name) for name in SCENARIOS]
    results.append(
        {
            "scenario": "no_auditor",
            "status": "PASS",
            "pilot_constraint": contract["pilot_manifest"]["constraints"]["auditor_enabled"],
            "profile_constraint": contract["classifier_profile"]["constraints"]["auditor"],
        }
    )
    print(
        json.dumps(
            {
                "schema": "opencode-permissions-mp2-disposable-proof/v1",
                "status": "PASS",
                "opencode_version": contract["version"],
                "compatibility_profile": contract["profile"]["profile_id"],
                "pilot_artifact": contract["pilot_manifest"]["artifact_id"],
                "native_artifact": contract["native_id"],
                "official_release_asset": contract["release"]["name"],
                "results": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
