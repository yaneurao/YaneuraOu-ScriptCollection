import json
import queue
import threading
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY') and os.name != 'nt',
                     'Set BOOKMINER_TEST_BINARY to a built POSIX BookMinerCpp executable')
class ShutdownTest(unittest.TestCase):
    def test_search_sends_standard_usi_sfen_with_ply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bookminer'
            shutil.copy2(os.environ['BOOKMINER_TEST_BINARY'], binary)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            engine = root / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                              'for line in sys.stdin:\n'
                              ' line = line.strip()\n'
                              ' if line == "usi": print("usiok", flush=True)\n'
                              ' elif line == "isready": print("readyok", flush=True)\n'
                              ' elif line.startswith("position "): pathlib.Path("position.txt").write_text(line)\n'
                              ' elif line.startswith("go "):\n'
                              '  print("info multipv 1 score cp 42 pv 7g7f", flush=True)\n'
                              '  print("info multipv 2 score cp -42 pv 2g2f", flush=True)\n'
                              '  print("bestmove 7g7f", flush=True)\n'
                              ' elif line == "quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'multipv': 2, 'multipv_delta': 0, 'nodes': 100}]))
            (root / 'book/think_sfens.txt').write_text('startpos moves 7g7f\n')
            process = subprocess.Popen([str(binary), '--from_gui'], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            try:
                process.stdin.write('sd 0 1 200 0 2000\ne\n')
                process.stdin.flush()
                captured = []
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    captured.append(line)
                    if '[TaskQueueDone]' in line:
                        break
                self.assertIn('[TaskQueueDone]', ''.join(captured))
                sfen = 'lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b -'
                self.assertEqual((root / 'position.txt').read_text(), f'position sfen {sfen} 1')
                process.stdin.write('!\n')
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_existing_path_position_updates_to_shorter_ply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bookminer'
            shutil.copy2(os.environ['BOOKMINER_TEST_BINARY'], binary)
            (root / 'settings').mkdir()
            backup = root / 'book/backup'
            backup.mkdir(parents=True)
            sfen = 'lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b -'
            child = 'lnsgkgsnl/1r5b1/ppppppppp/9/9/2P6/PP1PPPPPP/1B5R1/LNSGKGSNL w -'
            (backup / 'book_miner-20200101000000_1.db').write_text(
                f'#YANEURAOU-DB2016 1.00\nsfen {sfen} 20\n7g7f none 0 1\n'
                f'sfen {child} 20\n3c3d none 0 1\n')
            engine = root / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                              'for line in sys.stdin:\n'
                              ' line = line.strip()\n'
                              ' if line == "usi": print("usiok", flush=True)\n'
                              ' elif line == "isready": print("readyok", flush=True)\n'
                              ' elif line == "quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'multipv': 1, 'nodes': 100}]))
            (root / 'book/think_sfens.txt').write_text('startpos moves 7g7f\n')
            process = subprocess.Popen([str(binary), '--from_gui'], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            try:
                process.stdin.write('sd 0 1 200 0 2000\ne\n')
                process.stdin.flush()
                captured = []
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    captured.append(line)
                    if '[TaskQueueDone]' in line:
                        break
                self.assertIn('[TaskQueueDone]', ''.join(captured))
                process.stdin.write('q\n')
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
                latest = sorted(backup.glob('book_miner-*.ybb'))[-1]
                data = latest.read_bytes()
                record_count = struct.unpack_from('<Q', data, 16)[0]
                self.assertEqual(record_count, 2)
                plies = [struct.unpack_from('<H', data, 32 + i * 44 + 40)[0]
                         for i in range(record_count)]
                self.assertEqual(sorted(plies), [1, 2])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_deferred_task_waits_for_search_longer_than_ten_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bookminer'
            shutil.copy2(os.environ['BOOKMINER_TEST_BINARY'], binary)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            engine = root / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys,time\n'
                              'for line in sys.stdin:\n'
                              ' line = line.strip()\n'
                              ' if line == "usi": print("usiok", flush=True)\n'
                              ' elif line == "isready": print("readyok", flush=True)\n'
                              ' elif line.startswith("go "):\n'
                              '  time.sleep(11.5)\n'
                              '  print("info multipv 1 score cp 42 pv 7g7f", flush=True)\n'
                              '  print("info multipv 2 score cp -42 pv 2g2f", flush=True)\n'
                              '  print("bestmove 7g7f", flush=True)\n'
                              ' elif line == "quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'multi': 2, 'multipv': 2, 'multipv_delta': 0, 'nodes': 100}]))
            (root / 'book/think_sfens.txt').write_text('startpos moves 7g7f\nstartpos moves 2g2f\n')
            process = subprocess.Popen([str(binary), '--from_gui'], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            try:
                process.stdin.write('sd 0 1 200 0 2000\ne\n')
                process.stdin.flush()
                captured = []
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    captured.append(line)
                    if '[TaskQueueDone]' in line:
                        break
                output = ''.join(captured)
                self.assertIn('[TaskQueueDone]', output)
                self.assertNotIn('[TaskQueueDeferLimit]', output)
                self.assertIn('failed=0', next(line for line in captured if '[TaskQueueDone]' in line))
                process.stdin.write('!\n')
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_quit_does_not_wait_for_inflight_search_before_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bookminer'
            shutil.copy2(os.environ['BOOKMINER_TEST_BINARY'], binary)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            engine = root / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport pathlib, sys, time\n'
                              'for line in sys.stdin:\n'
                              ' line = line.strip()\n'
                              ' if line == "usi": print("usiok", flush=True)\n'
                              ' elif line == "isready": print("readyok", flush=True)\n'
                              ' elif line.startswith("go "):\n'
                              '  pathlib.Path("go-started").write_text("yes")\n'
                              '  time.sleep(1)\n'
                              '  print("info multipv 1 score cp 42 pv 7g7f", flush=True)\n'
                              '  print("bestmove 7g7f", flush=True)\n'
                              ' elif line == "quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'multipv': 4, 'nodes': 100}]))
            (root / 'book/think_sfens.txt').write_text('startpos moves 7g7f\n')
            process = subprocess.Popen([str(binary), '--from_gui'], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                process.stdin.write('sd 0 1 200 0 2000\ne\n')
                process.stdin.flush()
                deadline = time.monotonic() + 10
                while not (root / 'go-started').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((root / 'go-started').exists(), 'engine did not start searching')
                process.stdin.write('q\n')
                process.stdin.flush()
                output = process.communicate(timeout=15)[0]
                self.assertEqual(process.returncode, 0, output)
                self.assertIn('..save_book_backup has done, 0 positions.', output)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_failed_search_is_reported_with_position_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'bookminer'
            shutil.copy2(os.environ['BOOKMINER_TEST_BINARY'], binary)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            engine = root / 'mock-engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                              'for line in sys.stdin:\n'
                              ' line = line.strip()\n'
                              ' if line == "usi": print("usiok", flush=True)\n'
                              ' elif line == "isready": print("readyok", flush=True)\n'
                              ' elif line.startswith("go "): print("bestmove 7g7f", flush=True)\n'
                              ' elif line == "quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'multipv': 4, 'nodes': 100}]))
            (root / 'book/think_sfens.txt').write_text('startpos moves 7g7f\n')
            process = subprocess.Popen([str(binary), '--from_gui'], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            try:
                process.stdin.write('e\n')
                process.stdin.flush()
                captured = []
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                    except queue.Empty:
                        break
                    captured.append(line)
                    if '[TaskQueueDone]' in line:
                        break
                output = ''.join(captured)
                self.assertIn('Exception : task failed, job=1 engine=0 position=startpos moves 7g7f', output)
                done = next((line for line in captured if '[TaskQueueDone]' in line), '')
                self.assertIn('failed=1', done)
                process.stdin.write('!\n')
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
