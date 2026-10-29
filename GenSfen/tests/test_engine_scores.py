import io
import os
import tempfile
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
sys.path.insert(0, str(ROOT / 'GenSfen'))
from YaneShogiLib import Engine, EngineConnectionError, SFEN_START_PLY1, usi_mate_to_yaneuraou_eval
from gensfen import ShogiMatch


class EngineScoreTest(unittest.TestCase):
    def engine_with_output(self, lines):
        engine = Engine.__new__(Engine)
        engine.receive_usi = Mock(side_effect=lines)
        engine.send_usi = Mock()
        engine.dump_engine_io_log = Mock(return_value='')
        return engine

    @unittest.skipIf(os.name == 'nt', 'POSIX mock engine script')
    def test_search_through_real_subprocess_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'mock-engine'
            executable.write_text('#!/usr/bin/env python3\nimport sys\n'
                                  'for line in sys.stdin:\n'
                                  ' line = line.strip()\n'
                                  ' if line == "isready": print("readyok", flush=True)\n'
                                  ' elif line.startswith("go "):\n'
                                  '  print("info string bestmove 2g2f score cp 999 pv 2g2f", flush=True)\n'
                                  '  print("info multipv 1 score cp 42 upperbound pv 7g7f", flush=True)\n'
                                  '  print("bestmove 7g7f", flush=True)\n'
                                  ' elif line == "quit": break\n')
            executable.chmod(0o755)
            engine = Engine(str(executable), 0)
            try:
                self.assertEqual(engine.go('startpos', 100), ('7g7f', 42))
            finally:
                engine.close()

    def test_multipv_uses_score_for_bestmove_not_last_first_rank(self):
        engine = self.engine_with_output([
            'info multipv 1 score cp 100 pv 7g7f',
            'info multipv 2 score cp 80 lowerbound pv 2g2f',
            'bestmove 2g2f',
        ])
        move, score, candidates = engine.go_multipv('startpos', 100, 32000)
        self.assertEqual((move, score), ('2g2f', 80))
        self.assertEqual(candidates, [('7g7f', 100), ('2g2f', 80)])

    def test_info_string_is_not_protocol_data(self):
        engine = self.engine_with_output([
            'info string bestmove 2g2f score cp 999 pv 2g2f',
            'info score cp 42 upperbound pv 7g7f', 'bestmove 7g7f'])
        self.assertEqual(engine.go('startpos', 100), ('7g7f', 42))

    def test_no_matching_score_rejects_game(self):
        engine = self.engine_with_output(['info score cp 42 pv 7g7f', 'bestmove 2g2f'])
        with self.assertRaisesRegex(Exception, 'no score for bestmove'):
            engine.go('startpos', 100)

    def test_score_without_pv_remains_supported(self):
        engine = self.engine_with_output(['info score cp 42', 'bestmove 7g7f'])
        self.assertEqual(engine.go('startpos', 100), ('7g7f', 42))

    def test_terminal_move_needs_no_score(self):
        for move in ('resign', 'win', 'none', '0000'):
            with self.subTest(move=move):
                engine = self.engine_with_output([f'bestmove {move}'])
                self.assertEqual(engine.go('startpos', 100), (move, 0))

    def test_negative_zero_mate(self):
        self.assertEqual(usi_mate_to_yaneuraou_eval('-0'), -32000)
        self.assertEqual(usi_mate_to_yaneuraou_eval('-0', 31000), -31000)

    def test_buffered_output_is_read_after_child_exit(self):
        engine = Engine.__new__(Engine)
        engine.engine = Mock(stdout=io.StringIO('bestmove 7g7f\n'), poll=Mock(return_value=0))
        engine.append_engine_io_log = Mock()
        engine.dump_engine_io_log = Mock()
        engine.search_sfen = 'startpos'
        self.assertEqual(engine.receive_usi(), 'bestmove 7g7f')
        with self.assertRaises(EngineConnectionError):
            engine.receive_usi()

    def test_game_records_the_selected_move_score(self):
        for format_name in ('pack', 'hcpe3'):
            with self.subTest(format=format_name):
                match = ShogiMatch.__new__(ShogiMatch)
                match.quit = False
                pause = threading.Event()
                pause.set()
                match.shared = SimpleNamespace(output_format=format_name, get_next_startpos_sfen=lambda: 'startpos',
                                               pause_event=pause, max_game_ply=200, nodes=100,
                                               hcpe3_policy_nodes=0, hcpe3_mate_score=32000, multipv=2,
                                               hcpe3_resign_eval=None, hcpe3_visits_sum=100,
                                               hcpe3_temperature=100, hcpe3_visit_smoothing=False)
                black, white = Mock(), Mock()
                black.go.return_value = ('7g7f', 42)
                black.go_multipv.return_value = ('7g7f', 42, [('7g7f', 42), ('2g2f', 20)])
                white.go.return_value = ('resign', 0)
                white.go_multipv.return_value = ('resign', 0, [])
                match.engines = [black, white]
                game = match.start_game()
                self.assertEqual(game.position_num, 1)
                if format_name == 'pack':
                    self.assertEqual(int.from_bytes(game.data[3:5], 'little', signed=True), 42)
                else:
                    self.assertEqual(game.records[0][1], 42)
                    self.assertEqual(len(game.records[0][2]), 2)
                    self.assertEqual(sum(visit for _, visit in game.records[0][2]), 100)

    def test_sfen_start_position_and_moves_reach_engine(self):
        for format_name in ('pack', 'hcpe3'):
            with self.subTest(format=format_name):
                match = ShogiMatch.__new__(ShogiMatch)
                pause = threading.Event()
                pause.set()
                start = f'sfen {SFEN_START_PLY1} moves 7g7f'
                match.shared = SimpleNamespace(
                    output_format=format_name, get_next_startpos_sfen=lambda: start + ', eval_limit=400',
                    pause_event=pause, max_game_ply=200, nodes=100,
                    hcpe3_policy_nodes=0, hcpe3_mate_score=32000, multipv=4)
                engine = Mock()
                engine.go.return_value = ('resign', 0)
                engine.go_multipv.return_value = ('resign', 0, [])
                match.engines = [engine, engine]
                match.start_game()
                if format_name == 'pack':
                    engine.go.assert_called_once_with(start, 100)
                else:
                    engine.go_multipv.assert_called_once_with(start, 100, 32000)

    def test_no_move_in_mated_position_is_a_loss(self):
        for format_name in ('pack', 'hcpe3'):
            with self.subTest(format=format_name):
                match = ShogiMatch.__new__(ShogiMatch)
                pause = threading.Event()
                pause.set()
                match.shared = SimpleNamespace(
                    output_format=format_name,
                    get_next_startpos_sfen=lambda: 'sfen 4k4/9/9/9/9/9/3rgr3/4r4/4K4 b - 1',
                    pause_event=pause, max_game_ply=200, nodes=100,
                    hcpe3_policy_nodes=0, hcpe3_mate_score=32000, multipv=4)
                engine = Mock()
                engine.go.return_value = ('none', 0)
                engine.go_multipv.return_value = ('none', 0, [])
                match.engines = [engine, engine]
                game = match.start_game()
                self.assertEqual(game.position_num, 0)
                if format_name == 'pack':
                    self.assertEqual(game.data[-3:], bytes([2, 1, 0]))
                else:
                    self.assertEqual(game.result, 2)

    def test_invalid_bestmove_does_not_become_a_game_result(self):
        for format_name in ('pack', 'hcpe3'):
            with self.subTest(format=format_name):
                match = ShogiMatch.__new__(ShogiMatch)
                pause = threading.Event()
                pause.set()
                match.shared = SimpleNamespace(output_format=format_name, get_next_startpos_sfen=lambda: 'startpos',
                                               pause_event=pause, max_game_ply=200, nodes=100,
                                               hcpe3_policy_nodes=0, hcpe3_mate_score=32000, multipv=4)
                engine = Mock()
                engine.go.return_value = ('none', 0)
                engine.go_multipv.return_value = ('none', 0, [])
                match.engines = [engine, engine]
                with self.assertRaisesRegex(ValueError, 'bestmove'):
                    match.start_game()

    def test_bad_start_position_fails_instead_of_writing_empty_game(self):
        for format_name in ('pack', 'hcpe3'):
            with self.subTest(format=format_name):
                match = ShogiMatch.__new__(ShogiMatch)
                match.shared = Mock(output_format=format_name, get_next_startpos_sfen=Mock(return_value='bad'))
                with self.assertRaisesRegex(ValueError, 'start position'):
                    match.start_game()


if __name__ == '__main__':
    unittest.main()
