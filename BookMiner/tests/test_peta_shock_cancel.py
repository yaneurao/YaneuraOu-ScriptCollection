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
    def run_backend(self, binary=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book/backup'
            backup.mkdir(parents=True)
            (root / 'settings').mkdir()
            source = backup / 'book_miner-20261029000000_1.db'
            target = backup / 'peta_book-20261029000000_1.db'
            temporary = backup / 'tmp-peta_book-20261029000000_1.db'
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
                              f'  pathlib.Path({str(temporary)!r}).write_text("partial")\n'
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
                    self.assertTrue(temporary.exists())
                    send('cancel-peta-shock')
                    wait_for('[PetaShockCancelled]')
                    self.assertFalse(temporary.exists())
                    self.assertEqual(target.read_text(), content)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                send('pn 30 10 200 6 400 0')
                wait_for('[PetaNextDone]')
                self.assertIn('startpos moves 7g7f', (root / 'book/think_sfens.txt').read_text())
            finally:
                if process.poll() is None:
                    send('!')
                    process.wait(timeout=20)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_python_process_stops_child_and_preserves_book(self):
        self.run_backend()

    @unittest.skipUnless(os.environ.get('BOOKMINER_TEST_BINARY'), 'C++ binary not provided')
    def test_cpp_process_stops_child_and_preserves_book(self):
        self.run_backend(os.environ['BOOKMINER_TEST_BINARY'])
