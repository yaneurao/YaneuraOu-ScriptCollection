import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'concat_hcpe3.py'


class ConcatInputAliasTests(unittest.TestCase):
    def test_force_rejects_output_or_manifest_linked_to_input(self):
        for name in ('merged-00001.hcpe3', 'merged-manifest.tsv'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_dir, output_dir = root / 'source', root / 'output'
                source_dir.mkdir()
                output_dir.mkdir()
                source = source_dir / 'input.hcpe3'
                source.write_bytes(b'original')
                os.link(source, output_dir / name)
                result = subprocess.run([sys.executable, str(SCRIPT), '-s', str(source_dir), '1',
                                         '-o', str(output_dir), '--force'], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('output must not be the same file as an input', result.stderr)
                self.assertEqual(source.read_bytes(), b'original')
