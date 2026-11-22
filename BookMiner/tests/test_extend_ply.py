import importlib.util
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_extend_test', MODULE_PATH)
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


def make_line(length=25):
    board = miner.cshogi.Board()
    rng = random.Random(28)
    seen = {miner.trim_sfen(board.sfen()), miner.flipped_sfen(miner.trim_sfen(board.sfen()))}
    positions, moves = [], []
    for _ in range(length):
        positions.append(board.sfen())
        legal = list(board.legal_moves)
        rng.shuffle(legal)
        for move in legal:
            usi = miner.cshogi.move_to_usi(move)
            board.push(move)
            key = miner.trim_sfen(board.sfen())
            if key not in seen:
                seen.update((key, miner.flipped_sfen(key)))
                moves.append(usi)
                break
            board.pop()
        else:
            raise AssertionError('could not construct a nonrepeating test line')
    return positions, moves


def make_branch_lines():
    board = miner.cshogi.Board()
    root_key = miner.trim_sfen(board.sfen())
    first_moves = list(board.legal_moves)[:2]
    seen = {root_key, miner.flipped_sfen(root_key)}
    lines = {}
    for branch_index, first in enumerate(first_moves):
        branch = miner.cshogi.Board()
        branch.push(first)
        rng = random.Random(100 + branch_index)
        for _ in range(20):
            key = miner.trim_sfen(branch.sfen())
            assert key not in seen, 'branch position repeated'
            seen.update((key, miner.flipped_sfen(key)))
            candidates = list(branch.legal_moves)
            rng.shuffle(candidates)
            for candidate in candidates:
                branch.push(candidate)
                next_key = miner.trim_sfen(branch.sfen())
                if next_key not in seen:
                    lines[key] = miner.cshogi.move_to_usi(candidate)
                    break
                branch.pop()
            else:
                raise AssertionError('could not construct branch fixture')
    return root_key, first_moves, lines


class ExtendPlyTests(unittest.TestCase):
    def test_each_of_two_branch_leaves_gets_twenty_plies(self):
        root_key, first_moves, lines = make_branch_lines()
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        pending = []
        manager.put_branch_task = lambda _parent, task: pending.append(task)
        book = miner.Book()
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        def go(sfen, _ratio, _ply):
            if sfen == root_key:
                return [miner.MoveInfo(miner.cshogi.move_to_usi(first_moves[0]), 0),
                        miner.MoveInfo(miner.cshogi.move_to_usi(first_moves[1]), -1)]
            return [miner.MoveInfo(lines[sfen], 0)]
        engine.go.side_effect = go
        task = miner.Task(root_key, 1, 400, max_book_ply=200,
                          book_extend_ply=20, branch_width=2, branch_depth=1)
        self.assertEqual(manager.start_thinking(book, engine, task), miner.TASK_RESULT_DONE)
        self.assertEqual(len(pending), 1)
        self.assertEqual(manager.start_thinking(book, engine, pending.pop()), miner.TASK_RESULT_DONE)
        self.assertEqual(engine.go.call_count, 41)
        self.assertEqual(len(book.body), 41)

    def test_branch_then_extends_twenty_plies_even_through_existing_positions(self):
        positions, moves = make_line(30)
        for existing in (0, 7):
            with self.subTest(existing=existing):
                manager = object.__new__(miner.EngineManager)
                manager.book_miner_settings = miner.BookMinerSettings()
                manager.global_settings = SimpleNamespace(from_gui=False)
                manager.put_branch_task = Mock()
                book = miner.Book()
                for i in range(existing):
                    book.body[miner.trim_sfen(positions[i])] = miner.PositionInfo([miner.MoveInfo(moves[i], 0)], i + 1)
                engine = Mock()
                engine.thread_settings = SimpleNamespace(thread_id=0)
                engine.last_go_searched_nodes = 100
                engine.go.side_effect = lambda _sfen, _ratio, ply: [miner.MoveInfo(moves[ply - 1], 0)]
                task = miner.Task(miner.SFEN_START, 1, 400, max_book_ply=200,
                                  book_extend_ply=20, branch_width=1, branch_depth=3)
                self.assertEqual(manager.start_thinking(book, engine, task), miner.TASK_RESULT_DONE)
                self.assertEqual(engine.go.call_count, 23 - existing)
                self.assertEqual(len(book.body), 23)
                manager.put_branch_task.assert_not_called()

    def test_deferred_branch_extension_resumes_remaining_plies(self):
        positions, moves = make_line(30)
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        manager.put_branch_task = Mock()
        book = miner.Book()
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        engine.go.side_effect = lambda _sfen, _ratio, ply: [miner.MoveInfo(moves[ply - 1], 0)]
        blocked = miner.trim_sfen(positions[5])
        book.searching_sfens.add(blocked)
        task = miner.Task(miner.SFEN_START, 1, 400, max_book_ply=200,
                          book_extend_ply=20, branch_width=1, branch_depth=3)
        self.assertEqual(manager.start_thinking(book, engine, task), miner.TASK_RESULT_DEFERRED)
        self.assertTrue(task.resume.branch_extension)
        self.assertEqual(task.resume.rest_ply, 18)
        book.searching_sfens.clear()
        self.assertEqual(manager.start_thinking(book, engine, task), miner.TASK_RESULT_DONE)
        self.assertEqual(engine.go.call_count, 23)
        self.assertEqual(len(book.body), 23)

    def test_disconnected_engine_resumes_remaining_branch_extension(self):
        _positions, moves = make_line(30)
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        manager.put_branch_task = Mock()
        book = miner.Book()
        first = Mock()
        first.thread_settings = SimpleNamespace(thread_id=0)
        first.last_go_searched_nodes = 100
        first.connection_broken = False
        def go(_sfen, _ratio, ply):
            if ply == 5:
                first.connection_broken = True
                raise RuntimeError('engine disconnected')
            return [miner.MoveInfo(moves[ply - 1], 0)]
        first.go.side_effect = go
        task = miner.Task(miner.SFEN_START, 1, 400, max_book_ply=200,
                          book_extend_ply=20, branch_width=1, branch_depth=3)
        with self.assertRaisesRegex(RuntimeError, 'engine disconnected'):
            manager.start_thinking(book, first, task)
        self.assertTrue(task.resume.branch_extension)
        self.assertEqual(task.resume.rest_ply, 19)
        second = Mock()
        second.thread_settings = SimpleNamespace(thread_id=1)
        second.last_go_searched_nodes = 100
        second.go.side_effect = lambda _sfen, _ratio, ply: [miner.MoveInfo(moves[ply - 1], 0)]
        self.assertEqual(manager.start_thinking(book, second, task), miner.TASK_RESULT_DONE)
        self.assertEqual(second.go.call_count, 19)
        self.assertEqual(len(book.body), 23)

    def test_twenty_plies_count_existing_positions_and_stop_at_limits(self):
        positions, moves = make_line()
        for existing, max_ply, high_eval_ply, expected_go in ((0, 200, None, 20),
                                                               (7, 200, None, 13),
                                                               (0, 7, None, 6),
                                                               (0, 200, 5, 5)):
            with self.subTest(existing=existing, max_ply=max_ply, high_eval_ply=high_eval_ply):
                manager = object.__new__(miner.EngineManager)
                manager.book_miner_settings = miner.BookMinerSettings()
                manager.global_settings = SimpleNamespace(from_gui=False)
                book = miner.Book()
                for i in range(existing):
                    book.body[miner.trim_sfen(positions[i])] = miner.PositionInfo([miner.MoveInfo(moves[i], 0)], i + 1)
                engine = Mock()
                engine.thread_settings = SimpleNamespace(thread_id=0)
                engine.last_go_searched_nodes = 100
                engine.go.side_effect = lambda _sfen, _ratio, ply: [
                    miner.MoveInfo(moves[ply - 1], 500 if ply == high_eval_ply else 0)]
                task = miner.Task(miner.SFEN_START, 1, 400, max_book_ply=max_ply, book_extend_ply=20)
                self.assertEqual(manager.start_thinking(book, engine, task), miner.TASK_RESULT_DONE)
                self.assertEqual(engine.go.call_count, expected_go)
                self.assertEqual(len(book.body), expected_go + existing)
