#!/usr/bin/env python3
"""Reproducible synthetic output benchmark; leaves host RTK settings/history alone."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    rtk = shutil.which('rtk')
    if not rtk:
        raise SystemExit('Install RTK before running this benchmark')
    with tempfile.TemporaryDirectory(prefix='optimizer-rtk-benchmark-') as temporary:
        root = Path(temporary)
        env = dict(os.environ, HOME=temporary, XDG_CONFIG_HOME=temporary + '/.config',
                   XDG_DATA_HOME=temporary + '/.local/share',
                   RTK_TEE_DIR=temporary + '/tee', RTK_TELEMETRY_DISABLED='1')

        def execute(argv):
            return subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=30)

        version = execute([rtk, '--version']).stdout.decode().strip()
        suite = ('import unittest\nclass Example(unittest.TestCase):\n' +
                 ''.join('    def test_%d(self): self.assertTrue(True)\n' % i for i in range(40)))
        (root / 'test_success.py').write_text(suite)
        (root / 'test_failure.py').write_text(suite +
            '    def test_failure(self): self.fail("EXPECTED_BENCHMARK_FAILURE")\n')
        cases = [
            ('passing unittest', [sys.executable, '-m', 'unittest', '-v', 'test_success'],
             [rtk, 'test', sys.executable, '-m', 'unittest', '-v', 'test_success'], 0),
            ('failing unittest', [sys.executable, '-m', 'unittest', '-v', 'test_failure'],
             [rtk, 'test', sys.executable, '-m', 'unittest', '-v', 'test_failure'], 1),
        ]
        results = []
        for name, raw_command, compact_command, expected_status in cases:
            raw, compact = execute(raw_command), execute(compact_command)
            if raw.returncode != expected_status or compact.returncode != expected_status:
                raise RuntimeError(name + ': unexpected or changed exit status')
            raw_size = len(raw.stdout) + len(raw.stderr)
            compact_size = len(compact.stdout) + len(compact.stderr)
            if expected_status:
                evidence = compact.stdout + compact.stderr
                for path in (root / 'tee').rglob('*'):
                    if path.is_file():
                        evidence += path.read_bytes()
                if b'EXPECTED_BENCHMARK_FAILURE' not in evidence:
                    raise RuntimeError('Failure detail missing from output and recovery files')
            results.append(dict(case=name, raw_bytes=raw_size, compact_bytes=compact_size,
                                reduction_percent=round(100 * (1 - compact_size / raw_size), 1),
                                exit_status=compact.returncode))
        print(json.dumps(dict(version=version, measurement='output bytes, not billed tokens',
                              synthetic=True, results=results), indent=2))


if __name__ == '__main__':
    main()
