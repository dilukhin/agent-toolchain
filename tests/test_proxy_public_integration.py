from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_manifest  # noqa: E402
from setup_lib import Reporter  # noqa: E402
import setup_managed_tools  # noqa: E402
from setup_managed_tools import reconcile_builtin_tool  # noqa: E402
from setup_tools import HealthCheckSpec, ToolSpec  # noqa: E402


class _SocksProbe(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(3)
        self.request.sendall(b"\x05\x00")


class PublicProxyIntegrationTests(unittest.TestCase):
    def test_builtin_payload_is_content_addressed_and_check_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-toolchain-builtin-e2e-") as td:
            old_data, old_bin = os.environ.get("AGENT_TOOLCHAIN_DATA_DIR"), os.environ.get("AGENT_TOOLCHAIN_BIN_DIR")
            os.environ["AGENT_TOOLCHAIN_DATA_DIR"], os.environ["AGENT_TOOLCHAIN_BIN_DIR"] = str(Path(td) / "data"), str(Path(td) / "bin")
            try:
                spec = ToolSpec(
                    name="proxy-tools", source="builtin", runtime="python-builtin",
                    update_policy="bundled-with-setup", entrypoints=("opencode-proxied", "codex-proxied"),
                    health_contract=(HealthCheckSpec(("opencode-proxied", "--health")), HealthCheckSpec(("codex-proxied", "--health"))),
                    platforms=("windows", "linux"), module="proxy_tools",
                )
                original = setup_managed_tools._builtin_payload(spec)
                payload_a = dict(original, **{"payload-version.txt": b"A"})
                payload_b = dict(original, **{"payload-version.txt": b"B"})
                manifest = setup_manifest.empty_manifest()
                with mock.patch.object(setup_managed_tools, "_builtin_payload", return_value=payload_a):
                    self.assertTrue(reconcile_builtin_tool(spec, Reporter(), check=False, manifest=manifest))
                    release_a = Path(manifest["managed_tools"]["proxy-tools"]["runtime_path"])
                    before = {path: path.read_bytes() for path in release_a.rglob("*") if path.is_file()}
                    self.assertFalse(reconcile_builtin_tool(spec, Reporter(), check=False, manifest=manifest))
                with mock.patch.object(setup_managed_tools, "_builtin_payload", return_value=payload_b):
                    self.assertTrue(reconcile_builtin_tool(spec, Reporter(), check=False, manifest=manifest))
                    release_b = Path(manifest["managed_tools"]["proxy-tools"]["runtime_path"])
                    self.assertNotEqual(release_a, release_b)
                    self.assertTrue(release_a.is_dir())
                    self.assertEqual({path: path.read_bytes() for path in release_a.rglob("*") if path.is_file()}, before)
                    snapshot = json.dumps(manifest, sort_keys=True)
                    check_report = Reporter()
                    self.assertFalse(reconcile_builtin_tool(spec, check_report, check=True, manifest=manifest))
                    self.assertEqual(json.dumps(manifest, sort_keys=True), snapshot)

                    tampered_target = release_b / "proxy_tools.py"
                    tampered = tampered_target.read_bytes() + b"\n# tampered\n"
                    tampered_target.write_bytes(tampered)
                    tamper_report = Reporter()
                    self.assertFalse(reconcile_builtin_tool(spec, tamper_report, check=True, manifest=manifest))
                    self.assertTrue(tamper_report.has_conflict)
                    self.assertEqual(tampered_target.read_bytes(), tampered)

                    tampered_target.write_bytes(payload_b["proxy_tools.py"])
                    if os.name != "nt":
                        executable = release_b / "opencode-proxied.py"
                        executable.chmod(executable.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
                        mode_report = Reporter()
                        self.assertFalse(reconcile_builtin_tool(spec, mode_report, check=True, manifest=manifest))
                        self.assertTrue(mode_report.has_conflict)
            finally:
                if old_data is None: os.environ.pop("AGENT_TOOLCHAIN_DATA_DIR", None)
                else: os.environ["AGENT_TOOLCHAIN_DATA_DIR"] = old_data
                if old_bin is None: os.environ.pop("AGENT_TOOLCHAIN_BIN_DIR", None)
                else: os.environ["AGENT_TOOLCHAIN_BIN_DIR"] = old_bin

    def test_public_entrypoints_are_real_processes_with_isolated_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-toolchain-proxy-e2e-") as td:
            root = Path(td)
            data, bindir, fakebin = root / "data", root / "bin", root / "fakebin"
            result = root / "result.json"
            fakebin.mkdir()
            fake = (
                "import json, os, sys\n"
                "if sys.argv[1:] == ['--version']:\n"
                "    print('fake-cli 1.0')\n"
                "    raise SystemExit(0)\n"
                "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'http': os.environ.get('HTTP_PROXY'), 'all': os.environ.get('ALL_PROXY')}, open(os.environ['FAKE_RESULT'], 'w'))\n"
                "raise SystemExit(37)\n"
            )
            (fakebin / "fake.py").write_text(fake, encoding="utf-8")
            if os.name == "nt":
                for command in ("opencode", "codex"):
                    (fakebin / f"{command}.cmd").write_text(
                        f'@echo off\r\n@"{sys.executable}" "%~dp0fake.py" %*\r\n', encoding="utf-8"
                    )
            else:
                for command in ("opencode", "codex"):
                    target = fakebin / command
                    target.write_text(f"#!{sys.executable}\n{fake}", encoding="utf-8")
                    target.chmod(0o755)

            socks = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SocksProbe)
            thread = threading.Thread(target=socks.serve_forever, daemon=True)
            thread.start()
            old_env = os.environ.copy()
            try:
                os.environ.update({
                    "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                    "AGENT_TOOLCHAIN_BIN_DIR": str(bindir),
                    "AGENT_TOOLCHAIN_SOCKS_HOST": "127.0.0.1",
                    "AGENT_TOOLCHAIN_SOCKS_PORT": str(socks.server_address[1]),
                    "FAKE_RESULT": str(result),
                    "PATH": str(bindir) + os.pathsep + str(fakebin) + os.pathsep + old_env.get("PATH", ""),
                })
                spec = ToolSpec(
                    name="proxy-tools", source="builtin", runtime="python-builtin",
                    update_policy="bundled-with-setup", entrypoints=("opencode-proxied", "codex-proxied"),
                    health_contract=(HealthCheckSpec(("opencode-proxied", "--health")), HealthCheckSpec(("codex-proxied", "--health"))),
                    platforms=("windows", "linux"), module="proxy_tools",
                )
                manifest = setup_manifest.empty_manifest()
                report = Reporter()
                self.assertTrue(reconcile_builtin_tool(spec, report, check=False, manifest=manifest))
                public_paths: list[Path] = []
                for command in ("opencode-proxied", "codex-proxied"):
                    public = bindir / (command + (".cmd" if os.name == "nt" else ""))
                    public_paths.append(public)
                    completed = subprocess.run([str(public), "--help", "--test"], cwd=root, env=os.environ, check=False)
                    self.assertEqual(completed.returncode, 37)
                    payload = json.loads(result.read_text(encoding="utf-8"))
                    self.assertEqual(payload["argv"], ["--help", "--test"])
                    self.assertEqual(payload["cwd"], str(root))
                    self.assertTrue(payload["http"].startswith("http://127.0.0.1:"))
                    self.assertTrue(payload["all"].startswith("socks5://127.0.0.1:"))
                    bridge = urlsplit(payload["http"])
                    self.assertIsNotNone(bridge.hostname)
                    self.assertIsNotNone(bridge.port)
                    with self.assertRaises(OSError):
                        socket.create_connection((bridge.hostname, bridge.port), timeout=0.25)
                    result.unlink()
                    self.assertEqual(subprocess.run([str(public), "--health"], cwd=root, env=os.environ).returncode, 0)
                    self.assertFalse(result.exists())

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unavailable:
                    unavailable.bind(("127.0.0.1", 0))
                    os.environ["AGENT_TOOLCHAIN_SOCKS_PORT"] = str(unavailable.getsockname()[1])
                    for public in public_paths:
                        result.unlink(missing_ok=True)
                        completed = subprocess.run([str(public), "--missing-socks"], cwd=root, env=os.environ, check=False)
                        self.assertEqual(completed.returncode, 78)
                        self.assertFalse(result.exists())
            finally:
                os.environ.clear()
                os.environ.update(old_env)
                socks.shutdown()
                socks.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()