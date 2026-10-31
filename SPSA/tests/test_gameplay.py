import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cshogi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import BloodgateSPSA as spsa
from ParamLib import Entry
class SpsaGameTests(unittest.TestCase):
    def test_close_node_multipliers_have_separate_result_buckets(self):
        manager = spsa.WinManager()
        manager.update(0, 'engine', 1.234567)
        manager.update(1, 'engine', 1.234568)
        self.assertEqual(manager.win_count_by_node_multiplier,
                         {'1.234567': [0], '1.234568': [1]})

    def test_duplicate_start_is_rejected(self):
        matcher = spsa.GameMatcher.__new__(spsa.GameMatcher)
        matcher.shogi_matches = [SimpleNamespace(thread=SimpleNamespace(is_alive=lambda: True))]
        with patch.object(spsa, 'ShogiMatch') as start, self.assertRaisesRegex(RuntimeError, 'already running'):
            matcher.start_games()
        start.assert_not_called()

    def test_start_positions_ignore_blank_lines_and_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'positions.txt'
            path.write_text('\ufeff# comment\n\nstartpos\n// comment\n', encoding='utf-8')
            shared = spsa.SharedState.__new__(spsa.SharedState)
            self.assertEqual(shared.read_start_sfens(str(path)), ['startpos'])
            path.write_text('# no positions\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'no start positions'):
                shared.read_start_sfens(str(path))

    def test_elo_score_includes_draws(self):
        summary = spsa.WinManager().build_summary([1] + [2] * 9)
        self.assertIn('0.550', summary)
        self.assertIn('R34.9', summary)
        self.assertIn('0.500, R', spsa.WinManager().build_summary([2] * 10))

    def match(self, moves0, moves1):
        match = spsa.ShogiMatch.__new__(spsa.ShogiMatch)
        engines = [Mock(), Mock()]
        for engine, moves in zip(engines, (moves0, moves1)):
            engine.go.side_effect = [(move, 0) for move in moves]
        match.engines = engines
        match.t = [SimpleNamespace(engine_nodes=100), SimpleNamespace(engine_nodes=100)]
        match.shared = SimpleNamespace(standard_board=True, kif_manager=Mock())
        return match

    def test_engine_receives_valid_position_and_move_history(self):
        match = self.match(['7g7f'], ['resign'])
        self.assertEqual(match.game_play(0, 'startpos'), 0)
        match.engines[0].go.assert_called_once_with('startpos moves', 100)
        match.engines[1].go.assert_called_once_with('startpos moves 7g7f', 100)

    def test_resignation_at_move_limit_is_not_draw(self):
        match = self.match(['resign'], [])
        root = 'sfen ' + cshogi.STARTING_SFEN.rsplit(' ', 1)[0] + ' 239'
        self.assertEqual(match.game_play(0, root), 1)

    def test_no_legal_move_is_a_loss(self):
        match = self.match(['none'], [])
        root = 'sfen 4k4/9/9/9/9/9/3rgr3/4r4/4K4 b - 1'
        self.assertEqual(match.game_play(0, root), 1)

    def test_no_move_in_nonterminal_position_is_an_error(self):
        match = self.match(['none'], [])
        with self.assertRaisesRegex(ValueError, 'non-terminal'):
            match.game_play(0, 'startpos')
        match.shared.kif_manager.write_kif.assert_not_called()

    def test_second_occurrence_ends_game_as_draw(self):
        match = self.match(['5i6i', '6i5i'], ['5a6a', '6a5a'])
        self.assertEqual(match.game_play(0, 'sfen 4k4/9/9/9/9/9/9/9/4K4 b - 1'), 2)
        self.assertEqual(sum(engine.go.call_count for engine in match.engines), 4)

    def test_continuous_checker_loses(self):
        match = self.match(['5a4a', '4a5a'], ['5c4c', '4c5c'])
        self.assertEqual(match.game_play(0, 'sfen 4k4/9/4R4/9/9/9/9/9/K8 w - 2'), 0)
        self.assertEqual(sum(engine.go.call_count for engine in match.engines), 4)

    def test_repetition_in_root_history_is_detected_before_search(self):
        match = self.match([], [])
        root = 'sfen 4k4/9/9/9/9/9/9/9/4K4 b - 1 moves 5i6i 5a6a 6i5i 6a5a'
        self.assertEqual(match.game_play(0, root), 2)
        self.assertEqual(sum(engine.go.call_count for engine in match.engines), 0)

    def test_parameter_pair_is_generated_under_lock(self):
        match = spsa.ShogiMatch.__new__(spsa.ShogiMatch)
        match.shared = SimpleNamespace(param_lock=threading.Lock())
        params = [Entry('p', 'int', 10, 0, 20, 1, 0.1, '', False)]
        original = match.add_params
        def checked(*args):
            self.assertTrue(match.shared.param_lock.locked())
            return original(*args)
        with patch.object(match, 'generate_shift_params', return_value=[1]), \
             patch.object(match, 'add_params', side_effect=checked):
            self.assertEqual(match.generate_parameter_pair(params), ([1], [11], [9]))


if __name__ == '__main__':
    unittest.main()
