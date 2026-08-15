from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_manifest import MANIFEST_SCHEMA, empty_manifest, load_manifest, save_manifest  # noqa: E402


class ManifestV2Tests(unittest.TestCase):
    def test_missing_manifest_starts_as_v2_without_pending_migration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            manifest, error, pending = load_manifest(path)
            self.assertIsNone(error)
            self.assertFalse(pending)
            self.assertEqual(manifest, empty_manifest())
            self.assertFalse(path.exists())

    def test_v1_migrates_in_memory_without_touching_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            legacy = {
                "schema": 1,
                "managed_files": {
                    "OpenCode config": {"path": "/tmp/opencode.jsonc", "sha256": "abc", "source": "test"}
                },
                "credentials": {
                    "routerai": {"provider": "routerai", "mode": "external-file", "path": "/tmp/key"}
                },
                "legacy_metadata": {"preserve": True},
            }
            original = (json.dumps(legacy, indent=2) + "\n").encode("utf-8")
            path.write_bytes(original)

            manifest, error, pending = load_manifest(path)
            self.assertIsNone(error)
            self.assertTrue(pending)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(manifest["schema"], MANIFEST_SCHEMA)
            self.assertEqual(manifest["managed_files"], legacy["managed_files"])
            self.assertEqual(manifest["credentials"], legacy["credentials"])
            self.assertEqual(manifest["legacy_metadata"], legacy["legacy_metadata"])
            self.assertEqual(manifest["managed_tools"], {})
            self.assertEqual(manifest["managed_path_entries"], {})

    def test_persisted_v2_is_stable_and_not_migrated_again(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({"schema": 1, "managed_files": {}}), encoding="utf-8")
            manifest, error, pending = load_manifest(path)
            self.assertIsNone(error)
            self.assertTrue(pending)
            save_manifest(path, manifest)
            first = path.read_bytes()

            loaded, error, pending = load_manifest(path)
            self.assertIsNone(error)
            self.assertFalse(pending)
            self.assertEqual(loaded, manifest)
            save_manifest(path, loaded)
            self.assertEqual(path.read_bytes(), first)

    def test_v2_requires_all_ownership_sections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({"schema": 2, "managed_files": {}}), encoding="utf-8")
            _manifest, error, pending = load_manifest(path)
            self.assertFalse(pending)
            self.assertIsNotNone(error)
            self.assertIn("credentials", error or "")

    def test_unknown_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({"schema": 99, "managed_files": {}}), encoding="utf-8")
            _manifest, error, pending = load_manifest(path)
            self.assertFalse(pending)
            self.assertIn("unsupported", error or "")


if __name__ == "__main__":
    unittest.main()
