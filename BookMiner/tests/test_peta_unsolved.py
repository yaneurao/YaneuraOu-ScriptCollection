import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bookminer_unsolved_test', ROOT / 'BookMiner.py')
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


class PetaUnsolvedTests(unittest.TestCase):
    def test_duplicate_move_does_not_occupy_multiple_ranks(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            source = root / 'peta.db'
            source.write_text('#YANEURAOU-DB2016 1.00\n'
                              f'sfen {miner.SFEN_START} 1\n'
                              '7g7f none 100 0\n2g2f none 30 0\n7g7f none 20 3\n')
            (root / 'think_unsolved_sfens.txt').write_text('startpos\n')
            peta = miner.Book()
            miner.read_regular_book_fast(peta, str(source), 'test')
            with patch.object(miner, 'peta_book', peta):
                miner.peta_unsolved((1, 0), eval_limit=400)
            output = (root / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 2g2f', output)
            self.assertNotIn('startpos moves 7g7f', output)

    def test_unsorted_peta_file_uses_evaluation_order_for_depths(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            source = root / 'peta.db'
            source.write_text('#YANEURAOU-DB2016 1.00\n'
                              f'sfen {miner.SFEN_START} 1\n'
                              '7g7f none 10 0\n2g2f none 100 1\n')
            (root / 'think_unsolved_sfens.txt').write_text('startpos\n')
            peta = miner.Book()
            miner.read_regular_book_fast(peta, str(source), 'test')
            with patch.object(miner, 'peta_book', peta):
                miner.peta_unsolved((1, 0), eval_limit=400)
            self.assertEqual((root / 'think_sfens.txt').read_text(), '')

    def test_eval_limit_applies_only_to_depth_zero_best(self):
        book = miner.Book()
        board = miner.cshogi.Board()
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 501, 1), miner.MoveInfo('2g2f', 0, 0)], 1)
        board.push_usi('7g7f')
        book.body[miner.trim_sfen_ply(board.sfen())[0]] = miner.PositionInfo([
            miner.MoveInfo('3c3d', -401, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            (Path(directory) / 'think_unsolved_sfens.txt').write_text('startpos moves 7g7f 3c3d\n')
            miner.peta_unsolved((2, 1), eval_limit=400)
            output = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 2g2f', output)
            self.assertNotIn('startpos moves 7g7f', output)

    def test_rank_thresholds_and_eval_cutoff(self):
        book = miner.Book()
        root = miner.cshogi.Board()
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 100, 9), miner.MoveInfo('2g2f', 90, 8),
            miner.MoveInfo('6g6f', 80, 5), miner.MoveInfo('1g1f', 70, 4)], 1)
        root.push_usi('7g7f')
        child = miner.trim_sfen_ply(root.sfen())[0]
        book.body[child] = miner.PositionInfo([miner.MoveInfo('3c3d', 401, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            (Path(directory) / 'think_unsolved_sfens.txt').write_text('# 棋譜\nstartpos moves 7g7f 3c3d\n', encoding='utf-8-sig')
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400)
            text = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 6g6f', text)
            self.assertNotIn('startpos moves 7g7f', text)
            self.assertNotIn('startpos moves 2g2f', text)
            self.assertNotIn('startpos moves 1g1f', text)
            book.body[child].moveinfos[0].eval = 400
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400)
            text = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 7g7f', text)
            self.assertIn('startpos moves 7g7f 3c3d', text)
