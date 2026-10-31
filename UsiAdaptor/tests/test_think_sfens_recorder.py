import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'UsiAdaptor' / 'ThinkSfensRecorder.py'
sys.path.insert(0, str(ROOT / 'UsiAdaptor'))
from ThinkSfensRecorder import ThinkSfensRecorder


class RecorderTests(unittest.TestCase):
    def test_append_to_file_without_final_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'think.txt'
            path.write_text('startpos moves 7g7f', encoding='utf-8')
            recorder = ThinkSfensRecorder(path, dedupe=True)
            recorder.open()
            try:
                recorder.record_if_position('position startpos moves 2g2f')
            finally:
                recorder.close()
            self.assertEqual(path.read_text().splitlines(),
                             ['startpos moves 7g7f', 'startpos moves 2g2f'])

    @unittest.skipIf(os.name == 'nt', 'fixture uses a POSIX executable')
    def test_relative_engine_path_starts_from_own_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'engines').mkdir()
            engine = root / 'engines' / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                              'for line in sys.stdin:\n'
                              ' if line.strip() == "usi": print("usiok", flush=True)\n'
                              ' if line.strip() == "quit": break\n')
            engine.chmod(0o755)
            result = subprocess.run([sys.executable, str(SCRIPT), '--engine-path', 'engines/mock-engine',
                                     '--output', 'think.txt'], cwd=root, input='usi\nquit\n',
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('usiok', result.stdout)


if __name__ == '__main__':
    unittest.main()
