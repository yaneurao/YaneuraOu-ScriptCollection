import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_engine_test', MODULE_PATH)
miner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = miner
spec.loader.exec_module(miner)


class EngineProtocolTests(unittest.TestCase):
    def test_existing_position_records_shorter_ply_even_when_visited(self):
        for visited in (set(), {miner.SFEN_START}):
            with self.subTest(visited=bool(visited)):
                manager = object.__new__(miner.EngineManager)
                manager.book_miner_settings = miner.BookMinerSettings()
                book = miner.Book()
                position = miner.PositionInfo([miner.MoveInfo('7g7f', 10)], 20)
                book.body[miner.SFEN_START] = position
                engine = Mock()
                manager.think_sfen_once(book, engine, miner.SFEN_START, 5, 0, visited)
                self.assertEqual(position.ply, 5)
                engine.go.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'fixture uses Unix executable scripts')
    def test_two_engines_resume_branch_after_one_disconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            dead, healthy = root / 'dead-engine', root / 'healthy-engine'
            source = ('#!/usr/bin/env python3\nimport sys,time\n'
                      'count=0\nmove="7g7f"\n'
                      'for line in sys.stdin:\n'
                      ' line=line.strip()\n'
                      ' if line == "isready": print("readyok", flush=True)\n'
                      ' elif line.startswith("position "):\n'
                      '  parts=line.split(); ply=int(parts[-1]); side=parts[-3]\n'
                      '  move="8c8d" if side=="w" else ("7g7f" if ply==3 else "2g2f")\n'
                      ' elif line.startswith("go "):\n'
                      '  count+=1\n'
                      '  if CRASH and count>=2: sys.exit(1)\n'
                      '  if not CRASH: time.sleep(0.05)\n'
                      '  print("info multipv 1 score cp 42 pv "+move, flush=True)\n'
                      '  print("info multipv 2 score cp 20 pv "+move, flush=True)\n'
                      '  print("bestmove "+move, flush=True)\n'
                      ' elif line == "quit": break\n')
            for path, value in ((dead, 'True'), (healthy, 'False')):
                path.write_text(source.replace('CRASH', value))
                path.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(dead), 'multi': 1, 'nodes': 100, 'multipv': 2, 'multipv_delta': 0},
                {'path': str(healthy), 'multi': 1, 'nodes': 100, 'multipv': 2, 'multipv_delta': 0},
            ]))
            positions = []
            for move in ('1g1f', '3g3f', '4g4f', '5g5f', '6g6f', '8g8f', '9g9f'):
                board = miner.cshogi.Board()
                board.push_usi(move)
                board.push_usi('3c3d')
                positions.append('sfen ' + board.sfen())
            (root / 'book/think_sfens.txt').write_text('\n'.join(positions) + '\n')
            process = subprocess.Popen([sys.executable, '-u', str(MODULE_PATH), '--from_gui'], cwd=root,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            try:
                process.stdin.write('sd 0 1 200 1 1000\neb 1 3\n')
                process.stdin.flush()
                captured = []
                deadline = time.monotonic() + 25
                requested_backup = False
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    captured.append(line)
                    if '[TaskQueueDone]' in line and not requested_backup:
                        process.stdin.write('w\n')
                        process.stdin.flush()
                        requested_backup = True
                    if '[ManualBackupDone]' in line:
                        break
                output = ''.join(captured)
                self.assertIn('[EngineAlive] 1/2', output)
                self.assertIn('[TaskQueueDone]', output)
                self.assertIn('[ManualBackupDone]', output)
                backup = list((root / 'book/backup').glob('book_miner-*.db'))
                self.assertEqual(len(backup), 1)
                self.assertEqual(sum(line.startswith('sfen ') for line in backup[0].read_text().splitlines()), 28)
            finally:
                if process.poll() is None:
                    process.stdin.write('!\n')
                    process.stdin.flush()
                    process.wait(timeout=10)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_deferred_task_is_not_discarded_after_many_retries(self):
        manager = object.__new__(miner.EngineManager)
        manager.global_settings = SimpleNamespace(quit=False)
        manager.task_progress_lock = threading.Lock()
        manager.task_progress_jobs = {}
        manager.task_queue = miner.JobRoundRobinQueue()
        manager.report_task_queue_progress = Mock()
        task = miner.Task(miner.SFEN_START, 1, 100, defer_count=1000)
        with patch.object(miner.time, 'sleep'):
            manager.defer_task(task)
        self.assertEqual(task.defer_count, 1001)
        self.assertIs(manager.task_queue.get(), task)
        manager.report_task_queue_progress.assert_not_called()

    def test_enqueue_rejects_when_no_engine_is_available(self):
        manager = Mock()
        manager.engine_alive_count.return_value = 0
        with patch.object(miner, 'print') as output:
            miner.put_position_commands(miner.Book(), 'unused.txt', manager, 100, 200, 1)
        self.assertTrue(any('no search engines are available' in str(call) for call in output.call_args_list))
        manager.start_task_queue_progress.assert_not_called()

    def test_worker_requeues_task_when_engine_connection_fails(self):
        manager = object.__new__(miner.EngineManager)
        manager.task_queue = miner.JobRoundRobinQueue()
        manager.global_settings = SimpleNamespace(quit=False)
        task = miner.Task(miner.SFEN_START, 1, 100)
        manager.task_queue.put(task)
        manager.start_thinking = Mock(side_effect=RuntimeError('engine failed'))
        manager.report_task_queue_progress = Mock()
        manager.report_engine_alive = Mock()
        engine = Mock()
        engine.connection_broken = True
        engine.thread_settings = SimpleNamespace(alive=True)
        manager.worker(miner.Book(), engine)
        self.assertEqual(manager.task_queue.qsize(), 1)
        self.assertIs(manager.task_queue.get(), task)
        manager.report_task_queue_progress.assert_not_called()
        self.assertFalse(engine.thread_settings.alive)

    def test_branch_resume_after_engine_fails_midway(self):
        class FakeEngine:
            def __init__(self, engine_id, fail_at=None):
                self.thread_settings = SimpleNamespace(thread_id=engine_id)
                self.connection_broken = False
                self.last_go_searched_nodes = 100
                self.calls = 0
                self.fail_at = fail_at
            def send_newgame(self):
                pass
            def is_process_alive(self):
                return not self.connection_broken
            def go(self, _sfen, _ratio, ply):
                self.calls += 1
                if self.calls == self.fail_at:
                    self.connection_broken = True
                    raise RuntimeError('engine disconnected')
                return [miner.MoveInfo({1:'7g7f', 2:'3c3d', 3:'2g2f', 4:'8c8d'}[ply], 0)]

        for position_cmd in (None, 'startpos moves 7g7f', 'startpos moves 7g7f 3c3d 2g2f'):
            with self.subTest(position_cmd=position_cmd):
                manager = object.__new__(miner.EngineManager)
                manager.book_miner_settings = miner.BookMinerSettings()
                manager.global_settings = SimpleNamespace(from_gui=False)
                book = miner.Book()
                task = miner.Task(miner.SFEN_START, 1, 100, position_cmd=position_cmd,
                                  book_extend_ply=3, branch_width=1, branch_depth=3)
                run = manager.start_thinking if position_cmd is None else manager.start_thinking_position
                with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                    run(book, FakeEngine(0, fail_at=2), task)
                self.assertIsNotNone(task.resume)
                self.assertEqual(run(book, FakeEngine(1), task), miner.TASK_RESULT_DONE)
                self.assertIsNone(task.resume)
                self.assertEqual(len(book.body), 4)

    def test_engine_specific_multipv_settings_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'engine_settings.json5'
            settings.write_text(json.dumps([{'path': 'dummy', 'multi': 1, 'nodes': 100,
                                             'multipv': 2, 'multipv_delta': 7}]))
            class FakeEngine:
                def __init__(self, global_settings, thread_settings):
                    self.global_settings, self.thread_settings = global_settings, thread_settings
                def isready(self):
                    self.thread_settings.readyok = True
                def is_process_alive(self):
                    return True
            with patch.object(miner, 'ENGINE_SETTINGS_JSON_PATH', str(settings)), \
                 patch.object(miner, 'Engine', FakeEngine):
                manager = miner.EngineManager(miner.BookMinerSettings())
            try:
                thread = manager.engines[0].thread_settings
                self.assertEqual((thread.engine_multipv, thread.engine_multipv_delta), (2, 7))
            finally:
                manager.global_settings.quit = True
        engine = self.engine(['bestmove none'], multipv=4)
        engine.thread_settings.engine_multipv = 2
        engine.go(miner.SFEN_START, 1.0)
        self.assertEqual(engine.send_usi.call_args_list[0].args[0], 'setoption name MultiPV value 2')

    @unittest.skipIf(os.name == 'nt', 'fixture uses a Unix executable script')
    def test_initialization_failure_terminates_previously_started_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / 'ready-engine'
            engine.write_text('#!/usr/bin/env python3\nimport os,pathlib,sys\n'
                              'pathlib.Path("pid.txt").write_text(str(os.getpid()))\n'
                              'for line in sys.stdin:\n'
                              ' if line.strip() == "isready": print("readyok", flush=True)\n')
            engine.chmod(0o755)
            settings = root / 'engine_settings.json5'
            settings.write_text(json.dumps([{'path': str(engine), 'multi': 1, 'nodes': 100},
                                            {'path': str(root / 'missing'), 'multi': 1, 'nodes': 100}]))
            with patch.object(miner, 'ENGINE_SETTINGS_JSON_PATH', str(settings)):
                with self.assertRaisesRegex(Exception, 'Engine not Found'):
                    miner.EngineManager(miner.BookMinerSettings())
            pid = int((root / 'pid.txt').read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    @unittest.skipIf(os.name == 'nt', 'fixture uses a Unix executable script')
    def test_engine_initialization_error_does_not_wait_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / 'bad-engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                              'for line in sys.stdin:\n'
                              ' if line.strip() == "isready": print("info string Error! missing eval", flush=True)\n')
            engine.chmod(0o755)
            settings = root / 'engine_settings.json5'
            settings.write_text(json.dumps([{'path': str(engine), 'multi': 1, 'nodes': 100}]))
            start = time.monotonic()
            with patch.object(miner, 'ENGINE_SETTINGS_JSON_PATH', str(settings)):
                with self.assertRaisesRegex(RuntimeError, 'engine initialization failed.*missing eval'):
                    miner.EngineManager(miner.BookMinerSettings())
            self.assertLess(time.monotonic() - start, 5)

    def engine(self, lines, *, multipv=2, delta=0):
        engine = object.__new__(miner.Engine)
        engine.global_settings = SimpleNamespace(multipv=multipv, multipv_delta=delta, debug_engine=False)
        engine.thread_settings = SimpleNamespace(engine_nodes=100, thread_id=0)
        engine.send_usi = Mock()
        engine.receive_usi = Mock(side_effect=lines)
        return engine

    def test_usi_position_ply_multipv_and_bounded_scores(self):
        engine = self.engine([
            'info string bestmove is not a bestmove response',
            'info multipv 3 score cp 999 pv 3g3f',
            'info multipv 1 score cp 100 lowerbound pv 7g7f',
            'info multipv 2 score cp 20 upperbound pv 2g2f',
            'bestmove 7g7f',
        ])
        moves = engine.go(miner.SFEN_START, 1.0, 42)
        self.assertEqual([(m.move, m.eval) for m in moves], [('7g7f', 100), ('2g2f', 20)])
        self.assertEqual([call.args[0] for call in engine.send_usi.call_args_list], [
            'setoption name MultiPV value 2', f'position sfen {miner.SFEN_START} 42', 'go nodes 100'])

    def test_incomplete_research_does_not_mix_results(self):
        engine = self.engine([
            'info multipv 1 score cp 100 pv 7g7f',
            'info multipv 2 score cp 95 pv 2g2f',
            'bestmove 7g7f',
            'info multipv 1 score cp 200 pv 3g3f',
            'bestmove 3g3f',
        ], delta=10)
        moves = engine.go(miner.SFEN_START, 1.0)
        self.assertEqual([(m.move, m.eval) for m in moves], [('7g7f', 100), ('2g2f', 95)])
        self.assertEqual(engine.last_go_searched_nodes, 150)
        self.assertIn('setoption name MultiPV value 4', [call.args[0] for call in engine.send_usi.call_args_list])

    def test_inconsistent_or_missing_bestmove_data_is_not_saved(self):
        for lines in (["bestmove 7g7f"], ['info score cp 42 pv 2g2f', 'bestmove 7g7f']):
            with self.subTest(lines=lines):
                engine = self.engine(lines)
                with self.assertRaisesRegex(Exception, 'without a score|does not match'):
                    engine.go(miner.SFEN_START, 1.0)
        self.assertEqual(miner.usi_mate_to_yaneuraou_eval('-0'), -miner.VALUE_MATE)

    def test_terminal_result_does_not_create_empty_book_position(self):
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        engine = Mock()
        engine.go.return_value = []
        engine.last_go_searched_nodes = 100
        book = miner.Book()
        result = manager.think_sfen_once(book, engine, miner.SFEN_START, 9, 0, set())
        self.assertIsNone(result[0])
        self.assertFalse(book.body)
        engine.go.assert_called_once_with(miner.SFEN_START, 1.0, 9)

    def test_buffered_output_from_exited_engine_is_consumed(self):
        engine = object.__new__(miner.Engine)
        engine.global_settings = SimpleNamespace(debug_engine=False)
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.search_sfen = ''
        engine.engine = SimpleNamespace(stdout=io.StringIO('bestmove 7g7f\n'), poll=lambda: 0)
        self.assertEqual(engine.receive_usi(), 'bestmove 7g7f')
        with self.assertRaisesRegex(Exception, 'output is closed'):
            engine.receive_usi()


if __name__ == '__main__':
    unittest.main()
