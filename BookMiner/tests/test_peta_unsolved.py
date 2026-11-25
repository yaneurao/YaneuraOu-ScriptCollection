import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bookminer_unsolved_test', ROOT / 'BookMiner.py')
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


class PetaUnsolvedTests(unittest.TestCase):

    def test_enqueue_uses_normal_book_eval_even_when_peta_score_is_lower(self):
        peta, normal = miner.Book(), miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 0, 0)], 1)
        normal.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 20)], 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos\n')
            miner.peta_unsolved((10, 8, 6, 4), book_extend_ply=1, eval_limit=400, normal_book=normal)
            line = (root / 'think_sfens.txt').read_text().strip()
            self.assertEqual(line, '')
            task = miner.Task(miner.SFEN_START, 1, 400, 'startpos moves 7g7f', max_book_ply=200, book_extend_ply=1)
            manager = object.__new__(miner.EngineManager)
            manager.book_miner_settings = miner.BookMinerSettings()
            manager.global_settings = SimpleNamespace(from_gui=False)
            manager.put_branch_task = Mock()
            engine = Mock()
            engine.thread_settings = SimpleNamespace(thread_id=0)
            engine.last_go_searched_nodes = 100
            self.assertEqual(manager.start_thinking_position(normal, engine, task), miner.TASK_RESULT_DONE)
            engine.go.assert_not_called()

    def test_max_book_ply_does_not_parse_later_invalid_game_moves(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', miner.Book()), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos moves 7g7f 7g7f\n')
            miner.peta_unsolved((10, 8, 6, 4), max_book_ply=2, eval_limit=400)
            self.assertEqual((root / 'think_sfens.txt').read_text().split(',', 1)[0], 'startpos')


    def test_eval_cutoff_does_not_parse_later_invalid_game_moves(self):
        peta = miner.Book()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 0)], 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos moves 7g7f 7g7f\n')
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=peta)
            self.assertEqual((root / 'think_sfens.txt').read_text(), '')

    def test_game_move_below_rank_threshold_enqueues_missing_game_positions(self):
        book = miner.Book()
        moves = ['7g7f', '2g2f', '6g6f', '1g1f', '3g3f', '4g4f', '9g9f']
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo(move, 100 - index, 10 if index < 4 else 0)
            for index, move in enumerate(moves)], 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos moves 9g9f 9c9d\n')
            miner.peta_unsolved((10, 8, 6, 4), book_extend_ply=20, eval_limit=400)
            lines = (root / 'think_sfens.txt').read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith('startpos moves 9g9f,'))
            self.assertTrue(lines[1].startswith('startpos moves 9g9f 9c9d,'))
            self.assertTrue(all('book_extend_ply=20' in line for line in lines))

    def test_seventh_game_move_is_used_even_when_child_is_in_peta(self):
        book = miner.Book()
        normal = miner.Book()
        moves = ['7g7f', '2g2f', '6g6f', '1g1f', '3g3f', '4g4f', '9g9f']
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo(move, 100 - index, 10 if index < 4 else 0)
            for index, move in enumerate(moves)], 1)
        board = miner.cshogi.Board()
        board.push_usi('9g9f')
        book.body[miner.trim_sfen_ply(board.sfen())[0]] = miner.PositionInfo([miner.MoveInfo('9c9d', 30, 0)], 2)
        normal.body[miner.trim_sfen_ply(board.sfen())[0]] = miner.PositionInfo([miner.MoveInfo('9c9d', 30, 0)], 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', book), \
             patch.object(miner, 'BOOK_DIR', directory):
            (Path(directory) / 'think_unsolved_sfens.txt').write_text('startpos moves 9g9f\n')
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=normal)
            lines = (Path(directory) / 'think_sfens.txt').read_text().splitlines()
            self.assertTrue(any(line.startswith('startpos moves 9g9f 9c9d,') for line in lines), lines)
            normal.body[miner.trim_sfen_ply(board.sfen())[0]].moveinfos[0].eval = 401
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=normal)
            self.assertEqual((Path(directory) / 'think_sfens.txt').read_text(), '')
            normal.body[miner.trim_sfen_ply(board.sfen())[0]].moveinfos[0].eval = 400
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=normal)
            self.assertIn('startpos moves 9g9f 9c9d,', (Path(directory) / 'think_sfens.txt').read_text())

    def test_shallow_best_pv_enqueues_first_unsearched_position(self):
        peta = miner.Book()
        normal = miner.Book()
        board = miner.cshogi.Board()
        pv_moves = ['7g7f', '3c3d', '2g2f', '8c8d']
        for index, move in enumerate(pv_moves):
            sfen, ply = miner.trim_sfen_ply(board.sfen())
            peta.body[sfen] = miner.PositionInfo([miner.MoveInfo(move, 20, max(0, 2 - index))], ply)
            normal.body[sfen] = miner.PositionInfo([miner.MoveInfo(move, 20, 20)], ply)
            board.push_usi(move)
        target_sfen = miner.trim_sfen(board.sfen())
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos\n')
            miner.peta_unsolved((10, 8, 6, 4), book_extend_ply=1, eval_limit=400)
            line = (root / 'think_sfens.txt').read_text().strip()
            self.assertTrue(line.startswith('startpos moves 7g7f 3c3d 2g2f 8c8d,'), line)
            manager = object.__new__(miner.EngineManager)
            manager.book_miner_settings = miner.BookMinerSettings()
            manager.global_settings = SimpleNamespace(from_gui=False)
            manager.put_branch_task = Mock()
            engine = Mock()
            engine.thread_settings = SimpleNamespace(thread_id=0)
            engine.last_go_searched_nodes = 100
            legal = miner.cshogi.move_to_usi(next(iter(board.legal_moves)))
            engine.go.return_value = [miner.MoveInfo(legal, 0)]
            task = miner.Task(miner.SFEN_START, 1, 400, line.split(',', 1)[0],
                              max_book_ply=200, book_extend_ply=1)
            self.assertEqual(manager.start_thinking_position(normal, engine, task), miner.TASK_RESULT_DONE)
            self.assertEqual(engine.go.call_count, 1)
            self.assertIn(target_sfen, normal.body)

    def test_depth_zero_pv_continues_through_flipped_positions_and_respects_limits(self):
        book = miner.Book()
        normal = miner.Book()
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        child = miner.trim_sfen(board.sfen())
        book.body[miner.flipped_sfen(child)] = miner.PositionInfo([
            miner.MoveInfo(miner.flipped_move('3c3d'), 400, 0)], 2)
        normal.body[child] = miner.PositionInfo([miner.MoveInfo('3c3d', 400, 0)], 2)
        with patch.object(miner, 'peta_book', book):
            args = ('startpos', miner.SFEN_START_PLY1, '7g7f', 200)
            self.assertEqual(miner.peta_pv_leaf_position_cmd(*args, 2, 400, normal),
                             'startpos moves 7g7f 3c3d')
            self.assertIsNone(miner.peta_pv_leaf_position_cmd(*args, 1, 400, normal))
            normal.body[child].moveinfos[0].eval = 401
            self.assertIsNone(miner.peta_pv_leaf_position_cmd(*args, 2, 400, normal))

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
            miner.peta_unsolved((2, 1), eval_limit=400, normal_book=book)
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
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=book)
            text = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 6g6f', text)
            self.assertNotIn('startpos moves 7g7f', text)
            self.assertNotIn('startpos moves 2g2f', text)
            self.assertNotIn('startpos moves 1g1f', text)
            book.body[child].moveinfos[0].eval = 400
            miner.peta_unsolved((10, 8, 6, 4), eval_limit=400, normal_book=book)
            text = (Path(directory) / 'think_sfens.txt').read_text()
            self.assertIn('startpos moves 7g7f', text)
            self.assertIn('startpos moves 7g7f 3c3d', text)

    def test_normal_book_never_extends_peta_pv(self):
        peta, normal = miner.Book(), miner.Book()
        board = miner.cshogi.Board()
        peta.body[miner.SFEN_START] = miner.PositionInfo([miner.MoveInfo('7g7f', 500, 0)], 1)
        for move in ('7g7f', '3c3d', '2g2f'):
            sfen, ply = miner.trim_sfen_ply(board.sfen())
            normal.body[sfen] = miner.PositionInfo([miner.MoveInfo(move, 0, 99)], ply)
            board.push_usi(move)
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text('startpos\n')
            miner.peta_unsolved((1,), eval_limit=400, normal_book=normal)
            self.assertTrue((root / 'think_sfens.txt').read_text().startswith('startpos moves 7g7f,'))
            normal.body[miner.SFEN_START].moveinfos[0].eval = 401
            miner.peta_unsolved((1,), eval_limit=400, normal_book=normal)
            self.assertEqual((root / 'think_sfens.txt').read_text(), '')

    def test_resume_precheck_ignores_search_beyond_previous_repetition(self):
        manager = object.__new__(miner.EngineManager)
        book = miner.Book()
        board = miner.cshogi.Board()
        root_sfen = miner.trim_sfen(board.sfen())
        board.push_usi('5i5h')
        command = f'sfen {board.sfen()} moves 5a5b 5h5i 5b5a 2g2f'
        after = miner.cshogi.Board()
        after.push_usi('2g2f')
        book.searching_sfens.add(miner.trim_sfen(after.sfen()))
        self.assertTrue(manager.position_command_hits_searching_sfen(book, command, 200))
        self.assertFalse(manager.position_command_hits_searching_sfen(book, command, 200, {root_sfen}))

    def test_unsolved_candidates_remain_reachable_after_game_repetition(self):
        peta = miner.Book()
        with tempfile.TemporaryDirectory() as directory, patch.object(miner, 'peta_book', peta), \
             patch.object(miner, 'BOOK_DIR', directory):
            root = Path(directory)
            (root / 'think_unsolved_sfens.txt').write_text(
                'startpos moves 5i5h 5a5b 5h5i 5b5a 2g2f\n')
            miner.peta_unsolved((10,), book_extend_ply=1, eval_limit=400, normal_book=miner.Book())
            outputs = (root / 'think_sfens.txt').read_text().splitlines()
            target = miner.cshogi.Board()
            target.push_usi('2g2f')
            target_sfen = miner.trim_sfen(target.sfen())
            matching = [miner.parse_position_command_entry(line).position_cmd for line in outputs
                        if miner.trim_sfen(miner.decode_position_string(line.split(',', 1)[0])) == target_sfen]
            self.assertTrue(matching, outputs)
            for command in matching:
                seen = set()
                for _, sfen in miner.iter_position_prefixes(command):
                    key = miner.trim_sfen(sfen)
                    self.assertNotIn(key, seen, command)
                    seen.add(key)
            normal = miner.Book()
            manager = object.__new__(miner.EngineManager)
            manager.book_miner_settings = miner.BookMinerSettings()
            manager.global_settings = SimpleNamespace(from_gui=False)
            manager.put_branch_task = Mock()
            engine = Mock()
            engine.thread_settings = SimpleNamespace(thread_id=0)
            engine.last_go_searched_nodes = 100
            def search(sfen, ratio, ply):
                search_board = miner.cshogi.Board(sfen + ' ' + str(ply))
                return [miner.MoveInfo(miner.cshogi.move_to_usi(next(iter(search_board.legal_moves))), 0)]
            engine.go.side_effect = search
            task = miner.Task(miner.SFEN_START, 1, 400, matching[0], max_book_ply=200, book_extend_ply=1)
            self.assertEqual(manager.start_thinking_position(normal, engine, task), miner.TASK_RESULT_DONE)
            self.assertIn(target_sfen, normal.body)

    def test_extension_stops_when_it_revisits_game_path(self):
        normal = miner.Book()
        board = miner.cshogi.Board()
        for move, best in (('5i5h', '2g2f'), ('5a5b', '5a5b'), ('5h5i', '5h5i')):
            key, ply = miner.trim_sfen_ply(board.sfen())
            normal.body[key] = miner.PositionInfo([miner.MoveInfo(best, 0, 0)], ply)
            board.push_usi(move)
        key, ply = miner.trim_sfen_ply(board.sfen())
        normal.body[miner.flipped_sfen(key)] = miner.PositionInfo(
            [miner.MoveInfo(miner.flipped_move('5b5a'), 0, 0)], ply)
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        manager.put_branch_task = Mock()
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        engine.go.return_value = [miner.MoveInfo('3c3d', 0)]
        task = miner.Task(miner.SFEN_START, 1, 400, 'startpos moves 5i5h 5a5b 5h5i',
                          max_book_ply=200, book_extend_ply=3)
        original_think = manager.think_sfen_once
        def deferred_once(book, active_engine, current_sfen, ply, last_ply, visited, max_ply):
            manager.think_sfen_once = original_think
            return None, current_sfen, last_ply, miner.TASK_RESULT_DEFERRED, False
        manager.think_sfen_once = deferred_once
        self.assertEqual(manager.start_thinking_position(normal, engine, task), miner.TASK_RESULT_DEFERRED)
        self.assertIsNotNone(task.resume)
        self.assertIn(miner.SFEN_START, task.resume.path_visited)
        self.assertEqual(manager.start_thinking_position(normal, engine, task), miner.TASK_RESULT_DONE)
        engine.go.assert_not_called()

    def test_extension_does_not_treat_flipped_position_as_repetition(self):
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        original = miner.trim_sfen(board.sfen())
        flipped = miner.flipped_sfen(original)
        self.assertNotEqual(original, flipped)
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        manager.put_branch_task = Mock()
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        flipped_board = miner.cshogi.Board(flipped + ' 2')
        engine.go.return_value = [miner.MoveInfo(miner.cshogi.move_to_usi(next(iter(flipped_board.legal_moves))), 0)]
        task = miner.Task(flipped, 2, 400, max_book_ply=200, book_extend_ply=1)
        task.resume = miner.TaskResume(True, f'sfen {flipped} 2', 1, 0, miner.PLY_MIN, 0, set(), {original})
        self.assertEqual(manager.start_thinking(miner.Book(), engine, task), miner.TASK_RESULT_DONE)
        engine.go.assert_called_once()
