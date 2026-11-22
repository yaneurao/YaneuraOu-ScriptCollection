import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PetaNextCancelTests(unittest.TestCase):
    def run_shutdown_test(self, cpp_binary=None):
        for command in ('q', '!', 'eof'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'book').mkdir()
                (root / 'settings').mkdir()
                (root / 'settings/engine_settings.json5').write_text('[]')
                (root / 'book/peta_start_sfens.txt').write_text('startpos, 50\n' * 50000)
                output = root / 'book/think_sfens.txt'
                output.write_text('previous\n')
                args = ([cpp_binary, '--from_gui', '--work-dir', str(root)] if cpp_binary else
                        [sys.executable, '-u', str(ROOT / 'BookMiner.py'), '--from_gui'])
                process = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, bufsize=1)
                lines = queue.Queue()
                reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
                reader.start()
                captured = []
                def wait_for(needle):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        try: line = lines.get(timeout=.2)
                        except queue.Empty:
                            if process.poll() is not None: break
                            continue
                        captured.append(line)
                        if needle in line: return
                    self.fail(f'missing {needle}: ' + ''.join(captured[-30:]))
                try:
                    wait_for('[CommandReady]')
                    process.stdin.write('pn 30 100 200 6 400 0\n')
                    process.stdin.flush()
                    wait_for('read start sfens')
                    if command == 'eof':
                        process.stdin.close()
                    else:
                        process.stdin.write(command + '\n')
                        process.stdin.flush()
                    wait_for('[PetaNextCancelled]')
                    process.wait(timeout=20)
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(output.read_text(), 'previous\n')
                    self.assertFalse(list((root / 'book').glob('.*think_sfens*')))
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    reader.join(timeout=5)
                    process.stdin.close()
                    process.stdout.close()

    def run_cancel_test(self, cpp_binary=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'book').mkdir()
            (root / 'settings').mkdir()
            (root / 'settings/engine_settings.json5').write_text('[]')
            starts = root / 'book/peta_start_sfens.txt'
            starts.write_text('startpos, 50\n' * 50000)
            output = root / 'book/think_sfens.txt'
            output.write_text('previous\n')
            if cpp_binary:
                args = [cpp_binary, '--from_gui', '--work-dir', str(root)]
            else:
                args = [sys.executable, '-u', str(ROOT / 'BookMiner.py'), '--from_gui']
            process = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            captured = []
            def wait_for(needle):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=0.5)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    captured.append(line)
                    if needle in line:
                        return
                self.fail(f'missing {needle}: ' + ''.join(captured[-30:]))
            try:
                wait_for('[CommandReady]')
                process.stdin.write('pn 30 100 200 6 400 0\n')
                process.stdin.flush()
                wait_for('read start sfens')
                process.stdin.write('cancel-peta-next\n')
                process.stdin.flush()
                wait_for('[PetaNextCancelled]')
                self.assertEqual(output.read_text(), 'previous\n')
                starts.write_text('startpos\n')
                process.stdin.write('pn 30 100 200 6 400 0\n')
                process.stdin.flush()
                wait_for('[PetaNextDone]')
                self.assertTrue(output.read_text().startswith('startpos,'))
                process.stdin.write('pj 30 100 200 6 400\n')
                process.stdin.flush()
                wait_for('[PetaRejoinDone]')
                self.assertFalse(any('Error : peta command is running' in line for line in captured))
            finally:
                if process.poll() is None:
                    process.stdin.write('!\n')
                    process.stdin.flush()
                    process.wait(timeout=20)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_python_process_cancels_and_runs_again(self):
        self.run_cancel_test()

    def test_python_shutdown_cancels_next(self):
        self.run_shutdown_test()

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_process_cancels_and_runs_again(self):
        self.run_cancel_test(os.environ['BOOKMINER_TEST_BINARY'])

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_shutdown_cancels_next(self):
        self.run_shutdown_test(os.environ['BOOKMINER_TEST_BINARY'])
