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

from setup_external_updates import cache_fresh, load_cache, refresh  # noqa: E402
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

    def test_refresh_writes_atomic_schema_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "updates.json"
            fake = {"opencode": mock.Mock(active=None), "codex": mock.Mock(active=None)}
            with mock.patch("setup_external_updates.common_external_cli_inventory", return_value=fake):
                result = refresh(path=path)
            self.assertEqual(result["schema"], 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 1)


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
             mock.patch("proxy_tools.refresh", side_effect=OSError("offline")), \
             mock.patch("proxy_tools.socks5_preflight", side_effect=OSError("missing")), \
             mock.patch("proxy_tools.subprocess.Popen") as popen:
            self.assertNotEqual(proxy_tools.launch("opencode", ["--help"]), 0)
        self.assertFalse(any(call.args and call.args[0][0] == str(instance.canonical_path) for call in popen.call_args_list))


if __name__ == "__main__":
    unittest.main()
