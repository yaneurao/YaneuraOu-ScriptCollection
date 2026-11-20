import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'interleave_teacher_files.py'


class InterleaveInputAliasTests(unittest.TestCase):
    def test_manifest_hardlink_to_input_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir, output_dir = root / 'source', root / 'output'
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / 'input.hcpe3'
            source.write_bytes(b'original')
            os.link(source, output_dir / 'interleaved-manifest.tsv')
            result = subprocess.run([sys.executable, str(SCRIPT), '-s', str(source_dir), '-o', str(output_dir),
                                     '--force'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('manifest must not be the same file as an input', result.stderr)
            self.assertEqual(source.read_bytes(), b'original')
