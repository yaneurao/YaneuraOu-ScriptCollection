import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'csa_to_hcpe.py'


class CsaInputAliasTests(unittest.TestCase):
    def test_output_alias_does_not_destroy_csa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'game.csa'
            alias = root / 'output.hcpe'
            source.write_bytes(b'V2.2\nN+Black\nN-White\n')
            for kind in ('same', 'hardlink', 'symlink'):
                with self.subTest(kind=kind):
                    if alias.exists() or alias.is_symlink():
                        alias.unlink()
                    output = source if kind == 'same' else alias
                    if kind == 'hardlink':
                        os.link(source, alias)
                    elif kind == 'symlink':
                        try:
                            alias.symlink_to(source)
                        except OSError:
                            continue
                    result = subprocess.run([sys.executable, str(SCRIPT), str(root), str(output)],
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('output must not be the same file', result.stderr)
                    self.assertEqual(source.read_bytes(), b'V2.2\nN+Black\nN-White\n')
