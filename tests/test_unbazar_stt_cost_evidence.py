import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_unbazar_stt_costs import inspect, main, number, save_report


class CostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'synthetic.sqlite3'
        self.conn = sqlite3.connect(self.path)
        self.conn.execute('''CREATE TABLE transcription_jobs(
            id TEXT,provider_model TEXT,cost REAL,duration_seconds REAL,created_at TEXT,status TEXT,
            transcript TEXT,file_id TEXT,user_id INTEGER)''')
        self.add()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add(self, *, cost=0.0003500706456, seconds=1.0, model='openai/whisper-large-v3-turbo',
            created='2026-10-29T00:00:00+03:00', state='done'):
        self.conn.execute('INSERT INTO transcription_jobs VALUES(?,?,?,?,?,?,?,?,?)',
            ('NEVER_EXPORT_ID',model,cost,seconds,created,state,'NEVER_EXPORT_TRANSCRIPT','NEVER_EXPORT_FILE',987654321))
        self.conn.commit()

    def test_read_only_metadata_and_legacy_precision_without_content_or_identifiers(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        report = inspect(self.path)
        self.assertEqual(report['inference_requests'], 0)
        self.assertEqual(report['observations'][0]['day_utc'], '2026-10-28')
        self.assertEqual(report['observations'][0]['cost_precision'], 'legacy-approximate')
        text = json.dumps(report)
        for forbidden in ('NEVER_EXPORT', '987654321', str(self.path)):
            self.assertNotIn(forbidden, text)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_compatible_wal_reads_committed_metadata_without_checkpoint_or_missing_rows(self):
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.add(cost=0.001, seconds=10)
        self.assertEqual(inspect(self.path)['rows_examined'], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM transcription_jobs').fetchone()[0], 2)

    def test_exact_text_is_preferred_and_retained_without_float_conversion(self):
        self.conn.execute('ALTER TABLE transcription_jobs ADD COLUMN cost_rub TEXT')
        self.conn.execute("UPDATE transcription_jobs SET cost_rub='0.000000000001234567890123456789'")
        self.conn.commit()
        value = inspect(self.path)['observations'][0]
        self.assertEqual(value['reported_cost_rub'], '1.234567890123456789E-12')
        self.assertEqual(value['cost_precision'], 'decimal-text')

    def test_bounded_sample_only_known_models_and_successful_jobs(self):
        self.add(model='synthetic/unrelated')
        self.add(state='error')
        self.add()
        self.assertEqual(inspect(self.path,limit=1)['rows_examined'],1)
        self.assertEqual(inspect(self.path)['rows_examined'],2)
        for limit in (True,0,201):
            with self.subTest(limit=limit),self.assertRaises(ValueError):inspect(self.path,limit=limit)

    def test_invalid_or_missing_numeric_and_date_metadata_are_not_reinterpreted(self):
        for kwargs in ({'cost':None},{'cost':-1},{'cost':'Infinity'},{'seconds':0},
                       {'seconds':-1},{'seconds':90000},{'created':'NEVER_EXPORT_BAD_DATE'},
                       {'created':'2026-10-29'}):
            self.add(**kwargs)
        report=inspect(self.path)
        self.assertEqual(len(report['observations']),1)
        self.assertEqual(report['rows_skipped'],8)

    def test_extreme_exponents_in_text_do_not_expand_output_or_overflow_arithmetic(self):
        for raw in ('1e-1000000000', '1e1000000000'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):number(raw)

    def test_missing_database_is_not_created_and_unsupported_schema_is_refused(self):
        missing=self.root/'missing.sqlite3'
        self.assertEqual(main(['--database',str(missing),'--output',str(self.root/'missing.json')]),1)
        self.assertFalse(missing.exists());self.assertFalse((self.root/'missing.json').exists())
        other=self.root/'other.sqlite3'
        with sqlite3.connect(other) as conn:conn.execute('CREATE TABLE unrelated(value TEXT)')
        with self.assertRaises(ValueError):inspect(other)

    def test_report_is_private_exclusive_and_cannot_overwrite_source(self):
        report=inspect(self.path);output=self.root/'safe.json'
        save_report(report,output)
        if os.name!='nt':self.assertEqual(output.stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError):save_report(report,output)
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):save_report(report,self.path)
        self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':unittest.main()
