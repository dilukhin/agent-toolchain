#!/usr/bin/env python3
"""Disposable exact OpenCode check for project-scoped P0 enable/readback/disable."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup_opencode_permissions_pilot_control as control
import setup_opencode_permissions_pilot_enable as enable


def resolved_from_server(binary: str, project: Path, sibling: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    child = subprocess.Popen(
        [binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
        cwd=project, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 30
        last = "not_started"
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError("P0_SERVER_EXITED")
            try:
                configs = []
                for directory in (project, sibling):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/config",
                        headers={"x-opencode-directory": str(directory)},
                    )
                    with opener.open(request, timeout=3) as response:
                        value = json.load(response)
                    assert isinstance(value, dict)
                    configs.append(value)
                return configs
            except urllib.error.HTTPError as exc:
                last = f"HTTP_{exc.code}"
                time.sleep(0.2)
            except (OSError, ValueError) as exc:
                last = type(exc).__name__
                time.sleep(0.2)
        raise AssertionError(f"P0_EFFECTIVE_CONFIG_SERVER_TIMEOUT_{last}")
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opencode", required=True)
    parser.add_argument("--permissions-root", type=Path, required=True)
    args = parser.parse_args()
    source_root = args.permissions_root.resolve(strict=True)
    version = enable.source.CURRENT.version
    registry = json.loads((source_root / "tests/compatibility/registry.json").read_text())
    assert registry["current_target"] == version
    profile = json.loads((source_root / "tests/compatibility" / registry["profiles"][version]).read_text())
    assert profile["deployable"] and profile["platform_status"]["linux"] == "RUNTIME_REVALIDATED"
    pilot_id = enable.source.CURRENT.pilot_id.replace(":", "-")
    native_id = enable.source.CURRENT.native_id.replace(":", "-")
    bundle = source_root / "dist/pilot" / pilot_id
    native = source_root / "dist/opencode" / native_id
    assert bundle.is_dir() and native.is_dir()

    with tempfile.TemporaryDirectory(prefix="mp3-project-") as tmp:
        root = Path(tmp)
        home = root / "home"
        project = root / "project"
        sibling = root / "sibling"
        home.mkdir()
        project.mkdir()
        sibling.mkdir()
        (project / ".git").mkdir()
        (sibling / ".git").mkdir()
        previous = os.environ.copy()
        try:
            os.environ["HOME"] = str(home)
            for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                         "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT",
                         "OPENCODE_PERMISSION", "AGENT_TOOLCHAIN_STATE_DIR"):
                os.environ.pop(name, None)
            assert subprocess.run([args.opencode, "--version"], text=True, capture_output=True,
                                  timeout=10).stdout.strip() == version
            with mock.patch.object(enable.source, "materialize_source", return_value=(bundle, native)):
                result = enable.enable(workspace=project, executable=args.opencode)
            assert result["status"] == "active" and result["changed"] is True
            config_dir, data_dir, state_dir, cache_root = control.canonical_paths(project)
            assert config_dir == project / ".opencode"
            assert (config_dir / "plugins" / enable.pilot.PLUGIN_NAME).is_file()
            assert not (home / ".config/opencode/plugins" / enable.pilot.PLUGIN_NAME).exists()
            expected = enable.pilot.validate_artifacts(
                pilot_bundle_dir=bundle, native_artifact_dir=native,
                installed_version=version, installed_platform="linux",
            )["permission"]
            effective_project, effective_sibling = resolved_from_server(
                args.opencode, project, sibling,
            )
            assert effective_project.get("permission") == expected, "P0_EFFECTIVE_POLICY_MISMATCH"
            assert any(enable.pilot.PLUGIN_NAME in str(item)
                       for item in (effective_project.get("plugin") or [])), "P0_EFFECTIVE_PLUGIN_MISSING"
            assert not effective_sibling.get("permission"), "P0_POLICY_ESCAPED_PROJECT"
            assert not any(enable.pilot.PLUGIN_NAME in str(item)
                           for item in (effective_sibling.get("plugin") or [])), "P0_PLUGIN_ESCAPED_PROJECT"
            assert control.status(config_dir=config_dir, data_dir=data_dir,
                                  state_dir=state_dir, cache_root=cache_root)["status"] == "active"
            # The test used an already-checked-out exact artifact rather than
            # the network cache, so the rollback calls the same reconciler.
            rollback = enable.pilot.disable_pilot(
                pilot_bundle_dir=bundle, native_artifact_dir=native,
                installed_version=version, config_dir=config_dir,
                data_dir=data_dir, state_dir=state_dir,
            )
            assert rollback["status"] == "disabled"
            assert not (config_dir / "plugins" / enable.pilot.PLUGIN_NAME).exists()
            assert not (config_dir / "opencode.jsonc").exists()
            print(json.dumps({"result": "PASS", "version": version,
                              "project_scoped": True, "rollback": "PASS"}))
        finally:
            os.environ.clear()
            os.environ.update(previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
