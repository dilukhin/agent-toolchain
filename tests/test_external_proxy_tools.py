from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_external_updates import _latest, cache_fresh, load_cache, refresh  # noqa: E402
from setup_inventory import ExecutableInstance, ExternalCliSpec, external_cli_inventory  # noqa: E402
import proxy_tools  # noqa: E402


class ExternalInventoryTests(unittest.TestCase):
    def test_inventory_marks_active_shadowed_and_provider_conflict(self) -> None:
        with mock.patch("setup_inventory.executable_inventory", return_value=[
            ExecutableInstance(Path("C:/ProgramData/chocolatey/bin/opencode.exe"), "1.0", "choco", True),
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "2.0", "npm", False),
        ]):
            inventory = external_cli_inventory(ExternalCliSpec("opencode"))
        self.assertEqual(inventory.active.version, "1.0")
        self.assertTrue(inventory.conflict)
        self.assertIsNone(inventory.update_advice)
        self.assertEqual(len(inventory.instances), 2)


class UpdateCacheTests(unittest.TestCase):
    def test_malformed_and_stale_cache_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "updates.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_cache(path), {"schema": 1, "tools": {}})
            self.assertFalse(cache_fresh({"checked_at": "bad"}))
            self.assertTrue(cache_fresh({"checked_at": 0}, now=0, ttl=1))

    def test_refresh_writes_atomic_schema_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "updates.json"
            fake = {"opencode": mock.Mock(active=None), "codex": mock.Mock(active=None)}
            with mock.patch("setup_external_updates.common_external_cli_inventory", return_value=fake):
                result = refresh(path=path)
            self.assertEqual(result["schema"], 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 1)

    def test_provider_versions_use_bounded_payloads(self) -> None:
        npm_item = mock.Mock(active=mock.Mock(provider="npm", package="opencode-ai"), conflict=False)
        npm_item.spec.command = "opencode"
        with mock.patch("setup_external_updates.run", return_value=mock.Mock(returncode=0, stdout='"1.2.3"', stderr="")):
            self.assertEqual(_latest(npm_item, 3), ("1.2.3", None))

        choco_item = mock.Mock(active=mock.Mock(provider="chocolatey"), conflict=False)
        choco_item.spec.command = "opencode"
        with mock.patch("setup_external_updates.shutil.which", return_value="choco"), mock.patch(
            "setup_external_updates.run",
            return_value=mock.Mock(returncode=0, stdout="opencode|1.18.23\n", stderr=""),
        ):
            self.assertEqual(_latest(choco_item, 3), ("1.18.23", None))

    def test_choco_search_exit_code_two_means_no_results(self) -> None:
        choco_item = mock.Mock(active=mock.Mock(provider="chocolatey", version="1.18.18"), conflict=False)
        choco_item.spec.command = "opencode"
        with mock.patch("setup_external_updates.shutil.which", return_value="choco"), mock.patch(
            "setup_external_updates.run",
            return_value=mock.Mock(returncode=2, stdout="", stderr=""),
        ):
            latest, error = _latest(choco_item, 3)
        self.assertIsNone(latest)
        self.assertIn("no results", error)

    def test_choco_search_success_without_requested_package_fails_closed(self) -> None:
        choco_item = mock.Mock(active=mock.Mock(provider="chocolatey", version="1.18.18"), conflict=False)
        choco_item.spec.command = "opencode"
        with mock.patch("setup_external_updates.shutil.which", return_value="choco"), mock.patch(
            "setup_external_updates.run",
            return_value=mock.Mock(returncode=0, stdout="other|2.0\n", stderr=""),
        ):
            latest, error = _latest(choco_item, 3)
        self.assertIsNone(latest)
        self.assertIn("no matching package row", error)


class ProxyLaunchTests(unittest.TestCase):
    def test_preflight_requires_no_auth_handshake(self) -> None:
        class FakeSocket:
            def __init__(self):
                self.sent = b""
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def sendall(self, data): self.sent += data
            def recv(self, size): return b"\x05\x00"

        fake = FakeSocket()
        with mock.patch("proxy_tools.socket.create_connection", return_value=fake):
            proxy_tools.socks5_preflight()
        self.assertEqual(fake.sent, b"\x05\x01\x00")

    def test_missing_socks_does_not_start_child_or_tunnelctl(self) -> None:
        instance = mock.Mock(canonical_path=Path("/bin/opencode"))
        inventory = mock.Mock(active=instance)
        with mock.patch("proxy_tools.external_cli_inventory", return_value=inventory), \
             mock.patch("proxy_tools.load_cache", return_value={"tools": {}}), \
             mock.patch("proxy_tools._show_routerai_status"), \
             mock.patch("proxy_tools.socks5_preflight", side_effect=OSError("missing")), \
             mock.patch("proxy_tools.subprocess.Popen") as popen:
            self.assertNotEqual(proxy_tools.launch("opencode", ["--help"]), 0)
        self.assertFalse(any(call.args and call.args[0][0] == str(instance.canonical_path) for call in popen.call_args_list))

    def test_routerai_status_failure_never_blocks_launch_path(self) -> None:
        with mock.patch("proxy_tools.get_routerai_status", side_effect=RuntimeError("boom")):
            proxy_tools._show_routerai_status()

    def test_health_is_exact_and_help_is_forwarded(self) -> None:
        with mock.patch("proxy_tools.launch", return_value=37) as launch:
            self.assertEqual(proxy_tools.main(["opencode", "--health"]), 0)
            self.assertEqual(proxy_tools.main(["opencode", "--help"]), 37)
            launch.assert_called_once_with("opencode", ["--help"])

    def test_socks_reply_parser_reads_ipv4_and_domain_sequentially(self) -> None:
        class ReplySocket:
            def __init__(self, reply): self.reply, self.sent = reply, b""
            def sendall(self, data): self.sent += data
            def recv(self, size):
                data, self.reply = self.reply[:size], self.reply[size:]
                return data
            def settimeout(self, value): pass
            def close(self): pass

        for reply in (b"\x05\x00\x00\x01\x7f\x00\x00\x01\x1f\x90", b"\x05\x00\x00\x03\x03abc\x1f\x90"):
            sock = ReplySocket(b"\x05\x00" + reply)
            with mock.patch("proxy_tools.socket.create_connection", return_value=sock):
                result = proxy_tools._socks_connect("example.test", 443)
            self.assertIs(result, sock)


if __name__ == "__main__":
    unittest.main()