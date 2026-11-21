import json
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


@unittest.skipIf(os.name == 'nt', 'fixture uses a Unix executable script')
class PetaShockCancelTests(unittest.TestCase):
    def run_backend(self, binary=None, eof_during_shock=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book/backup'
            backup.mkdir(parents=True)
            (root / 'settings').mkdir()
            source = backup / 'book_miner-20261029000000_1.db'
            target = backup / 'peta_book-20261029000000_1.db'
            temporary_path_file = root / 'temporary-path.txt'
            legacy_temporary = backup / 'tmp-peta_book-20261029000000_1.db'
            legacy_temporary.write_text('unrelated')
            content = ('#YANEURAOU-DB2016 1.00\n'
                       'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
                       '7g7f none 0 1\n')
            source.write_text(content)
            target.write_text(content)
            child_pid = root / 'child.pid'
            engine = root / 'material-engine'
            engine.write_text('#!/usr/bin/env python3\nimport os,pathlib,sys,time\n'
                              f'pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()))\n'
                              'for line in sys.stdin:\n'
                              ' if line.startswith("makebook "):\n'
                              f'  target = pathlib.Path({str(root / "book")!r}) / line.split()[3]\n'
                              '  target.write_text("partial")\n'
                              f'  pathlib.Path({str(temporary_path_file)!r}).write_text(str(target.resolve()))\n'
                              '  print("child-ready",flush=True)\n'
                              '  time.sleep(120)\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text('[]')
            (root / 'settings/book_miner_settings.json5').write_text(json.dumps({
                'peta_shock_engine_path': str(engine), 'peta_shock_threads': 1}))
            args = ([binary, '--from_gui', '--work-dir', str(root)] if binary else
                    [sys.executable, '-u', str(ROOT / 'BookMiner.py'), '--from_gui'])
            process = subprocess.Popen(args, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, bufsize=1)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            captured = []
            def send(command):
                process.stdin.write(command + '\n')
                process.stdin.flush()
            def wait_for(needle):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        line = lines.get(timeout=.5)
                    except queue.Empty:
                        if process.poll() is not None:
                            break
                        continue
                    captured.append(line)
                    if needle in line:
                        return
                self.fail(f'missing {needle}: ' + ''.join(captured[-35:]))
            try:
                wait_for('[CommandReady]')
                send('r')
                wait_for('[PetaReadDone]')
                for command in ('p', 'pl'):
                    send(command)
                    wait_for('[peta_shock] child-ready')
                    pid = int(child_pid.read_text())
                    temporary = Path(temporary_path_file.read_text())
                    self.assertTrue(temporary.exists())
                    if eof_during_shock:
                        process.stdin.close()
                        process.wait(timeout=15)
                        self.assertEqual(process.returncode, 0)
                        self.assertFalse(temporary.exists())
                        self.assertEqual(target.read_text(), content)
                        self.assertEqual(legacy_temporary.read_text(), 'unrelated')
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                        return
                    if command == 'pl':
                        send('w')
                    send('cancel-peta-shock')
                    wait_for('[PetaShockCancelled]')
                    if command == 'pl' and not any('[ManualBackupDone]' in line for line in captured):
                        wait_for('[ManualBackupDone]')
                    self.assertFalse(temporary.exists())
                    self.assertEqual(target.read_text(), content)
                    self.assertEqual(legacy_temporary.read_text(), 'unrelated')
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                send('pn 30 10 200 6 400 0')
                wait_for('[PetaNextDone]')
                self.assertIn('startpos moves 7g7f', (root / 'book/think_sfens.txt').read_text())
            finally:
                if process.poll() is None:
                    if process.stdin.closed:
                        process.kill()
                    else:
                        send('!')
                    process.wait(timeout=20)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_python_process_stops_child_and_preserves_book(self):
        self.run_backend()

    def test_python_stops_peta_child_when_input_closes(self):
        self.run_backend(eof_during_shock=True)

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_process_stops_child_and_preserves_book(self):
        self.run_backend(os.environ['BOOKMINER_TEST_BINARY'])

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_stops_peta_child_when_input_closes(self):
        self.run_backend(os.environ['BOOKMINER_TEST_BINARY'], eof_during_shock=True)
