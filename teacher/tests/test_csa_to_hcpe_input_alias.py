import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'csa_to_hcpe.py'
sys.path.insert(0, str(SCRIPT.parent))


class CsaInputAliasTests(unittest.TestCase):
    def test_output_write_error_is_not_reported_as_skipped_game(self):
        spec = importlib.util.spec_from_file_location('csa_to_hcpe_test', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'game.csa', root / 'output.hcpe'
            source.write_text('game')
            output.write_bytes(b'previous')
            args = SimpleNamespace(input=source, output=output, no_recursive=False,
                                   include_draw=True, out_maxmove=False, filter_moves=0,
                                   filter_rating=0, uniq=False, progress_interval=0, teacher_eval=0)
            game = SimpleNamespace(endgame='%TORYO', moves=[1])
            with patch.object(module, 'parse_args', return_value=args), \
                 patch.object(module, 'parse_csa_games', return_value=[game]), \
                 patch.object(module, 'write_game_hcpe', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    module.main()
            self.assertEqual(output.read_bytes(), b'previous')
            self.assertFalse(list(root.glob('.output.hcpe.*.tmp')))

    def test_successful_conversion_publishes_complete_output(self):
        import cshogi
        import csa_to_hcpe as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'game.csa', root / 'output.hcpe'
            source.write_text('game')
            output.write_bytes(b'previous')
            args = SimpleNamespace(input=source, output=output, no_recursive=False,
                                   include_draw=True, out_maxmove=False, filter_moves=0,
                                   filter_rating=0, uniq=False, progress_interval=0, teacher_eval=0)
            board = cshogi.Board()
            game = SimpleNamespace(endgame='%TORYO', moves=[board.move_from_usi('7g7f')],
                                   sfen=board.sfen(), win=1)
            with patch.object(module, 'parse_args', return_value=args), \
                 patch.object(module, 'parse_csa_games', return_value=[game]):
                module.main()
            self.assertEqual(output.stat().st_size, module.HCPE.itemsize)

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
