"""Bootstrap byte pins are checked portably; native behavior runs on Windows."""
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeBootstrapContractTests(unittest.TestCase):
    def test_context_fixture_uses_the_exact_inactive_component_pin(self):
        import setup_yc_owner_component as component
        pins = json.loads((ROOT / 'tests/yc59_context_component_pins.json').read_bytes())
        self.assertEqual(pins['source_ref'], component.SOURCE_REF)
        self.assertEqual(pins['source_map'], component.SOURCES)
        self.assertEqual(pins['manifest'], component.MANIFEST)
        self.assertEqual(pins['manifest_json'].encode(), component.canonical(component.MANIFEST) + b'\n')
        self.assertFalse(component.MANIFEST['execution_enabled'])

    def test_native_source_pin_matches_exact_compiled_bytes(self):
        seed = (ROOT / "native/yc_owner_bootstrap.ps1").read_text(encoding="utf-8")
        expected = re.search(r"(?m)^\$NativeSha256 = '([0-9a-f]{64})'$", seed)
        self.assertIsNotNone(expected)
        self.assertEqual(expected.group(1), hashlib.sha256((ROOT / "native/yc_owner_native.cs").read_bytes()).hexdigest())

    def test_installed_core_publishers_include_native_payload(self):
        import bootstrap_core
        import toolchainctl
        self.assertIn("native", bootstrap_core.REQUIRED_TREES)
        self.assertIn("native", toolchainctl._CORE_REQUIRED_TREES)
        payload = {name for name, _ in bootstrap_core._iter_source_files(ROOT)}
        self.assertIn("native/yc_owner_native.cs", payload)
        self.assertIn("native/yc_owner_bootstrap.ps1", payload)


if __name__ == "__main__":
    unittest.main()
