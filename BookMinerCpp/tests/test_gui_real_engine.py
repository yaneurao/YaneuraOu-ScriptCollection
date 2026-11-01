"""Optional end-to-end GUI test with a real YaneuraOu Material engine."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BINARY = os.environ.get('BOOKMINER_TEST_BINARY')
MATERIAL = os.environ.get('BOOKMINER_TEST_MATERIAL_ENGINE')


@unittest.skipUnless(os.name != 'nt' and os.environ.get('DISPLAY') and BINARY and MATERIAL,
                     'requires DISPLAY, BOOKMINER_TEST_BINARY and BOOKMINER_TEST_MATERIAL_ENGINE')
class RealGuiEngineTest(unittest.TestCase):
    def test_configured_executable_search_save_and_reload(self):
        spec = importlib.util.spec_from_file_location('bookminer_real_gui', ROOT / 'BookMiner/BookMiner-gui.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for case in ('absolute', 'relative', 'default'):
            with self.subTest(case=case), tempfile.TemporaryDirectory(prefix='bookminer real ') as directory:
                root = Path(directory)
                work = root / 'BookMiner 作業 folder'
                (work / 'settings').mkdir(parents=True)
                (work / 'book').mkdir()
                engine = work / 'engines' / 'material'
                engine.parent.mkdir()
                shutil.copy2(MATERIAL, engine)
                (engine.parent / 'book').mkdir()
                (engine.parent / 'book/standard_book.db').write_text('#YANEURAOU-DB2016 1.00\n')
                executable = work / 'BookMinerCpp.exe' if case == 'default' else root / 'outputs' / 'custom binary'
                executable.parent.mkdir(exist_ok=True)
                shutil.copy2(BINARY, executable)
                settings = {} if case == 'default' else {'bookminer_cpp_path': str(executable) if case == 'absolute'
                                                        else '../outputs/custom binary'}
                settings_path = work / 'settings/book_miner_settings.json5'
                settings_path.write_text(json.dumps(settings))
                (work / 'settings/engine_settings.json5').write_text(json.dumps([
                    {'path': str(engine), 'nodes': 1000, 'multipv': 2, 'multipv_delta': 0,
                     'multi': 2 if case == 'absolute' else 1}]))
                (work / 'book/think_sfens.txt').write_text(
                    'startpos moves 7g7f\n' + ('startpos moves 2g2f\n' if case == 'absolute' else ''))
                chosen, _ = module.configured_bookminer_cpp_path(work, settings_path)
                self.assertEqual(chosen.resolve(), executable.resolve())
                with patch.multiple(module, BASE_DIR=work, BOOK_MINER_SETTINGS_PATH=settings_path,
                                    GUI_SETTINGS_PATH=work / 'gui.pickle',
                                    THINK_SFENS_PATH=work / 'book/think_sfens.txt',
                                    AUTO_THINK_SFENS_PATH=work / 'book/think_sfens-tmp.txt'):
                    window = tk.Tk()
                    window.withdraw()
                    gui = module.BookMinerGui(window, use_cpp=True, cpp_executable=chosen)
                    logs = []
                    append = gui._append_log
                    def capture(key, text):
                        logs.append(text)
                        append(key, text)
                    gui._append_log = capture
                    def wait_for(label, predicate):
                        deadline = time.monotonic() + 35
                        while time.monotonic() < deadline:
                            window.update()
                            if predicate():
                                return
                            if gui.process is not None and gui.process.poll() is not None:
                                break
                            time.sleep(.01)
                        self.fail(f'{case}: {label} failed:\n' + ''.join(logs)[-6000:])
                    try:
                        wait_for('startup', lambda: gui.command_ready)
                        self.assertTrue(gui.send_command('sd 30 5 200 2 30000'))
                        self.assertTrue(gui.send_command('e'))
                        wait_for('mining', lambda: '[TaskQueueDone]' in ''.join(logs))
                        self.assertIn('failed=0', ''.join(logs))
                        self.assertTrue(gui.send_command('w'))
                        wait_for('backup', lambda: '[ManualBackupDone]' in ''.join(logs))
                        files = list((work / 'book/backup').glob('book_miner-*.ybb'))
                        self.assertTrue(files)
                        data = files[-1].read_bytes()
                        count, flags = struct.unpack_from('<QQ', data, 16)
                        self.assertGreaterEqual(count, 2)
                        move_base, record_size = 32 + 44 * count, 6 if flags & 1 else 4
                        for index in range(count):
                            offset, _, size = struct.unpack_from('<QHH', data, 32 + 44 * index + 32)
                            moves = [struct.unpack_from('<Hh', data, move_base + offset + j * record_size)
                                     for j in range(size)]
                            self.assertTrue(moves)
                            self.assertEqual([score for _, score in moves], sorted((score for _, score in moves), reverse=True))
                            self.assertEqual(len({move for move, _ in moves}), len(moves))
                        self.assertTrue(gui.send_command('q'))
                        process = gui.process
                        wait_for('exit', lambda: process.poll() is not None)
                        self.assertEqual(process.returncode, 0)
                        command = [str(executable), '--from_gui'] + ([] if case == 'default' else ['--work-dir', str(work)])
                        restarted = subprocess.run(command, input='!\n', cwd=work, capture_output=True, text=True, timeout=30)
                        self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
                        self.assertIn(f'done..{count} positions.', restarted.stdout)
                        if case != 'default':
                            self.assertFalse((executable.parent / 'book').exists())
                    finally:
                        if gui.process is not None and gui.process.poll() is None:
                            gui.process.kill()
                            gui.process.wait(timeout=5)
                        for timer in window.tk.call('after', 'info'):
                            window.tk.call('after', 'cancel', timer)
                        window.destroy()


if __name__ == '__main__':
    unittest.main()
