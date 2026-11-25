import importlib.util
from contextlib import nullcontext
import random
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bookminer_fallback_test', ROOT / 'BookMiner.py')
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


class PetaNextFallbackTest(unittest.TestCase):
    def test_refutation_finds_flipped_normal_book_behind_empty_entry(self):
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        sfen = miner.trim_sfen(board.sfen())
        source = miner.Book()
        source.body[sfen] = miner.PositionInfo([], 2)
        source.body[miner.flipped_sfen(sfen)] = miner.PositionInfo([
            miner.MoveInfo(miner.flipped_move('8c8d'), 10, 1),
            miner.MoveInfo(miner.flipped_move('3c3d'), 0, 1)], 2)
        peta = miner.PositionInfo([miner.MoveInfo('3c3d', 100, 0), miner.MoveInfo('8c8d', 0, 1)], 2)
        self.assertTrue(miner.is_refuted_move_by_peta_impact(source, peta, False, sfen, '3c3d', 100, 10))
        peta_book = miner.Book()
        peta_book.body[sfen] = peta
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta_book), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'sfen {sfen} 2\n')
            miner.peta_next(0, 10, 200, str(roots), refutation_book=source, eval_refutation_margin=10)
            self.assertIn('moves 3c3d', (Path(directory) / 'think_sfens.txt').read_text())

    def test_opponent_pv_returning_to_game_history_is_rebased(self):
        own = miner.Book()
        board = miner.cshogi.Board()
        opponent_sfen = miner.trim_sfen(board.sfen())
        own.body[opponent_sfen] = miner.PositionInfo([miner.MoveInfo('7g7f', 0, 1)], 1)
        for move, reply in (('5i5h', '5a5b'), ('5a5b', '5h5i'), ('5h5i', '5b5a')):
            board.push_usi(move)
            own.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo(reply, 0, 1)], board.move_number)
        class Probe:
            def probe(self, key):
                if key == opponent_sfen:
                    return [miner.BookLib.BookMove('5i5h', 'none', 0, 1, 1)]
                return None
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', own), \
             patch.object(miner, 'BOOK_DIR', directory), \
             patch.object(miner, 'collect_opponent_book_paths', return_value=['opponent.ybb']), \
             patch.object(miner.BookLib, 'open_book_probe', return_value=nullcontext(Probe())):
            miner.peta_opponent(0, 200, 100, 1)
            output = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('sfen ', output)
            self.assertIn('moves 7g7f', output)

    def test_opponent_uses_flipped_moves_behind_empty_direct_entry(self):
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        sfen = miner.trim_sfen(board.sfen())
        flipped = miner.flipped_sfen(sfen)
        book = miner.Book()
        book.body[sfen] = miner.PositionInfo([], 2)
        book.body[flipped] = miner.PositionInfo([miner.MoveInfo(miner.flipped_move('3c3d'), 0, 1)], 2)
        self.assertEqual(miner.candidate_best_moves_for_sfen(book, sfen, 0), [('3c3d', 0)])
        move = miner.BookLib.BookMove(miner.flipped_move('3c3d'), 'none', 0, 1, 1)
        class Probe:
            def probe(self, key):
                return [] if key == sfen else [move] if key == flipped else None
        self.assertEqual(miner.candidate_best_moves_for_probe(Probe(), sfen, 0), [('3c3d', 0)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opponent.ybb'
            miner.BookLib.write_ybb_book({sfen + ' 2': [], flipped + ' 2': [move]}, path)
            with miner.BookLib.open_book_probe(path) as probe:
                self.assertEqual(miner.candidate_best_moves_for_probe(probe, sfen, 0), [('3c3d', 0)])

    def test_rebased_paths_keep_distinct_eval_widths(self):
        first = 'startpos moves 5i5h 5a5b 5h5i'
        second = 'startpos moves 5i4h 5a5b 4h5i'
        board = miner.cshogi.Board(miner.decode_position_string(first))
        book = miner.Book()
        book.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo('5b5a', 0, 1)], 4)
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 0, 1), miner.MoveInfo('2g2f', -50, 1)], 5)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(first + ',100\n' + second + ',0\n')
            miner.peta_next(0, 10, 200, str(roots))
            lines = (Path(directory) / 'think_sfens-white.txt').read_text()
            self.assertIn('moves 7g7f', lines)
            self.assertIn('moves 2g2f', lines)

    def test_peta_next_rebases_return_to_starting_path(self):
        root = 'startpos moves 5i5h 5a5b 5h5i'
        board = miner.cshogi.Board(miner.decode_position_string(root))
        book = miner.Book()
        book.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo('5b5a', 0, 0)], 4)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(root + '\n')
            miner.peta_next(0, 10, 200, str(roots))
            line = (Path(directory) / 'think_sfens.txt').read_text().split(',', 1)[0]
            self.assertTrue(line.startswith('sfen '))
            self.assertEqual(miner.decode_position_string(line), miner.decode_position_string(root + ' 5b5a'))
            with patch.object(miner, 'peta_leaf_rejoins_peta_book', return_value=True):
                miner.peta_rejoin(0, 10, 200, str(roots))
            line = (Path(directory) / 'think_sfens.txt').read_text().split(',', 1)[0]
            self.assertTrue(line.startswith('sfen '))
            self.assertEqual(miner.decode_position_string(line), miner.decode_position_string(root + ' 5b5a'))

    def test_repeated_starting_path_is_rebased_for_mining(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', miner.Book()), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos moves 5i5h 5a5b 5h5i 5b5a 7g7f,50\n')
            miner.peta_next(0, 10, 200, str(roots))
            line = (Path(directory) / 'think_sfens.txt').read_text().split(',', 1)[0]
            self.assertTrue(line.startswith('sfen '))
            self.assertTrue(line.endswith('moves 7g7f'))
            self.assertEqual(miner.decode_position_string(line),
                             miner.decode_position_string('startpos moves 5i5h 5a5b 5h5i 5b5a 7g7f'))

    def test_same_leaf_prefers_shorter_game_ply(self):
        base = miner.SFEN_START
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', miner.Book()), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'sfen {base} 199\nsfen {base} 5\n')
            miner.peta_next(0, 10, 200, str(roots))
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].split(',', 1)[0], f'sfen {base} 5')
            with patch.object(miner, 'peta_leaf_rejoins_peta_book', return_value=True):
                miner.peta_rejoin(0, 10, 200, str(roots))
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].split(',', 1)[0], f'sfen {base} 5')

    def test_empty_peta_child_is_a_leaf_without_fallback(self):
        peta = miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 0, 0)], 1)
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        peta.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([], 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos\n')
            miner.peta_next(0, 10, 100, str(roots))
            self.assertIn('startpos moves 7g7f', (Path(directory) / 'think_sfens.txt').read_text())

    def test_rejoin_ignores_empty_peta_child(self):
        peta = miner.Book()
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        peta.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([], 2)
        with patch.object(miner, 'peta_book', peta):
            self.assertFalse(miner.peta_leaf_rejoins_peta_book(miner.SFEN_START_PLY1))
            peta.body[miner.flipped_sfen(miner.trim_sfen(board.sfen()))] = miner.PositionInfo([
                miner.MoveInfo(miner.flipped_move('3c3d'), 0, 0)], 2)
            self.assertTrue(miner.peta_leaf_rejoins_peta_book(miner.SFEN_START_PLY1))

    def test_refutation_treats_empty_peta_child_as_leaf(self):
        peta, old = miner.Book(), miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 100, 0), miner.MoveInfo('2g2f', 0, 0)], 1)
        old.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('2g2f', 10, 1), miner.MoveInfo('7g7f', 0, 1)], 1)
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        peta.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([], 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos\n')
            miner.peta_next(0, 10, 100, str(roots), refutation_book=old, eval_refutation_margin=10)
            self.assertIn('startpos moves 7g7f', (Path(directory) / 'think_sfens.txt').read_text())

    def test_opponent_ybb_probe_uses_latest_duplicate_move_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opponent.ybb'
            move = miner.BookLib.BookMove
            miner.BookLib.write_ybb_book({miner.SFEN_START_PLY1: [
                move('7g7f', 'none', 100, 0, 1), move('2g2f', 'none', 30, 0, 1),
                move('7g7f', 'none', 20, 0, 1)]}, path)
            with miner.BookLib.open_book_probe(path) as probe:
                self.assertEqual(miner.candidate_best_moves_for_probe(probe, miner.SFEN_START, 0), [('2g2f', 30)])

    def test_rejoin_stops_at_unextended_high_eval(self):
        peta = miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 0)], 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory), \
             patch.object(miner, 'peta_leaf_rejoins_peta_book', return_value=True) as rejoins:
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos\n')
            miner.peta_rejoin(0, 10, 100, str(roots), eval_limit=400)
            rejoins.assert_not_called()
            peta.body[miner.SFEN_START].moveinfos[0].depth = 1
            miner.peta_rejoin(0, 10, 100, str(roots), eval_limit=400)
            self.assertGreater(rejoins.call_count, 0)

    def test_opponent_pv_honors_depth_zero_eval_limit(self):
        peta = miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', -500, 0)], 1)
        with patch.object(miner, 'peta_book', peta):
            args = ('startpos', miner.SFEN_START_PLY1, 100, 10, 0, random.Random(1))
            self.assertIsNone(miner.peta_pv_leaf_position_cmd_from_position_random(*args, 400))
            peta.body[miner.SFEN_START].moveinfos[0].depth = 1
            self.assertEqual(miner.peta_pv_leaf_position_cmd_from_position_random(*args, 400),
                             'startpos moves 7g7f')

    def test_eval_limit_checks_normal_best_child_even_with_peta_hit(self):
        peta, normal = miner.Book(), miner.Book()
        board = miner.cshogi.Board()
        root = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 0)], 1)
        peta.body[miner.SFEN_START] = root
        board.push_usi('7g7f')
        child = miner.trim_sfen_ply(board.sfen())[0]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', peta), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos\n')
            miner.peta_next(0, 10, 100, str(roots), eval_limit=400, fallback_book=normal)
            self.assertEqual((Path(directory) / 'think_sfens.txt').read_text(), '')
            normal.body[miner.flipped_sfen(child)] = miner.PositionInfo([
                miner.MoveInfo(miner.flipped_move('3c3d'), 0, 0)], 2)
            miner.peta_next(0, 10, 100, str(roots), eval_limit=400, fallback_book=normal)
            self.assertIn('startpos moves 7g7f 3c3d', (Path(directory) / 'think_sfens.txt').read_text())
            normal.body[miner.flipped_sfen(child)].moveinfos[0].eval = None
            miner.peta_next(0, 10, 100, str(roots), eval_limit=400, fallback_book=normal)
            self.assertEqual((Path(directory) / 'think_sfens.txt').read_text(), '')
            root.moveinfos[0].depth = 1
            miner.peta_next(0, 10, 100, str(roots), eval_limit=400, fallback_book=normal)
            self.assertIn('startpos moves 7g7f', (Path(directory) / 'think_sfens.txt').read_text())

    def test_normal_depth_uses_child_not_stored_depth(self):
        normal = miner.Book()
        position = miner.PositionInfo([miner.MoveInfo('7g7f', -500, 99)], 1)
        self.assertTrue(miner.peta_cut_by_eval_limit(position, False, miner.SFEN_START_PLY1, 400, normal, True))
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        normal.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo('3c3d', 0)], 2)
        self.assertFalse(miner.peta_cut_by_eval_limit(position, False, miner.SFEN_START_PLY1, 400, normal, True))

    def test_empty_direct_position_does_not_hide_considered_flipped_position(self):
        peta, normal = miner.Book(), miner.Book()
        position = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 0)], 1)
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        child = miner.trim_sfen(board.sfen())
        normal.body[child] = miner.PositionInfo([], 2)
        normal.body[miner.flipped_sfen(child)] = miner.PositionInfo([
            miner.MoveInfo(miner.flipped_move('3c3d'), 0)], 2)
        self.assertFalse(miner.peta_cut_by_eval_limit(position, False, miner.SFEN_START_PLY1, 400, normal, True))
        hit, flipped = miner.find_peta_next_position(peta, normal, child)
        self.assertTrue(flipped)
        self.assertEqual(hit.moveinfos[0].move, miner.flipped_move('3c3d'))
        peta.body[child] = miner.PositionInfo([], 2)
        peta.body[miner.flipped_sfen(child)] = normal.body[miner.flipped_sfen(child)]
        self.assertTrue(miner.find_peta_next_position(peta, None, child)[1])
        self.assertTrue(miner.find_peta_position_with_flip(peta, child)[1])
        normal.body[child] = miner.PositionInfo([miner.MoveInfo('3c3d', None)], 2)
        self.assertFalse(miner.peta_cut_by_eval_limit(position, False, miner.SFEN_START_PLY1, 400, normal, True))
        self.assertTrue(miner.find_peta_next_position(peta, normal, child)[1])
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        engine = unittest.mock.Mock()
        info, orientation, _, status, searched = manager.think_sfen_once(normal, engine, child, 2, 0, set())
        self.assertEqual(status, miner.TASK_RESULT_DONE)
        self.assertFalse(searched)
        self.assertEqual(orientation, miner.flipped_sfen(child))
        self.assertEqual(info.moveinfos[0].eval, 0)
        engine.go.assert_not_called()

    def test_opponent_probe_considers_later_duplicate_position_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opponent.db'
            sfen = miner.cshogi.Board().sfen().rsplit(' ', 1)[0]
            path.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {sfen} 1\n7g7f none 10 1 1\n'
                            f'sfen {sfen} 3\n2g2f none 20 1 1\n')
            with miner.BookLib.open_book_probe(path) as probe:
                candidates = miner.candidate_best_moves_for_probe(probe, sfen, 0)
            self.assertEqual(candidates, [('2g2f', 20)])

    def test_completion_is_announced_after_result_is_ready_and_busy_is_cleared(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos\n')
            active = Event()
            active.set()
            def before_done():
                self.assertTrue((Path(directory) / 'think_sfens.txt').is_file())
                active.clear()
            def observe(*args, **kwargs):
                if args and str(args[0]).startswith('[PetaNextDone]'):
                    self.assertFalse(active.is_set())
            with patch.object(miner, 'print', side_effect=observe):
                miner.peta_next(5, 10, 100, str(roots), before_done=before_done)
            self.assertFalse(active.is_set())

    def test_start_positions_support_slash_comments(self):
        moves = ('5i5h 5a5b 5h5i 5b5a 2g2f 8c8d 2f2e 8d8e 7g7f 4a3b 8h7g '
                 '3c3d 7i6h 2b7g+ 6h7g 3a2b 3i3h 2b3c 6i7h 7a6b 9g9f '
                 '9c9d 1g1f 1c1d 4g4f 6c6d 3g3f 6a5b')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'// 無効な局面,xyz\nstartpos moves {moves},100 // 電竜戦\n'
                             'startpos moves 7g7f // 共通値\n# 従来のコメント\n', encoding='utf-8-sig')
            loaded = miner.load_peta_root_positions(str(roots))
            self.assertEqual([d for _p, _s, d in loaded], [100, None])
            self.assertEqual(miner.decode_position_string(loaded[0][0]),
                             miner.decode_position_string(f'startpos moves {moves}'))
            self.assertEqual(loaded[1][0], 'startpos moves 7g7f')
            miner.peta_next(5, 10, 200, str(roots))
            output = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertNotIn('//', output)
            self.assertIn(f'{loaded[0][0]},', output)
            roots.write_text('startpos, invalid // コメント\n')
            with self.assertRaisesRegex(ValueError, 'roots.txt:1'):
                miner.load_peta_root_positions(str(roots))

    def test_root_zero_overrides_default_five_and_is_logged(self):
        book = miner.Book()
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 100, 0), miner.MoveInfo('2g2f', 97, 0)], 1)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', book), patch.object(miner, 'BOOK_DIR', directory), \
             patch.object(miner, 'print') as log:
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos,0\n')
            miner.peta_next(5, 10, 100, str(roots))
            lines = (Path(directory) / 'think_sfens-white.txt').read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(any('root_best = 100 , eval_diff = 0' in str(call) for call in log.call_args_list))
            roots.write_text('startpos\n')
            miner.peta_next(5, 10, 100, str(roots))
            self.assertEqual(len((Path(directory) / 'think_sfens-white.txt').read_text().splitlines()), 2)

    def test_cancellation_during_merge_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            black, white, output = root / 'black', root / 'white', root / 'output'
            black.write_text('startpos\n')
            white.write_text('startpos moves 7g7f\n')
            output.write_text('previous\n')
            cancel = Event()
            cancel.set()
            with self.assertRaises(miner.PetaNextCancelled):
                miner.merge_position_command_files(str(black), str(white), str(output), cancel)
            self.assertEqual(output.read_text(), 'previous\n')

    def test_fallback_does_not_hide_unscored_frontier(self):
        peta, normal = miner.Book(), miner.Book()
        board = miner.cshogi.Board()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 0, 0)], 1)
        board.push_usi('7g7f')
        child, _ = miner.trim_sfen_ply(board.sfen())
        normal.body[child] = miner.PositionInfo([miner.MoveInfo('3c3d', None, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', peta), patch.object(miner, 'BOOK_DIR', directory):
            miner.peta_next(10, 10, 100, str(Path(directory) / 'missing'), fallback_book=normal)
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith('startpos moves 7g7f,'), lines)

    def test_refutation_does_not_output_missing_start_position(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos, 50\n')
            miner.peta_next(10, 10, 100, str(roots), refutation_book=miner.Book(), eval_refutation_margin=1)
            self.assertEqual((Path(directory) / 'think_sfens.txt').read_text(), '')

    def test_start_positions_are_loaded_once_per_command(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos, 50\n')
            for command in (miner.peta_next, miner.peta_rejoin):
                with self.subTest(command=command.__name__), \
                     patch.object(miner, 'load_peta_root_positions', wraps=miner.load_peta_root_positions) as load:
                    command(10, 10, 100, str(roots))
                    load.assert_called_once()
                    self.assertEqual(load.call_args.args[0], str(roots))

    def test_start_position_eval_diff_overrides_default(self):
        book = miner.Book()
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 100, 0), miner.MoveInfo('2g2f', 60, 0)], 1)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', book), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            for root_line, expected in [('startpos', 1), ('startpos, 50', 2), ('startpos, 0', 1),
                                        ('startpos, 50\nstartpos, 0', 2),
                                        (f'startpos, 0\nsfen {miner.SFEN_START_PLY1}, 50', 2)]:
                with self.subTest(root_line=root_line):
                    roots.write_text(root_line + '\n')
                    miner.peta_next(10, 10, 100, str(roots))
                    lines = (Path(directory) / 'think_sfens-white.txt').read_text().splitlines()
                    self.assertEqual(len(lines), expected, lines)
                    self.assertTrue(all(', 50' not in line for line in lines))
                    with patch.object(miner, 'peta_leaf_rejoins_peta_book', return_value=True):
                        miner.peta_rejoin(10, 10, 100, str(roots))
                    lines = (Path(directory) / 'think_sfens-white.txt').read_text().splitlines()
                    self.assertEqual(len(lines), expected, lines)
            for invalid in ('-1', 'abc', '', '1,2', '2147483648'):
                roots.write_text('startpos, ' + invalid + '\n')
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'roots.txt:1'):
                    miner.load_peta_root_positions(str(roots))

    def test_merge_removes_duplicate_entries_preserving_order_and_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            black, white, output = root / 'black', root / 'white', root / 'output'
            black.write_text('startpos, eval_limit=400\nstartpos moves 7g7f\n')
            white.write_text('startpos, eval_limit=400\nstartpos, eval_limit=500\nstartpos moves 2g2f\n')
            self.assertEqual(miner.merge_position_command_files(str(black), str(white), str(output)), 4)
            self.assertEqual(output.read_text().splitlines(), [
                'startpos, eval_limit=400', 'startpos moves 7g7f',
                'startpos, eval_limit=500', 'startpos moves 2g2f'])

    def test_peta_next_deduplicates_same_leaf_from_different_paths(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', miner.Book()), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text('startpos moves 7g7f 3c3d 2g2f 8c8d\n'
                             'startpos moves 2g2f 8c8d 7g7f 3c3d\n')
            miner.peta_next(100, 10, 100, str(roots))
            black = (Path(directory) / 'think_sfens-black.txt').read_text().splitlines()
            white = (Path(directory) / 'think_sfens-white.txt').read_text().splitlines()
        self.assertEqual(len(black), 1, black)
        self.assertEqual(len(white), 1, white)

    def test_position_output_preserves_existing_file_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output, black, white = root / 'think_sfens.txt', root / 'black.txt', root / 'white.txt'
            output.write_text('existing\n')
            black.write_text('startpos\n')
            with self.assertRaises(FileNotFoundError):
                miner.merge_position_command_files(str(black), str(white), str(output))
            self.assertEqual(output.read_text(), 'existing\n')
            white.write_text('startpos moves 7g7f\n')
            entries = {'startpos': miner.PositionCommandEntry('startpos')}
            with patch.object(miner.os, 'replace', side_effect=OSError('failed')):
                with self.assertRaises(OSError):
                    miner.write_position_command_entries(str(output), entries)
                with self.assertRaises(OSError):
                    miner.merge_position_command_files(str(black), str(white), str(output))
            with patch.object(miner, 'format_position_command_entry', side_effect=OSError('write failed')):
                with self.assertRaises(OSError):
                    miner.write_position_command_entries(str(output), entries)
            self.assertEqual(output.read_text(), 'existing\n')
            self.assertEqual({p.name for p in root.iterdir()}, {'think_sfens.txt', 'black.txt', 'white.txt'})
            self.assertEqual(miner.merge_position_command_files(str(black), str(white), str(output)), 2)
            self.assertEqual(output.read_text(), 'startpos\nstartpos moves 7g7f\n')

    def test_peta_rejoin_distinguishes_incoming_leaf_evaluations(self):
        a, b = miner.cshogi.Board(), miner.cshogi.Board()
        for move in ('7g7f', '3c3d', '2g2f'):
            a.push_usi(move)
        for move in ('2g2f', '8c8d', '7g7f'):
            b.push_usi(move)
        book = miner.Book()
        sfen_a, _ = miner.trim_sfen_ply(a.sfen())
        sfen_b, _ = miner.trim_sfen_ply(b.sfen())
        book.body[sfen_a] = miner.PositionInfo([miner.MoveInfo('4c4d', 100), miner.MoveInfo('8c8d', -50)], 4)
        book.body[sfen_b] = miner.PositionInfo([miner.MoveInfo('4c4d', 100), miner.MoveInfo('3c3d', 20)], 4)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', book), patch.object(miner, 'BOOK_DIR', directory), \
             patch.object(miner, 'peta_leaf_rejoins_peta_book', side_effect=lambda _sfen, value, _drop: value == 20):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'sfen {a.sfen()}\nsfen {b.sfen()}\n')
            miner.peta_rejoin(200, 10, 100, str(roots), eval_drop=10)
            lines = (Path(directory) / 'think_sfens-black.txt').read_text().splitlines()
        self.assertTrue(any(line.startswith(f'sfen {b.sfen()} moves 3c3d,') for line in lines), lines)

    def test_peta_next_overlapping_roots_keep_distinct_evaluation_context(self):
        book = miner.Book()
        board = miner.cshogi.Board()
        book.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 100, 0)], 1)
        board.push_usi('7g7f')
        child, _ = miner.trim_sfen_ply(board.sfen())
        book.body[child] = miner.PositionInfo([miner.MoveInfo('3c3d', 0, 0), miner.MoveInfo('8c8d', -50, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', book), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'startpos\nsfen {board.sfen()}\n')
            miner.peta_next(10, 10, 100, str(roots))
            lines = (Path(directory) / 'think_sfens-black.txt').read_text().splitlines()
        self.assertTrue(any(line.startswith('startpos moves 7g7f 8c8d,') for line in lines), lines)

    def test_peta_next_and_rejoin_use_sfen_side_not_ply_parity(self):
        white_sfen = miner.SFEN_START.replace(' b ', ' w ')
        book = miner.Book()
        book.body[white_sfen] = miner.PositionInfo([miner.MoveInfo('3c3d', 100, 0), miner.MoveInfo('8c8d', 60, 0)], 1)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(miner, 'peta_book', book), patch.object(miner, 'BOOK_DIR', directory):
            roots = Path(directory) / 'roots.txt'
            roots.write_text(f'sfen {white_sfen} 1\n')
            miner.peta_next(50, 10, 100, str(roots))
            lines = (Path(directory) / 'think_sfens-black.txt').read_text().splitlines()
            self.assertEqual(len(lines), 2, lines)
            with patch.object(miner, 'peta_leaf_rejoins_peta_book', return_value=True):
                miner.peta_rejoin(50, 10, 100, str(roots))
            lines = (Path(directory) / 'think_sfens-black.txt').read_text().splitlines()
            self.assertEqual(len(lines), 2, lines)

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
            self.assertEqual(miner.load_peta_root_positions(str(roots)), [('startpos', miner.SFEN_START_PLY1, None)])
            miner.peta_next(100, 100, 200, str(roots))
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertTrue(lines)
            self.assertTrue(all(line.startswith('startpos,') for line in lines))
            roots.write_text('# コメント\nstartpos\n', encoding='cp932')
            self.assertEqual(miner.load_peta_root_positions(str(roots)), [('startpos', miner.SFEN_START_PLY1, None)])

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
