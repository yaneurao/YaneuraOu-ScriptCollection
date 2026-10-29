import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_test_module', MODULE_PATH)
bookminer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bookminer
spec.loader.exec_module(bookminer)


class PetaSettingsTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json5'
            path.write_text('{}', encoding='utf8')
            settings = bookminer.load_book_miner_settings(str(path))
            self.assertEqual(settings.peta_shock_engine_path, 'YO-MATERIAL.exe')
            self.assertEqual(settings.peta_shock_threads, 1)
            path.write_text('{peta_shock_engine_path: "engines/custom.exe", peta_shock_threads: 4}', encoding='utf8')
            settings = bookminer.load_book_miner_settings(str(path))
            self.assertEqual((settings.peta_shock_engine_path, settings.peta_shock_threads), ('engines/custom.exe', 4))
            for value in ['0', '-1', 'true', '1.5', '4294967296']:
                path.write_text('{peta_shock_threads: ' + value + '}', encoding='utf8')
                with self.subTest(value=value), self.assertRaisesRegex(Exception, 'peta_shock_threads'):
                    bookminer.load_book_miner_settings(str(path))
            path.write_text('{peta_shock_engine_path: " "}', encoding='utf8')
            with self.assertRaisesRegex(Exception, 'peta_shock_engine_path'):
                bookminer.load_book_miner_settings(str(path))

    @unittest.skipIf(os.name == 'nt', 'test fixture uses a Unix executable script')
    def test_real_subprocess_path_and_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            source = backup / 'book_miner-20261029000000_1.db'
            source.write_text('source', encoding='utf8')
            engine = root / 'engine directory' / 'custom engine'
            engine.parent.mkdir()
            engine.write_text('#!/usr/bin/env python3\nimport pathlib, sys\ncommands = sys.stdin.read()\npathlib.Path("commands.txt").write_text(commands)\ncommand = next(line for line in commands.splitlines() if line.startswith("makebook "))\noutput = command.split()[3]\n(pathlib.Path("book") / output).write_text("result")\n', encoding='utf8')
            engine.chmod(0o755)
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.object(bookminer, '__file__', str(root / 'BookMiner.py')):
                    settings = bookminer.BookMinerSettings(peta_shock_engine_path='engine directory/custom engine', peta_shock_threads=4)
                    output = bookminer.run_peta_shock_makebook(str(source), settings)
                    self.assertEqual(Path(output).read_text(), 'result')
                    self.assertIn(' threads 4\n', (root / 'commands.txt').read_text())
                    settings.peta_shock_engine_path = str(engine)
                    settings.peta_shock_threads = 1
                    bookminer.run_peta_shock_makebook(str(source), settings)
                    self.assertNotIn(' threads ', (root / 'commands.txt').read_text())
            finally:
                os.chdir(old_cwd)

    def test_p_and_pl_forward_settings(self):
        settings = bookminer.BookMinerSettings(peta_shock_threads=3)
        book = bookminer.Book()
        with patch.object(bookminer, 'write_to_yaneuraou_book', return_value='book/backup/source.db'), \
             patch.object(bookminer, 'make_and_read_peta_book') as make:
            bookminer.write_and_read_peta_book(book, settings=settings)
            make.assert_called_once_with('book/backup/source.db', settings)
        with patch.object(bookminer, 'resolve_peta_source_book_path', return_value='source.db'), \
             patch.object(bookminer, 'run_peta_shock_makebook', return_value='output.db') as run, \
             patch.object(bookminer, 'read_peta_book'):
            bookminer.make_and_read_peta_book(None, settings)
            run.assert_called_once_with('source.db', settings)


if __name__ == '__main__':
    unittest.main()
