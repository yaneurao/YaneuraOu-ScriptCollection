import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bookminer_fallback_test', ROOT / 'BookMiner.py')
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


class PetaNextFallbackTest(unittest.TestCase):
    def test_peta_opponent_uses_loaded_book_not_replaced_disk_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk = root / 'previous-peta.db'
            opponent = root / 'opponent.db'
            disk.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {miner.SFEN_START_PLY1}\n2g2f none 0 0\n')
            opponent.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {miner.SFEN_START_PLY1}\n7g7f none 0 0\n')
            with patch.object(miner, 'peta_book', miner.Book()), \
                 patch.object(miner, 'collect_opponent_book_paths', return_value=[str(opponent)]), \
                 patch.object(miner, 'BOOK_DIR', str(root)):
                miner.read_peta_book(str(disk))
                disk.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {miner.SFEN_START_PLY1}\n7g7f none 0 0\n')
                miner.peta_opponent(eval_diff=0, max_book_ply=200, max_step=5)
            lines = (root / 'think_sfens.txt').read_text().splitlines()
            self.assertTrue(any(line.startswith('startpos moves 2g2f,') for line in lines), lines)

    def test_peta_opponent_does_not_load_peta_book_implicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opponent = root / 'opponent.db'
            opponent.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {miner.SFEN_START_PLY1}\n7g7f none 0 0\n')
            with patch.object(miner, 'peta_book', miner.Book()), \
                 patch.object(miner, 'resolve_peta_book_path', side_effect=AssertionError('unexpected peta disk lookup')), \
                 patch.object(miner, 'collect_opponent_book_paths', return_value=[str(opponent)]), \
                 patch.object(miner, 'BOOK_DIR', str(root)):
                miner.peta_opponent(eval_diff=0, max_book_ply=200, max_step=5)

    def test_start_positions_accept_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('# コメント\nstartpos\n', encoding='utf-8-sig')
            self.assertEqual(miner.load_peta_root_positions(str(roots)), [('startpos', miner.SFEN_START_PLY1)])
            miner.peta_next(100, 100, 200, str(roots))
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertTrue(lines)
            self.assertTrue(all(line.startswith('startpos,') for line in lines))
            roots.write_text('# コメント\nstartpos\n', encoding='cp932')
            self.assertEqual(miner.load_peta_root_positions(str(roots)), [('startpos', miner.SFEN_START_PLY1)])

    def test_current_book_extends_old_frontier_and_peta_takes_priority(self):
        peta, normal = miner.Book(), miner.Book()
        root = miner.cshogi.Board()
        sfen0 = miner.trim_sfen_ply(root.sfen())[0]
        peta.body[sfen0] = miner.PositionInfo([miner.MoveInfo('7g7f', 0, 0)], 1)
        normal.body[sfen0] = miner.PositionInfo([miner.MoveInfo('2g2f', 100, 0)], 1)
        root.push_usi('7g7f')
        sfen1 = miner.trim_sfen_ply(root.sfen())[0]
        normal.body[sfen1] = miner.PositionInfo([miner.MoveInfo('3c3d', 0, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', peta), patch.object(miner, 'BOOK_DIR', directory):
            roots = str(Path(directory) / 'absent.txt')
            miner.peta_next(100, 100, 200, roots)
            old = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            miner.peta_next(100, 100, 200, roots, fallback_book=normal)
            new = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
        self.assertTrue(all('startpos moves 7g7f,' in line for line in old))
        self.assertTrue(all('startpos moves 7g7f 3c3d,' in line for line in new))
        self.assertTrue(new)

    def test_fallback_makes_snapshot_and_supports_flipped_hit(self):
        peta, normal = miner.Book(), miner.Book()
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        sfen = miner.trim_sfen_ply(board.sfen())[0]
        flipped = miner.flipped_sfen(sfen)
        normal.body[flipped] = miner.PositionInfo([miner.MoveInfo('7g7f', 20, 0)], 2)
        hit, is_flipped = miner.find_peta_next_position(peta, normal, sfen)
        self.assertTrue(is_flipped)
        self.assertEqual(hit.moveinfos[0].eval, 20)
        normal.body[flipped].moveinfos[0].eval = 30
        self.assertEqual(hit.moveinfos[0].eval, 20)

class PetaNextFallbackGuiTest(unittest.TestCase):
    def test_checkbox_selects_cli_mode_for_manual_and_auto(self):
        from unittest.mock import Mock
        gui_spec = importlib.util.spec_from_file_location('bookminer_fallback_gui_test', ROOT / 'BookMiner-gui.py')
        gui = importlib.util.module_from_spec(gui_spec)
        sys.modules[gui_spec.name] = gui
        gui_spec.loader.exec_module(gui)
        for enabled in (False, True):
            for auto in (False, True):
                instance = Mock()
                instance._get_step2_int_token.side_effect = ['50', '100', '200', '6', '400']
                instance._build_default_settings_command.return_value = 'sd 50 100 200 6 400'
                instance._begin_manual_action.return_value = True
                instance.send_command.return_value = True
                instance.peta_next_normal_fallback.get.return_value = enabled
                with self.subTest(enabled=enabled, auto=auto):
                    self.assertTrue(gui.BookMinerGui.send_peta_next(instance, auto=auto))
                    self.assertEqual(instance.send_command.call_args.args[0],
                                     f'pn 50 100 200 6 400 {int(enabled)}')


if __name__ == '__main__':
    unittest.main()
