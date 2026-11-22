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
class ManualBackupCancellationTests(unittest.TestCase):
    def run_scenario(self, cancel_manual, with_peta=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            (root / 'settings').mkdir()
            content = ('#YANEURAOU-DB2016 1.00\n'
                       'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
                       '7g7f none 0 0\n')
            source = backup / 'book_miner-20261029000000_1.db'
            source.write_text(content)
            manual_release = root / 'manual-release'
            peta_release = root / 'peta-release'
            engine = root / 'material'
            engine.write_text('#!/usr/bin/env python3\nimport pathlib,sys,time\n'
                f'root=pathlib.Path({str(root)!r})\n'
                'for line in sys.stdin:\n'
                ' if line.startswith("makebook "):\n'
                '  print("peta-child-ready",flush=True)\n'
                '  while not (root/"peta-release").exists(): time.sleep(.01)\n'
                '  src=root/"book"/line.split()[2]\n'
                '  dst=root/"book"/line.split()[3]\n'
                '  dst.write_text(src.read_text())\n'
                '  print("Making a peta-shock book has been completed.",flush=True)\n')
            engine.chmod(0o755)
            (root / 'settings' / 'engine_settings.json5').write_text('[]')
            (root / 'settings' / 'book_miner_settings.json5').write_text(json.dumps({
                'peta_shock_engine_path': str(engine), 'peta_shock_threads': 1}))
            code = (f'import importlib.util,sys,time,pathlib\n'
                    f's=importlib.util.spec_from_file_location("manual_fixture",{str(ROOT / "BookMiner.py")!r})\n'
                    'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n'
                    f'm.__file__={str(root / "BookMiner.py")!r}\n'
                    'original=m.write_to_yaneuraou_book\n'
                    'def slow(book,directory,ply_limit=None,cancel=None):\n'
                    ' print("[ManualTestStarted]",flush=True)\n'
                    f' while not pathlib.Path({str(manual_release)!r}).exists():\n'
                    '  m.check_peta_shock_cancel(cancel);time.sleep(.01)\n'
                    ' return original(book,directory,ply_limit,cancel)\n'
                    'm.write_to_yaneuraou_book=slow\n'
                    'm.user_input(from_gui=True)\n')
            process = subprocess.Popen([sys.executable, '-u', '-c', code], cwd=root, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            lines = queue.Queue()
            reader = threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True)
            reader.start()
            captured = []
            def wait_for(marker):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try: line = lines.get(timeout=.2)
                    except queue.Empty:
                        if process.poll() is not None: break
                        continue
                    captured.append(line)
                    if marker in line: return
                self.fail(f'missing {marker}: ' + ''.join(captured[-40:]))
            def send(command):
                process.stdin.write(command + '\n')
                process.stdin.flush()
            try:
                wait_for('[CommandReady]')
                if with_peta:
                    send('pl')
                    wait_for('peta-child-ready')
                send('w')
                wait_for('[ManualTestStarted]')
                if cancel_manual:
                    send('cancel-manual-backup')
                    wait_for('[ManualBackupCancelled]')
                    self.assertNotIn('[PetaShockCancelled]', ''.join(captured))
                    if with_peta:
                        peta_release.touch()
                        wait_for('[PetaCommandDone]')
                    else:
                        send('w')
                        wait_for('[ManualTestStarted]')
                        manual_release.touch()
                        wait_for('[ManualBackupDone]')
                else:
                    send('cancel-peta-shock')
                    wait_for('[PetaShockCancelled]')
                    self.assertNotIn('[ManualBackupCancelled]', ''.join(captured))
                    manual_release.touch()
                    wait_for('[ManualBackupDone]')
                self.assertEqual(source.read_text(), content)
            finally:
                manual_release.touch()
                peta_release.touch()
                if process.poll() is None:
                    send('!')
                    process.wait(timeout=20)
                reader.join(timeout=5)
                process.stdin.close()
                process.stdout.close()

    def test_cancel_manual_keeps_peta_running(self):
        self.run_scenario(True)

    def test_cancel_peta_keeps_manual_running(self):
        self.run_scenario(False)

    def test_cancel_then_save_again_without_peta(self):
        self.run_scenario(True, with_peta=False)
