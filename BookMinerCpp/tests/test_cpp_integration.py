import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source'
YANE_SOURCE = ROOT.parents[1] / 'YaneuraOu' / 'source'


@unittest.skipUnless(os.name != 'nt' and shutil.which('make') and shutil.which('g++')
                     and (YANE_SOURCE / 'position.cpp').is_file(),
                     'requires POSIX, make, g++ and sibling YaneuraOu source')
class CppIntegrationTest(unittest.TestCase):
    def test_unsolved_generation_obeys_normal_book_eval_limit(self):
        import json
        try:
            import cshogi
        except ImportError:
            self.skipTest('cshogi is required to construct fixture positions')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            (root / 'settings').mkdir()
            start = cshogi.Board().sfen()
            (backup / 'book_miner-20200101000000_1.db').write_text(
                f'#YANEURAOU-DB2016 1.00\nsfen {start}\n7g7f none 500 20\n')
            (backup / 'peta_book-20200101000000_1.db').write_text(
                f'#YANEURAOU-DB2016 1.00\nsfen {start}\n7g7f none 0 0\n')
            (root / 'book' / 'think_unsolved_sfens.txt').write_text('startpos\n')
            engine = root / 'engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                'for line in sys.stdin:\n'
                ' if line.strip()=="usi": print("usiok",flush=True)\n'
                ' elif line.strip()=="isready": print("readyok",flush=True)\n'
                ' elif line.startswith("go "):\n'
                '  open("unexpected-search.txt","w").write(line)\n'
                '  print("bestmove 7g7f",flush=True)\n'
                ' elif line.strip()=="quit": break\n')
            engine.chmod(0o755)
            (root / 'settings' / 'engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'nodes': 100, 'multipv': 1}]))
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                def wait_for(needle):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        data = log.read_text()
                        if needle in data: return
                        if process.poll() is not None: break
                        time.sleep(.02)
                    self.fail('missing ' + needle + ': ' + log.read_text())
                def send(command):
                    process.stdin.write(command + '\n')
                    process.stdin.flush()
                try:
                    wait_for('[CommandReady]')
                    send('r')
                    wait_for('[PetaReadDone]')
                    send('pu 10,8,6,4 9999 200 1 400')
                    wait_for('[PetaUnsolvedDone]')
                    line = (root / 'book' / 'think_sfens.txt').read_text()
                    self.assertEqual(line, '')
                    self.assertFalse((root / 'unexpected-search.txt').exists(), log.read_text())
                finally:
                    if process.poll() is None:
                        send('!')
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_unsolved_enqueues_and_searches_end_of_peta_pv(self):
        import json
        try:
            import cshogi
        except ImportError:
            self.skipTest('cshogi is required to construct fixture positions')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            (root / 'settings').mkdir()
            board = cshogi.Board()
            pv = ['7g7f', '3c3d', '2g2f', '8c8d']
            records = []
            for index, move in enumerate(pv):
                records.append(f'sfen {board.sfen()}\n{move} none 20 {max(0, 2 - index)}\n')
                board.push_usi(move)
            target = ' '.join(board.sfen().split()[:3])
            reply = cshogi.move_to_usi(next(iter(board.legal_moves)))
            content = '#YANEURAOU-DB2016 1.00\n' + ''.join(records)
            (backup / 'book_miner-20200101000000_4.db').write_text(content)
            (backup / 'peta_book-20200101000000_4.db').write_text(content)
            (root / 'book' / 'think_unsolved_sfens.txt').write_text('startpos\n')
            engine = root / 'engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                f'target={target!r}\nreply={reply!r}\n'
                'for line in sys.stdin:\n'
                ' if line.strip()=="usi": print("usiok",flush=True)\n'
                ' elif line.strip()=="isready": print("readyok",flush=True)\n'
                ' elif line.startswith("position "): open("searches.txt","a").write(line.strip()+"\\n")\n'
                ' elif line.startswith("go "):\n'
                '  print("info multipv 1 score cp 0 pv "+reply,flush=True)\n'
                '  print("bestmove "+reply,flush=True)\n'
                ' elif line.strip()=="quit": break\n')
            engine.chmod(0o755)
            (root / 'settings' / 'engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'nodes': 100, 'multipv': 1}]))
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                def wait_for(needle):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        data = log.read_text()
                        if needle in data: return
                        if process.poll() is not None: break
                        time.sleep(.02)
                    self.fail('missing ' + needle + ': ' + log.read_text())
                def send(command):
                    process.stdin.write(command + '\n')
                    process.stdin.flush()
                try:
                    wait_for('[CommandReady]')
                    send('r')
                    wait_for('[PetaReadDone]')
                    send('pu 10,8,6,4 9999 200 1 400')
                    wait_for('[PetaUnsolvedDone]')
                    self.assertIn('startpos moves 7g7f 3c3d 2g2f 8c8d,',
                                  (root / 'book' / 'think_sfens.txt').read_text())
                    send('e')
                    wait_for('[TaskQueueJobDone]')
                    searches = (root / 'searches.txt').read_text().splitlines()
                    self.assertEqual(len(searches), 1, log.read_text())
                    self.assertIn(target, searches[0])
                finally:
                    if process.poll() is None:
                        send('!')
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_eof_waits_for_manual_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(self.binary), '--from_gui', '--work-dir', directory],
                input='w\n', text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[ManualBackupDone]', result.stdout)
            backups = list((Path(directory) / 'book' / 'backup').glob('book_miner-*'))
            self.assertEqual(len(backups), 1, result.stdout)

    def test_two_branch_leaves_each_extend_twenty_plies(self):
        import importlib.util
        import json
        try:
            import cshogi  # noqa: F401
        except ImportError:
            self.skipTest('cshogi is required to construct fixture positions')
        fixture = ROOT.parents[0] / 'BookMiner' / 'tests' / 'test_extend_ply.py'
        spec = importlib.util.spec_from_file_location('extend_branch_fixture', fixture)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root_sfen, first_moves, lines = module.make_branch_lines()
        first = [module.miner.cshogi.move_to_usi(move) for move in first_moves]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            (root / 'book' / 'think_sfens.txt').write_text('startpos\n')
            engine = root / 'engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                f'root={root_sfen!r}\nfirst={first!r}\nlines={lines!r}\n'
                'key=root\nfor line in sys.stdin:\n'
                ' line=line.strip()\n'
                ' if line=="usi": print("usiok",flush=True)\n'
                ' elif line=="isready": print("readyok",flush=True)\n'
                ' elif line.startswith("position "):\n'
                '  key=" ".join(line.split()[2:-1])\n'
                '  open("searches.txt","a").write(key+"\\n")\n'
                ' elif line.startswith("go "):\n'
                '  moves=first if key==root else [lines[key]]\n'
                '  for i,move in enumerate(moves):\n'
                '   print(f"info multipv {i+1} score cp {-i} pv {move}",flush=True)\n'
                '  print("bestmove "+moves[0],flush=True)\n'
                ' elif line=="quit": break\n')
            engine.chmod(0o755)
            (root / 'settings' / 'engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'nodes': 100, 'multipv': 2, 'multipv_delta': 0}]))
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                def wait_for(needle):
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        data = log.read_text()
                        if needle in data: return
                        if process.poll() is not None: break
                        time.sleep(.02)
                    self.fail('missing ' + needle + ': ' + log.read_text())
                try:
                    wait_for('[CommandReady]')
                    process.stdin.write('sd 30 99999 200 20 400\neb 2 1\n')
                    process.stdin.flush()
                    wait_for('[TaskQueueJobDone]')
                    searches = (root / 'searches.txt').read_text().splitlines()
                    self.assertEqual(len(searches), 41, log.read_text())
                    self.assertEqual(len(set(searches)), 41)
                    self.assertEqual(set(searches), {root_sfen, *lines.keys()})
                finally:
                    if process.poll() is None:
                        process.stdin.write('!\n')
                        process.stdin.flush()
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_twenty_ply_and_branch_extension_end_to_end(self):
        import importlib.util
        import json
        try:
            import cshogi  # noqa: F401
        except ImportError:
            self.skipTest('cshogi is required to construct fixture positions')
        fixture = ROOT.parents[0] / 'BookMiner' / 'tests' / 'test_extend_ply.py'
        spec = importlib.util.spec_from_file_location('extend_ply_fixture', fixture)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        positions, moves = module.make_line(30)
        cases = (("e", 200, None, None, 0, 20), ("e", 200, 6, None, 0, 6),
                 ("e", 7, None, None, 0, 6), ("e", 200, None, 5, 0, 5),
                 ("e", 200, None, None, 7, 20),
                 ("eb 1 3", 200, None, None, 0, 23),
                 ("eb 1 3", 200, None, None, 7, 23))
        for command, max_ply, metadata, high_eval_ply, existing, expected in cases:
            with self.subTest(command=command, max_ply=max_ply, metadata=metadata, high_eval_ply=high_eval_ply, existing=existing), \
                 tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'settings').mkdir()
                (root / 'book').mkdir()
                if existing:
                    backup = root / 'book' / 'backup'
                    backup.mkdir()
                    entries = sorted((positions[i], moves[i]) for i in range(existing))
                    (backup / f'book_miner-20200101000000_{existing}.db').write_text(
                        '#YANEURAOU-DB2016 1.00\n' + ''.join(
                            f'sfen {position}\n{move} none 0 0\n' for position, move in entries))
                (root / 'book' / 'think_sfens.txt').write_text(
                    'startpos' + (f', book_extend_ply={metadata}' if metadata is not None else '') + '\n')
                engine = root / 'engine'
                engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                    f'moves = {moves!r}\nhigh_ply = {high_eval_ply!r}\n'
                    'ply = 1\nfor line in sys.stdin:\n'
                    ' line=line.strip()\n'
                    ' if line=="usi": print("usiok",flush=True)\n'
                    ' elif line=="isready": print("readyok",flush=True)\n'
                    ' elif line.startswith("position "):\n'
                    '  ply=int(line.split()[-1])\n'
                    '  open("searches.txt","a").write(str(ply)+"\\n")\n'
                    ' elif line.startswith("go "):\n'
                    '  move=moves[ply-1]\n'
                    '  score=500 if ply==high_ply else 0\n'
                    '  print(f"info depth 1 multipv 1 score cp {score} pv {move}",flush=True)\n'
                    '  print("bestmove "+move,flush=True)\n'
                    ' elif line=="quit": break\n')
                engine.chmod(0o755)
                (root / 'settings' / 'engine_settings.json5').write_text(json.dumps([
                    {'path': str(engine), 'nodes': 100, 'multipv': 1}]))
                log = root / 'output.log'
                with log.open('w') as output:
                    process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                        stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                    def wait_for(needle):
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            data = log.read_text()
                            if needle in data: return
                            if process.poll() is not None: break
                            time.sleep(.02)
                        self.fail('missing ' + needle + ': ' + log.read_text())
                    try:
                        wait_for('[CommandReady]')
                        process.stdin.write(f'sd 30 99999 {max_ply} 20 400\n{command}\n')
                        process.stdin.flush()
                        wait_for('[TaskQueueJobDone]')
                        searches = (root / 'searches.txt').read_text().splitlines()
                        self.assertEqual(searches, [str(i) for i in range(existing + 1, expected + 1)])
                    finally:
                        if process.poll() is None:
                            process.stdin.write('!\n')
                            process.stdin.flush()
                        process.wait(timeout=20)
                        process.stdin.close()

    def test_peta_shock_cancellation_preserves_unrelated_temporary_file(self):
        env = os.environ.copy()
        env['BOOKMINER_TEST_BINARY'] = str(self.binary)
        test_dir = ROOT.parents[0] / 'BookMiner' / 'tests'
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(test_dir),
                        '-p', 'test_peta_shock_cancel.py'], env=env, check=True,
                       capture_output=True, text=True, timeout=90)

    def test_cpp_peta_cancel_and_immediate_next_command(self):
        env = os.environ.copy()
        env['BOOKMINER_TEST_BINARY'] = str(self.binary)
        test_dir = ROOT.parents[0] / 'BookMiner' / 'tests'
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(test_dir), '-p', 'test_peta_next_cancel.py'],
                       env=env, check=True, capture_output=True, text=True, timeout=60)

    def test_cpp_peta_unsolved_cancel_and_restart(self):
        env = os.environ.copy()
        env['BOOKMINER_TEST_BINARY'] = str(self.binary)
        test_dir = ROOT.parents[0] / 'BookMiner' / 'tests'
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(test_dir),
                        '-p', 'test_peta_unsolved_cancel.py'], env=env, check=True,
                       capture_output=True, text=True, timeout=70)

    def test_job_pause_resume_and_delete_with_running_engine(self):
        import json
        try:
            import cshogi
        except ImportError:
            self.skipTest('cshogi is required to construct fixture positions')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            engine = root / 'engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys,time\n'
                'for line in sys.stdin:\n'
                ' line=line.strip()\n'
                ' if line=="usi": print("usiok",flush=True)\n'
                ' elif line=="isready": print("readyok",flush=True)\n'
                ' elif line.startswith("go "):\n'
                '  open("searches.txt","a").write("go\\n")\n'
                '  time.sleep(.5)\n'
                '  print("info multipv 1 score cp 100 pv 3c3d",flush=True)\n'
                '  print("info multipv 2 score cp 0 pv 8c8d",flush=True)\n'
                '  print("bestmove 3c3d",flush=True)\n'
                ' elif line=="quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'nodes': 100, 'multipv': 2, 'multipv_delta': 0}]))
            positions = []
            for move in ('1g1f', '2g2f', '3g3f', '4g4f', '5g5f', '6g6f', '7g7f', '9g9f'):
                board = cshogi.Board()
                board.push_usi(move)
                positions.append('sfen ' + board.sfen())
            (root / 'book/think_sfens.txt').write_text('\n'.join(positions) + '\n')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                def wait_for(needle):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        data = log.read_text()
                        if needle in data: return data
                        if process.poll() is not None: break
                        time.sleep(.02)
                    self.fail('missing ' + needle + ': ' + log.read_text())
                def send(text):
                    process.stdin.write(text + '\n')
                    process.stdin.flush()
                try:
                    wait_for('[CommandReady]')
                    send('sd 0 1 200 1 1000')
                    send('e')
                    wait_for('[TaskQueueStart]')
                    wait_for('[0] ')
                    send('job pause 1')
                    wait_for('[JobControl] job=1 state=pause')
                    time.sleep(1)
                    count = len((root / 'searches.txt').read_text().splitlines())
                    time.sleep(.8)
                    self.assertEqual(len((root / 'searches.txt').read_text().splitlines()), count)
                    send('job resume 1')
                    wait_for('[JobControl] job=1 state=resume')
                    deadline = time.monotonic() + 5
                    while len((root / 'searches.txt').read_text().splitlines()) <= count and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertGreater(len((root / 'searches.txt').read_text().splitlines()), count)
                    send('job delete 1')
                    wait_for('[JobControl] job=1 state=delete')
                    wait_for('[TaskQueueJobDone]')
                finally:
                    if process.poll() is None:
                        send('!')
                    process.wait(timeout=15)
                    process.stdin.close()

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='bookminer-cpp-test-')
        cls.root = Path(cls.temp.name)
        build = cls.root / 'build'
        binary = cls.root / 'bookminer'
        subprocess.run(['make', '-j2', f'BUILD_DIR={build}', f'TARGET={binary}'],
                       cwd=SOURCE, check=True, capture_output=True, text=True, timeout=180)
        cls.binary = binary
        objects = [build / 'book_store.o', build / 'config.o', build / 'usi_engine.o',
                   build / 'yaneuraou' / 'yaneuraou_core.lib']
        cls.executables = {}
        for name in ('peta_next', 'peta_settings', 'book_save'):
            exe = cls.root / f'{name}-test'
            subprocess.run(['g++', '-std=c++20', '-O2', '-ffunction-sections', '-fdata-sections',
                            '-I', str(SOURCE), str(ROOT / 'tests' / f'{name}_test.cpp'),
                            *map(str, objects), '-Wl,--gc-sections', '-pthread', '-o', str(exe)],
                           check=True, capture_output=True, text=True, timeout=120)
            cls.executables[name] = exe
        cls.config_exe = cls.root / 'config-test'
        subprocess.run(['g++', '-std=c++20', '-I', str(SOURCE), str(SOURCE / 'config.cpp'),
                        str(ROOT / 'tests' / 'config_test.cpp'), '-o', str(cls.config_exe)],
                       check=True, capture_output=True, text=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_overlapping_roots_and_rejoin(self):
        subprocess.run([str(self.executables['peta_next']), str(self.root / 'roots.txt')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_config_strings_and_threads(self):
        subprocess.run([str(self.config_exe), str(self.root / 'settings.json5')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_bad_command_does_not_stop_the_program(self):
        for command in ('sd invalid 1 200 0 400', 'sd 1x 1 200 0 400', 'e'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'book').mkdir()
                (root / 'book' / 'think_sfens.txt').write_text('startpos, game_ply_limit=200x\n')
                binary = root / 'bookminer'
                shutil.copy2(self.binary, binary)
                result = subprocess.run([str(binary), '--from_gui'], input=command + '\nq\n',
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Exception : command failed', result.stdout)
                if command == 'e':
                    self.assertIn('think_sfens.txt:1', result.stdout)
                self.assertIn('..save_book_backup has done', result.stdout)

    def test_enqueue_without_engines_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'book').mkdir()
            (root / 'book' / 'think_sfens.txt').write_text('startpos\n')
            binary = root / 'bookminer'
            shutil.copy2(self.binary, binary)
            result = subprocess.run([str(binary), '--from_gui'], input='e\nq\n',
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('no search engines are configured', result.stdout)
            self.assertNotIn('[TaskQueueStart]', result.stdout)
            self.assertIn('..save_book_backup has done', result.stdout)

    def test_explicit_work_directory_keeps_settings_and_books_together(self):
        with tempfile.TemporaryDirectory(prefix='book miner work ') as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            source = backup / 'book_miner-20261029000000_1.db'
            source.write_text('#YANEURAOU-DB2016 1.00\n'
                              'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
                              '7g7f none 0 1\n')
            result = subprocess.run([str(self.binary), '--from_gui', '--work-dir', str(root)],
                                    input='q\n', capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('done..1 positions.', result.stdout)
            self.assertEqual(len(list(backup.glob('book_miner-*.ybb'))), 1)
            self.assertTrue((root / 'log').is_dir())

    def test_peta_next_with_startpos_file_and_live_book(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            (root / 'book' / 'peta_start_sfens.txt').write_text('startpos\n')
            (backup / 'book_miner-20261114000000_1.db').write_text(
                '#YANEURAOU-DB2016 1.00\n'
                'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
                '7g7f none 0 1\n')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                                           stdin=subprocess.PIPE, stdout=output,
                                           stderr=subprocess.STDOUT, text=True)
                try:
                    process.stdin.write('sd 30 99999 200 6 400\npn 100 99999 200 6 400 1\n')
                    process.stdin.flush()
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if '[PetaNextDone]' in log.read_text():
                            break
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
                    else:
                        self.fail('peta next did not finish: ' + log.read_text())
                    self.assertIn('[PetaNextDone]', log.read_text())
                    self.assertEqual(log.read_text().count('read start sfens , path ='), 1)
                    lines = (root / 'book' / 'think_sfens.txt').read_text().splitlines()
                    self.assertEqual(len(lines), 1)
                    self.assertTrue(all(line.startswith('startpos moves 7g7f,') for line in lines), lines)
                finally:
                    if process.poll() is None:
                        process.stdin.write('q\n')
                        process.stdin.flush()
                    process.wait(timeout=30)
                    process.stdin.close()

    def test_reload_settings_changes_live_timer_and_start_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'book').mkdir()
            (root / 'settings').mkdir()
            (root / 'book' / '開始 局面.txt').write_text('startpos moves 7g7f\n', encoding='utf8')
            settings = root / 'settings' / 'book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 3600, max_book_ply: 200}', encoding='utf8')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    deadline = time.monotonic() + 30
                    while '[CommandReady]' not in log.read_text():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail('startup failed: ' + log.read_text())
                        time.sleep(0.05)
                    settings.write_text('{auto_save_interval_seconds: 1, max_book_ply: 99, '
                                        'peta_next_start_sfens_path: "book/開始 局面.txt", peta_shock_threads: 2}',
                                        encoding='utf8')
                    process.stdin.write('reload-settings\npn 100 100 99 6 400 1\n')
                    process.stdin.flush()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        contents = log.read_text()
                        if '[SettingsApplied]' in contents and '[PetaNextDone]' in contents and '[BackupDone]' in contents:
                            break
                        if process.poll() is not None:
                            break
                        time.sleep(0.05)
                    self.assertIn('[SettingsApplied]', contents)
                    self.assertIn('[BackupDone]', contents)
                    self.assertIn('max_book_ply = 99', contents)
                    self.assertIn('開始 局面.txt', contents)
                    self.assertIn('startpos moves 7g7f', (root / 'book' / 'think_sfens.txt').read_text())
                finally:
                    if process.poll() is None:
                        process.stdin.write('q\n')
                        process.stdin.flush()
                    process.wait(timeout=30)
                    process.stdin.close()

    def test_reload_unrelated_settings_preserves_auto_save_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            settings = root / 'settings' / 'book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 3600, max_book_ply: 200}')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
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

    def test_manual_peta_reuses_unchanged_auto_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            settings = root / 'settings' / 'book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 1}')
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    def wait_for(text):
                        deadline = time.monotonic() + 20
                        while text not in log.read_text():
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail('missing ' + text + ': ' + log.read_text())
                            time.sleep(0.02)
                    wait_for('[BackupDone]')
                    settings.write_text('{auto_save_interval_seconds: 3600}')
                    process.stdin.write('reload-settings\n')
                    process.stdin.flush()
                    wait_for('[SettingsApplied]')
                    process.stdin.write('p\n')
                    process.stdin.flush()
                    wait_for('peta shock failed')
                    self.assertIn('p command source book reused = ', log.read_text())
                finally:
                    if process.poll() is None:
                        process.stdin.write('!\n')
                        process.stdin.flush()
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_manual_peta_does_not_reuse_auto_backup_after_mining(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings').mkdir()
            (root / 'book').mkdir()
            (root / 'book/think_sfens.txt').write_text('startpos\n')
            settings = root / 'settings/book_miner_settings.json5'
            settings.write_text('{auto_save_interval_seconds: 1}')
            engine = root / 'engine'
            engine.write_text('#!/usr/bin/env python3\nimport sys\n'
                'for line in sys.stdin:\n'
                ' if line.strip()=="usi": print("usiok",flush=True)\n'
                ' elif line.strip()=="isready": print("readyok",flush=True)\n'
                ' elif line.startswith("go "):\n'
                '  print("info score cp 0 pv 7g7f",flush=True)\n'
                '  print("bestmove 7g7f",flush=True)\n'
                ' elif line.strip()=="quit": break\n')
            engine.chmod(0o755)
            (root / 'settings/engine_settings.json5').write_text(json.dumps([
                {'path': str(engine), 'nodes': 100, 'multipv': 1}]))
            log = root / 'output.log'
            with log.open('w') as output:
                process = subprocess.Popen([str(self.binary), '--from_gui', '--work-dir', str(root)],
                                           stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    def wait_for(text):
                        deadline = time.monotonic() + 20
                        while text not in log.read_text():
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail('missing ' + text + ': ' + log.read_text())
                            time.sleep(0.02)
                    wait_for('[BackupDone]')
                    settings.write_text('{auto_save_interval_seconds: 3600}')
                    process.stdin.write('reload-settings\nsd 30 99999 200 1 400\ne\n')
                    process.stdin.flush()
                    wait_for('[TaskQueueJobDone]')
                    process.stdin.write('p\n')
                    process.stdin.flush()
                    wait_for('peta shock failed')
                    contents = log.read_text()
                    self.assertNotIn('p command source book reused = ', contents)
                    self.assertIn('p command source book = ', contents)
                finally:
                    if process.poll() is None:
                        process.stdin.write('!\n')
                        process.stdin.flush()
                    process.wait(timeout=20)
                    process.stdin.close()

    def test_peta_shock_uses_configured_engine_and_threads(self):
        subprocess.run([str(self.executables['peta_settings']), str(self.root / 'peta')],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_book_save_and_reload(self):
        directory = self.root / 'book-save'
        directory.mkdir()
        subprocess.run([str(self.executables['book_save']), str(directory)],
                       check=True, capture_output=True, text=True, timeout=30)

    def test_worker_shutdown_and_deferred_search(self):
        if os.environ.get('BOOKMINER_TEST_BINARY'):
            return  # The same tests run directly in this test process.
        env = os.environ.copy()
        env['BOOKMINER_TEST_BINARY'] = str(self.binary)
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'),
                        '-p', 'test_shutdown.py', '-q'], env=env, check=True,
                       capture_output=True, text=True, timeout=90)

    def test_p_command_uses_shared_peta_settings(self):
        root = self.root / 'p-command'
        (root / 'book' / 'backup').mkdir(parents=True)
        (root / 'settings').mkdir()
        (root / 'engine directory').mkdir()
        binary = root / 'bookminer'
        shutil.copy2(self.binary, binary)
        search = root / 'search-engine'
        search.write_text('#!/usr/bin/env python3\nimport sys\n'
                          'for line in sys.stdin:\n'
                          ' line = line.strip()\n'
                          ' if line == "usi": print("usiok", flush=True)\n'
                          ' elif line == "isready": print("readyok", flush=True)\n'
                          ' elif line == "quit": break\n')
        search.chmod(0o755)
        material = root / 'engine directory' / 'custom engine'
        material.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                            'commands = sys.stdin.read()\n'
                            'pathlib.Path("peta-commands.txt").write_text(commands)\n'
                            'command = next(line for line in commands.splitlines() if line.startswith("makebook "))\n'
                            '(pathlib.Path("book") / command.split()[3]).write_text("#YANEURAOU-DB2016 1.00\\n")\n'
                            'print("Making a peta-shock book has been completed.")\n')
        material.chmod(0o755)
        (root / 'settings' / 'engine_settings.json5').write_text(
            '[{path: "' + str(search) + '", nodes: 100, multi: 1}]')
        (root / 'settings' / 'book_miner_settings.json5').write_text(
            "{peta_shock_engine_path: 'engine directory/custom engine', peta_shock_threads: 4}")
        (root / 'book' / 'backup' / 'book_miner-20261029000000_1.db').write_text(
            '#YANEURAOU-DB2016 1.00\n'
            'sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1\n'
            '7g7f none 0 1\n')
        log_path = root / 'run.log'
        with log_path.open('w') as output:
            process = subprocess.Popen([str(binary), '--from_gui'], stdin=subprocess.PIPE,
                                       stdout=output, stderr=subprocess.STDOUT, text=True)
            try:
                process.stdin.write('p\n')
                process.stdin.flush()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    contents = log_path.read_text()
                    if '[PetaCommandDone]' in contents or process.poll() is not None:
                        break
                    time.sleep(.05)
                self.assertIn('[PetaCommandDone]', contents)
                self.assertIn('..peta_shock makebook has done', contents)
            finally:
                if process.poll() is None:
                    process.stdin.write('!\n')
                    process.stdin.flush()
                process.wait(timeout=30)
                process.stdin.close()
            self.assertEqual(process.returncode, 0, contents)
        self.assertIn('[PetaBookPositions] positions=0', contents)
        self.assertIn(' threads 4\n', (root / 'peta-commands.txt').read_text())


if __name__ == '__main__':
    unittest.main()
