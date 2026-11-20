import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'hcpe3_re_eval_from_hcpe.py'


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('onnxruntime', 'numpy', 'cshogi', 'tqdm')),
                     'requires the HCPE re-evaluation dependencies')
class ReevalPathTests(unittest.TestCase):
    def test_output_alias_does_not_destroy_input_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, source, alias = root / 'model.onnx', root / 'input.hcpe', root / 'output.hcpe3'
            model.write_bytes(b'model')
            source.write_bytes(b'input')
            for target in (source, model):
                for kind in ('same', 'hardlink'):
                    with self.subTest(target=target.name, kind=kind):
                        if alias.exists():
                            alias.unlink()
                        if kind == 'hardlink':
                            os.link(target, alias)
                        output = target if kind == 'same' else alias
                        result = subprocess.run([sys.executable, str(SCRIPT), str(model), str(source), str(output)],
                                                capture_output=True, text=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('output must not be the same file', result.stderr)
                        self.assertEqual(source.read_bytes(), b'input')
                        self.assertEqual(model.read_bytes(), b'model')

    def test_zero_batch_size_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output.hcpe3'
            result = subprocess.run([sys.executable, str(SCRIPT), 'model', 'input', str(output), '--batch-size', '0'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('--batch-size must be positive', result.stderr)
            self.assertFalse(output.exists())
