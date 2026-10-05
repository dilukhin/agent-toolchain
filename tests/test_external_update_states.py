from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_external_updates as updates
from setup_inventory import ExternalCliInventory, ExternalCliInstance, ExternalCliSpec


def inventory(name="opencode", provider="standalone", version="opencode v2.0.22", *, missing=False, conflict=False):
    instance = ExternalCliInstance(Path("/bin") / name, Path("/bin") / name, version,
                                   provider, "test", "opencode-ai" if provider == "npm" else None,
                                   "user", True)
    return ExternalCliInventory(ExternalCliSpec(name, name.title()), () if missing else (instance,),
                                None if missing else instance, conflict,
                                "npm install -g opencode-ai@latest" if provider == "npm" else None)


class ExternalUpdateStateTests(unittest.TestCase):
    def refresh(self, items):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            updates, "common_external_cli_inventory", return_value=items,
        ):
            path = Path(td) / "cache.json"
            result = updates.refresh(path=path, timeout=2)
            self.assertEqual(result, updates.load_cache(path))
            self.assertEqual(list(Path(td).iterdir()), [path])
            return result["tools"]

    def test_missing_conflict_and_unsupported_are_distinct_without_lookups(self):
        cases = [(inventory("codex", missing=True), "not_installed"),
                 (inventory(conflict=True), "conflict"),
                 (inventory(provider="unknown"), "unsupported"),
                 (inventory(version="1.18.34"), "unsupported"),
                 (inventory(version="2.1.0-beta.1"), "unsupported"),
                 (inventory(version=None), "unsupported")]
        for item, status in cases:
            with self.subTest(status=status, version=item.active.version if item.active else None), \
                 mock.patch.object(updates, "_latest") as latest:
                record = self.refresh({item.spec.command: item})[item.spec.command]
            latest.assert_not_called()
            self.assertEqual(record["status"], status)
            self.assertIsNone(record["error"])
            self.assertIsNone(record["latest_version"])
            self.assertIsNone(record["advice"])
            self.assertTrue(record["reason"])

    def test_lookup_error_is_isolated_from_other_tools(self):
        with mock.patch.object(updates, "_latest", side_effect=OSError("missing npm")):
            records = self.refresh({"opencode": inventory(provider="npm"),
                                    "codex": inventory("codex", missing=True)})
        self.assertEqual(records["opencode"]["status"], "error")
        self.assertEqual(records["codex"]["status"], "not_installed")
        self.assertTrue(records["opencode"]["error"])
        self.assertNotIn("missing npm", records["opencode"]["error"])

    def test_timeout_is_an_error_and_not_a_success_or_unsupported(self):
        with mock.patch.object(updates, "_latest", side_effect=subprocess.TimeoutExpired("npm", 2)):
            record = self.refresh({"opencode": inventory(provider="npm")})["opencode"]
        self.assertEqual(record["status"], "error")
        self.assertIsNone(record["advice"])

    def test_malformed_package_version_is_an_error(self):
        with mock.patch.object(updates, "_latest", return_value=("['2.0.23']", None)):
            record = self.refresh({"opencode": inventory(provider="npm")})["opencode"]
        self.assertEqual(record["status"], "error")
        self.assertIsNone(record["latest_version"])

    def test_standalone_request_uses_official_bounded_metadata_without_installer(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"version":"2.0.23","metadata":{"package":"@opencode/cli"}}'
        with mock.patch.object(updates.urllib.request, "urlopen", return_value=response) as urlopen, \
             mock.patch.object(updates, "run") as run:
            record = self.refresh({"opencode": inventory()})["opencode"]
        self.assertEqual(urlopen.call_args.args[0].full_url, updates.OPENCODE_LATEST_URL)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2)
        response.read.assert_called_once_with(updates.OPENCODE_LATEST_MAX_BYTES + 1)
        run.assert_not_called()
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["latest_version"], "2.0.23")
        self.assertEqual(record["advice"], "opencode upgrade")

    def test_invalid_metadata_and_network_failures_are_not_current(self):
        payloads = [b"bad", b"\xff", b"[]", b"{}",
                    b'{"version":"2.0.23","package":"@opencode/cli"}',
                    b'{"version":"2.0.23","metadata":null}',
                    b'{"version":"2.0.23","metadata":[]}',
                    b'{"version":"2.0.23","metadata":{"package":[]}}',
                    b'{"version":"2.0.23","metadata":{"package":{}}}',
                    b'{"version":"2.0.23","metadata":{"package":"untrusted/cli"}}',
                    b'{"version":"1.18.34","metadata":{"package":"@opencode/cli"}}',
                    b'{"version":"2.1.0-beta.1","metadata":{"package":"@opencode/cli"}}',
                    b"x" * (updates.OPENCODE_LATEST_MAX_BYTES + 1)]
        for payload in payloads:
            with self.subTest(payload=payload[:40]):
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = payload
                with mock.patch.object(updates.urllib.request, "urlopen", return_value=response):
                    self.assertIsNotNone(updates._latest(inventory(), 2)[1])
        for error in [TimeoutError(), urllib.error.URLError("credential=private"),
                      urllib.error.HTTPError(updates.OPENCODE_LATEST_URL, 503, "Unavailable", {}, None)]:
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(updates.urllib.request, "urlopen", side_effect=error):
                version, message = updates._latest(inventory(), 2)
            self.assertIsNone(version)
            self.assertTrue(message)
            self.assertNotIn("private", message)

    def test_official_update_worker_artifact_shape(self):
        # services/update/src/index.ts returns decodeArtifact(row), preserving
        # metadata nesting; the V2 updater reads data.metadata.package.
        for package in ("@opencode/cli", "@opencode-ai/cli"):
            artifact = {
                "channel": "latest", "name": "cli", "distribution": "npm",
                "version": "2.0.23", "metadata": {"package": package,
                    "github": {"sha": "a" * 40, "run_id": "12345"}},
                "active": True, "minimum": False,
                "time_created": 1790928000000, "time_updated": 1790928000000,
            }
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps(artifact).encode("utf-8")
            with self.subTest(package=package), \
                 mock.patch.object(updates.urllib.request, "urlopen", return_value=response):
                self.assertEqual(updates._latest(inventory(), 2), ("2.0.23", None))

    def test_advisory_normalizes_versions_and_never_recommends_downgrade(self):
        item = inventory()
        record = {"status": "ok", "provider": "standalone", "installed_version": "v2.0.22",
                  "latest_version": "2.0.22", "advice": "opencode upgrade"}
        self.assertIsNone(updates.advisory(item, record))
        self.assertIsNone(updates.advisory(item, dict(record, latest_version="2.0.21")))
        self.assertIsNone(updates.advisory(item, dict(record, latest_version="2.0.22+build.2")))
        message = updates.advisory(item, dict(record, latest_version="v2.0.23"))
        self.assertIn("2.0.22 -> 2.0.23", message)
        self.assertIn("opencode upgrade", message)
        self.assertIn("refresh", updates.advisory(inventory(version="2.0.23"), record))
        self.assertIn("refresh", updates.advisory(item, dict(record, provider="npm")))
        self.assertNotIn("upgrade", updates.advisory(inventory(conflict=True), record))
        self.assertIsNone(updates.advisory(inventory(missing=True), record))

    def test_prerelease_order_and_numeric_version_order(self):
        versions = ["2.0.22-alpha.2", "2.0.22-alpha.10", "2.0.22-beta", "2.0.22", "2.0.23", "2.0.100"]
        self.assertEqual(sorted(reversed(versions), key=updates._version_key), versions)

    def test_cli_explains_neutral_states(self):
        import argparse
        import toolchainctl
        item = inventory("codex", missing=True)
        with mock.patch.object(toolchainctl, "refresh", return_value={"tools": {
            "codex": {"status": "not_installed", "reason": "Программа не найдена в PATH."},
        }}), mock.patch.object(toolchainctl, "common_external_cli_inventory", return_value={"opencode": inventory(), "codex": item}), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(toolchainctl._updates_phase(argparse.Namespace(updates_command="refresh")), 0)
        self.assertIn("status=not_installed", output.getvalue())
        self.assertIn("Программа не найдена в PATH.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
