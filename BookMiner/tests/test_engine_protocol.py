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
    def test_auto_backup_waiting_on_manual_save_is_superseded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'settings/engine_settings.json5').write_text('[]')
            settings = root / 'settings/book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 3600}')
            release = root / 'release'
            code = (f'import importlib.util,sys,time,pathlib\ns=importlib.util.spec_from_file_location("miner_overlap", {str(MODULE_PATH)!r})\n'
                    'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n'
                    'original=m.write_yaneuraou_book_records\n'
                    'def held(*args,**kwargs):\n'
                    ' print("[ManualWriteHeld]",flush=True)\n'
                    f' while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n'
                    ' return original(*args,**kwargs)\n'
                    'm.write_yaneuraou_book_records=held\nm.user_input(from_gui=True)\n')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root,
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    def wait_for(text):
                        deadline = time.monotonic() + 15
                        while text not in log.read_text():
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail('missing ' + text + ': ' + log.read_text())
                            time.sleep(0.02)
                    wait_for('[CommandReady]')
                    process.stdin.write('w\n')
                    process.stdin.flush()
                    wait_for('[ManualWriteHeld]')
                    settings.write_text('{auto_save_interval_seconds: 1}')
                    process.stdin.write('reload-settings\n')
                    process.stdin.flush()
                    wait_for('[BackupStart]')
                    release.touch()
                    wait_for('[ManualBackupDone]')
                    time.sleep(0.3)
                    self.assertNotIn('[BackupDone]', log.read_text())
                finally:
                    release.touch()
                    if process.poll() is None:
                        process.stdin.write('!\n')
                        process.stdin.flush()
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_reload_unrelated_settings_preserves_auto_save_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'settings/engine_settings.json5').write_text('[]')
            settings = root / 'settings/book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 3600, max_book_ply: 200}')
            code = (f'import importlib.util,sys\ns=importlib.util.spec_from_file_location("miner_reload", {str(MODULE_PATH)!r})\n'
                    'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n'
                    'm.user_input(from_gui=True)\n')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root,
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    deadline = time.monotonic() + 20
                    while '[CommandReady]' not in log.read_text() or '[BackupNext]' not in log.read_text():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail('startup failed: ' + log.read_text())
                        time.sleep(0.02)
                    time.sleep(1.2)
                    settings.write_text('{auto_save_interval_seconds: 3600, max_book_ply: 199}')
                    process.stdin.write('reload-settings\n')
                    process.stdin.flush()
                    deadline = time.monotonic() + 10
                    while '[SettingsApplied]' not in log.read_text() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    time.sleep(0.2)
                    contents = log.read_text()
                    self.assertIn('[SettingsApplied]', contents)
                    self.assertEqual(contents.count('[BackupNext]'), 1, contents)
                    settings.write_text('{auto_save_interval_seconds: 3599, max_book_ply: 199}')
                    process.stdin.write('reload-settings\n')
                    process.stdin.flush()
                    deadline = time.monotonic() + 10
                    while log.read_text().count('[BackupNext]') < 2 and time.monotonic() < deadline:
                        time.sleep(0.02)
                    contents = log.read_text()
                    self.assertIn('interval=3599', contents)
                finally:
                    if process.poll() is None:
                        process.stdin.write('!\n')
                        process.stdin.flush()
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_concurrent_jobs_receive_distinct_ids(self):
        ids = queue.Queue()
        def allocate():
            for _ in range(500):
                ids.put(miner.get_job_counter())
        threads = [threading.Thread(target=allocate) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        result = [ids.get_nowait() for _ in range(6000)]
        self.assertEqual(len(set(result)), 6000)

    def test_auto_backup_worker_retries_after_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'book/backup').mkdir(parents=True)
            (root / 'settings').mkdir()
            (root / 'settings/engine_settings.json5').write_text('[]')
            (root / 'settings/book_miner_settings.json5').write_text('{auto_save_interval_seconds: 1}')
            code = (f'import importlib.util,sys\ns=importlib.util.spec_from_file_location("miner_retry", {str(MODULE_PATH)!r})\n'
                    'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n'
                    'original=m.save_book_backup\ncount=0\n'
                    'def flaky(*args,**kwargs):\n'
                    ' global count\n count+=1\n'
                    ' if count==1: raise OSError("simulated failure")\n'
                    ' return original(*args,**kwargs)\n'
                    'm.save_book_backup=flaky\nm.user_input(from_gui=True)\n')
            process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            captured = []
            try:
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=.5)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    captured.append(line)
                    if '[BackupDone]' in line:
                        break
                output = ''.join(captured)
                self.assertIn('Exception : auto save failed: simulated failure', output)
                self.assertIn('[BackupDone]', output)
            finally:
                if process.poll() is None:
                    process.stdin.write('!\n')
                    process.stdin.flush()
                    process.wait(timeout=10)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_delayed_enqueue_keeps_its_own_settings(self):
        scheduled = []
        class DelayedThread:
            def __init__(self, target, args, daemon):
                self.target, self.args = target, args
            def start(self):
                scheduled.append(self)
        book, manager = miner.Book(), Mock()
        with patch.object(miner, 'Thread', DelayedThread), patch.object(miner, 'put_position_commands') as put:
            miner.enqueue_position_commands_async(book, 'first.txt', manager, 100, 150, 4)
            miner.enqueue_position_commands_async(book, 'second.txt', manager, 900, 200, 9, 3, 2)
            for thread in reversed(scheduled):
                thread.target(*thread.args)
        self.assertEqual(put.call_args_list[0].args[1:], ('second.txt', manager, 900, 200, 9, 3, 2))
        self.assertEqual(put.call_args_list[1].args[1:], ('first.txt', manager, 100, 150, 4, 0, 0))

    def test_failed_auto_backup_does_not_report_success_and_can_retry(self):
        with patch.object(miner, 'save_book_backup', side_effect=[OSError('disk full'), 'backup.ybb']) as save, \
             patch.object(miner, 'print') as log:
            self.assertFalse(miner.perform_auto_backup(miner.Book(), 'backup'))
            self.assertFalse(any('[BackupDone]' in str(call) for call in log.call_args_list))
            self.assertTrue(any('Exception : auto save failed: disk full' in str(call) for call in log.call_args_list))
            self.assertTrue(miner.perform_auto_backup(miner.Book(), 'backup'))
            self.assertTrue(any('[BackupDone]' in str(call) for call in log.call_args_list))
            self.assertEqual(save.call_count, 2)

    def test_enqueue_merges_duplicates_after_resolving_default_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'think_sfens.txt'
            path.write_text('startpos\nstartpos, book_extend_ply=3, eval_limit=100, game_ply_limit=100\n')
            manager = Mock()
            manager.engine_alive_count.return_value = 1
            miner.put_position_commands(miner.Book(), str(path), manager, 400, 200, 10)
            manager.put_task.assert_called_once()
            task = manager.put_task.call_args.args[0]
            self.assertEqual((task.book_extend_ply, task.eval_limit, task.max_book_ply), (10, 400, 200))

    def test_enqueue_accepts_utf8_bom_and_rejects_unknown_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'think_sfens.txt'
            path.write_text('startpos, eval_limit=123\nstartpos moves 7g7f, eval_limt=999\n', encoding='utf-8-sig')
            manager = Mock()
            manager.engine_alive_count.return_value = 1
            with patch.object(miner, 'print') as log:
                miner.put_position_commands(miner.Book(), str(path), manager, 400, 200, 10)
            manager.put_task.assert_called_once()
            self.assertEqual(manager.put_task.call_args.args[0].eval_limit, 123)
            self.assertTrue(any('line 2' in str(call) and 'unknown metadata: eval_limt' in str(call)
                                for call in log.call_args_list))

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
                return [miner.MoveInfo({1:'7g7f', 2:'3c3d', 3:'2g2f', 4:'8c8d', 5:'2f2e', 6:'8d8e'}[ply], 0)]

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
                self.assertEqual(len(book.body), 6)

    def test_enqueue_crosses_unexplored_alternative_if_best_has_normal_child(self):
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings()
        manager.global_settings = SimpleNamespace(from_gui=False)
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        engine.go.return_value = [miner.MoveInfo('3c3d', 0)]
        book = miner.Book()
        book.body[miner.SFEN_START] = miner.PositionInfo([
            miner.MoveInfo('7g7f', 500), miner.MoveInfo('2g2f', 450)], 1)
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        book.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo('3c3d', 0)], 2)
        task = miner.Task(miner.SFEN_START, 1, 400, position_cmd='startpos moves 2g2f', book_extend_ply=1)
        self.assertEqual(manager.start_thinking_position(book, engine, task), miner.TASK_RESULT_DONE)
        self.assertEqual(engine.go.call_count, 1)

    def test_best_line_can_follow_explored_best_above_eval_limit(self):
        manager = object.__new__(miner.EngineManager)
        book = miner.Book()
        position = miner.PositionInfo([miner.MoveInfo('7g7f', 500)], 1)
        self.assertEqual(manager.get_thinking_candidate_moves(position, 400, None, book, miner.SFEN_START_PLY1), [])
        board = miner.cshogi.Board()
        board.push_usi('7g7f')
        book.body[miner.trim_sfen(board.sfen())] = miner.PositionInfo([miner.MoveInfo('3c3d', 0)], 2)
        self.assertEqual(manager.get_thinking_candidate_moves(position, 400, None, book, miner.SFEN_START_PLY1), ['7g7f'])

    def test_consecutive_search_uses_configured_ratio(self):
        manager = object.__new__(miner.EngineManager)
        manager.book_miner_settings = miner.BookMinerSettings(consecutive_node_ratio=0.45)
        manager.global_settings = SimpleNamespace(from_gui=False)
        engine = Mock()
        engine.thread_settings = SimpleNamespace(thread_id=0)
        engine.last_go_searched_nodes = 100
        engine.go.side_effect = lambda _sfen, _ratio, ply: [miner.MoveInfo({1: '7g7f', 2: '3c3d'}[ply], 0)]
        task = miner.Task(miner.SFEN_START, 1, 100, book_extend_ply=2)
        self.assertEqual(manager.start_thinking(miner.Book(), engine, task), miner.TASK_RESULT_DONE)
        self.assertEqual([call.args[1] for call in engine.go.call_args_list], [1.0, 0.45])

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

    def test_nodes_are_counted_once_per_search_including_research(self):
        engine = self.engine(['info nodes 3', 'info string nodes 999',
                              'info multipv 1 nodes 8 score cp 100 pv 7g7f',
                              'info multipv 2 nodes 8 score cp 95 pv 2g2f', 'bestmove 7g7f',
                              'info nodes 12', 'info nodes -1',
                              'info multipv 1 score cp 200 pv 3g3f nodes 999', 'bestmove 3g3f'], delta=10)
        engine.go(miner.SFEN_START, 1.0)
        self.assertEqual(engine.last_go_searched_nodes, 20)

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
