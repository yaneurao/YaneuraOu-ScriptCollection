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


class PetaUnsolvedCancelTests(unittest.TestCase):
    def run_cancel_test(self, cpp_binary=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'book').mkdir()
            (root / 'settings').mkdir()
            (root / 'settings/engine_settings.json5').write_text('[]')
            source = root / 'book/think_unsolved_sfens.txt'
            source.write_text('startpos\n' * 300000)
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
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=.2)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    captured.append(line)
                    if needle in line:
                        return
                self.fail(f'missing {needle}: ' + ''.join(captured[-30:]))
            def send(command):
                process.stdin.write(command + '\n')
                process.stdin.flush()
            try:
                wait_for('[CommandReady]')
                send('pu 10,8,6,4 9999 200 1 400')
                wait_for('[PetaUnsolvedProgress]')
                if cpp_binary is None:
                    # A real timed update must arrive while the worker is busy.
                    wait_for('[PetaUnsolvedProgress]')
                    self.assertTrue(any('lines=' in line and 'lines=0 ' not in line
                                        for line in captured if '[PetaUnsolvedProgress]' in line))
                send('cancel-peta-next')
                wait_for('[PetaNextCancelIgnored]')
                send('cancel-peta-unsolved')
                wait_for('[PetaUnsolvedCancelled]')
                self.assertEqual(output.read_text(), 'previous\n')
                source.write_text('startpos\n')
                send('pu 10,8,6,4 9999 200 1 400')
                wait_for('[PetaUnsolvedDone]')
                self.assertTrue(output.read_text().startswith('startpos,'))
            finally:
                if process.poll() is None:
                    send('!')
                    process.wait(timeout=20)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def run_shutdown_test(self, cpp_binary=None):
        for command in ('q', '!', 'eof'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'book').mkdir()
                (root / 'settings').mkdir()
                (root / 'settings/engine_settings.json5').write_text('[]')
                (root / 'book/think_unsolved_sfens.txt').write_text('startpos\n' * 300000)
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
                    deadline = time.monotonic() + 30
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
                    process.stdin.write('pu 10 9999 200 1 400\n')
                    process.stdin.flush()
                    wait_for('[PetaUnsolvedProgress]')
                    if command == 'eof':
                        process.stdin.close()
                    else:
                        process.stdin.write(command + '\n')
                        process.stdin.flush()
                    wait_for('[PetaUnsolvedCancelled]')
                    process.wait(timeout=20)
                    self.assertEqual(process.returncode, 0)
                    self.assertEqual(output.read_text(), 'previous\n')
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    reader.join(timeout=5)
                    process.stdin.close()
                    process.stdout.close()

    def test_python_cancel_and_restart(self):
        self.run_cancel_test()

    def test_python_shutdown_cancels_unsolved(self):
        self.run_shutdown_test()

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_shutdown_cancels_unsolved(self):
        self.run_shutdown_test(os.environ['BOOKMINER_TEST_BINARY'])

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_cancel_and_restart(self):
        self.run_cancel_test(os.environ['BOOKMINER_TEST_BINARY'])
