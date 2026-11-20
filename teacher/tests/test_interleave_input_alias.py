import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'interleave_teacher_files.py'


class InterleaveInputAliasTests(unittest.TestCase):
    def test_manifest_must_not_collide_with_output(self):
        for hardlink in (False, True):
            with self.subTest(hardlink=hardlink), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_dir, output_dir = root / 'source', root / 'output'
                source_dir.mkdir()
                output_dir.mkdir()
                source = source_dir / 'input.hcpe3'
                source.write_bytes(b'original')
                output = output_dir / '0001-input.hcpe3'
                manifest = output if not hardlink else output_dir / 'manifest.tsv'
                if hardlink:
                    output.write_bytes(b'old output')
                    os.link(output, manifest)
                result = subprocess.run([sys.executable, str(SCRIPT), '-s', str(source_dir), '-o', str(output_dir),
                                         '--manifest', manifest.name, '--force'], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('manifest must not be the same file as an output', result.stderr)
                self.assertEqual(source.read_bytes(), b'original')
                if hardlink:
                    self.assertEqual(output.read_bytes(), b'old output')
                    self.assertEqual(manifest.read_bytes(), b'old output')
                else:
                    self.assertFalse(output.exists())

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
