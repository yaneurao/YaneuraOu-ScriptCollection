import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'concat_hcpe3_round_robin.py'


class RoundRobinInputAliasTests(unittest.TestCase):
    def test_output_and_manifest_hardlinks_are_rejected(self):
        for name in ('mixed-00001.hcpe3', 'mixed-manifest.tsv'):
            for shuffle in (False, True):
                with self.subTest(name=name, shuffle=shuffle), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source_dir, output_dir = root / 'source', root / 'output'
                    source_dir.mkdir()
                    output_dir.mkdir()
                    source = source_dir / 'input.hcpe3'
                    source.write_bytes(b'original')
                    os.link(source, output_dir / name)
                    command = [sys.executable, str(SCRIPT), '--source', str(source_dir),
                               '--output', str(output_dir), '--force']
                    if shuffle:
                        command.append('--shuffle-positions')
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('output must not be the same file as an input', result.stderr)
                    self.assertEqual(source.read_bytes(), b'original')
