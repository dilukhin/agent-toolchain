import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import setup_yc_owner_component as library
import setup_yc_owner_release as release

ROOT = Path(__file__).resolve().parents[1]


class OwnerApplicationTests(unittest.TestCase):
    def test_exact_package_and_idempotence_preserve_previous_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            result = release.prepare(data)
            self.assertEqual(result['status'],'owner_action_required')
            self.assertFalse(result['execution_enabled'])
            self.assertFalse(result['trusted_owner_placement_proven'])
            self.assertEqual(release.prepare(data),result)
            seed = Path(result['seed_path'])
            self.assertEqual(hashlib.sha256(seed.read_bytes()).hexdigest(),release.SEED_SHA256)
            previous = seed.parent.parent/'sha256-previous'
            previous.mkdir()
            (previous/'keep').write_text('previous reviewed bytes')
            release.prepare(data)
            self.assertEqual((previous/'keep').read_text(),'previous reviewed bytes')

    def test_unknown_directory_corrupt_seed_and_partial_journal_are_not_repaired(self):
        for defect in ('unknown','seed','journal'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                data=Path(tmp)/'data'
                if defect=='unknown':
                    root=release._root(data);root.mkdir(parents=True);(root/'foreign').write_text('keep')
                else:
                    result=release.prepare(data);package=Path(result['package'])
                    (package/('owner-start.ps1' if defect=='seed' else 'journal.json')).write_bytes(b'changed')
                with self.assertRaises(library.ComponentConflict):
                    release.prepare(data)

    def test_source_mismatch_refuses_before_any_output_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'changed.ps1';source.write_text('unreviewed source')
            data=Path(tmp)/'data'
            with self.assertRaises(library.ComponentConflict):
                release.prepare(data,source=source)
            self.assertFalse(data.exists())

    def test_parallel_apply_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp)/'data';release.prepare(data)
            with library._lock(release._root(data)):
                with self.assertRaises(library.ComponentConflict):
                    release.prepare(data)
