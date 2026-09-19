from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TARGET = 'AmneziaWGTunnel$alice'


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class WindowsSafetyWorkflowTests(unittest.TestCase):
    def test_documented_verifier_preserves_literal_and_observes_actual_state(self) -> None:
        names = ('powershell.exe', 'pwsh.exe') if os.name == 'nt' else ('pwsh',)
        shells = [shutil.which(name) for name in names]
        if os.name == 'nt':
            self.assertTrue(all(shells), f'Windows validation requires both shells: {names}')
        elif not any(shells):
            self.skipTest('PowerShell is unavailable; Windows CI exercises the literal transport')

        doc = (ROOT / 'docs/windows_safety_workflow_ru.md').read_text(encoding='utf-8')
        match = re.search(
            r'<!-- executable-example: literal-service-verifier -->\s*```powershell\n(.*?)\n```',
            doc, re.DOTALL,
        )
        self.assertIsNotNone(match)
        example = match.group(1)

        for shell in filter(None, shells):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prepare = root / 'prepare.ps1'
                prepare.write_text("$alice = 'WRONG_INTERPOLATED_TARGET'\n" + example, encoding='utf-8-sig')

                def run_file(path: Path) -> subprocess.CompletedProcess:
                    return subprocess.run(
                        [shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(path)],
                        cwd=root, capture_output=True, timeout=30,
                    )

                prepared = run_file(prepare)
                self.assertEqual(prepared.returncode, 0, prepared.stderr.decode('utf-8', errors='replace'))
                verifier = root / 'verify-service.ps1'
                payload = verifier.read_bytes()
                self.assertIn(TARGET.encode(), payload)
                self.assertNotIn(b'WRONG_INTERPOLATED_TARGET', payload)
                repeated = run_file(prepare)
                self.assertNotEqual(repeated.returncode, 0)
                self.assertEqual(verifier.read_bytes(), payload)

                for returned_name, status in ((TARGET, 'Running'), (TARGET, 'Stopped'), ('other-service', 'Running'), (None, None)):
                    with self.subTest(returned_name=returned_name, status=status):
                        harness = root / 'observe.ps1'
                        result = ("throw 'Synthetic service missing'" if returned_name is None else
                                  '@{ Name = ' + ps_literal(returned_name) + '; Status = ' + ps_literal(status) + ' }')
                        harness.write_text(
                            "$ErrorActionPreference = 'Stop'\n$alice = 'WRONG_INTERPOLATED_TARGET'\n"
                            "function Get-Service {\nparam([string]$Name, [string]$ErrorAction)\n"
                            "if ($Name -cne 'AmneziaWGTunnel$alice') { throw 'Target was interpolated' }\n"
                            + result + '\n}\n& ' + ps_literal(str(verifier)) + '\n',
                            encoding='utf-8-sig',
                        )
                        before = {p.name: p.read_bytes() for p in root.iterdir()}
                        observed = run_file(harness)
                        if returned_name == TARGET:
                            self.assertEqual(observed.returncode, 0, observed.stderr.decode('utf-8', errors='replace'))
                            self.assertEqual(json.loads(observed.stdout.decode('utf-8-sig')), {'target': TARGET, 'status': status})
                        else:
                            self.assertNotEqual(observed.returncode, 0)
                        self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
