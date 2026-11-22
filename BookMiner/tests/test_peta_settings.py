import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_test_module', MODULE_PATH)
bookminer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bookminer
spec.loader.exec_module(bookminer)


class PetaSettingsTests(unittest.TestCase):
    def test_peta_backup_resets_timer_only_after_successful_save(self):
        reset = Mock()
        book = bookminer.Book()
        with patch.object(bookminer, 'write_to_yaneuraou_book', side_effect=bookminer.PetaShockCancelled):
            with self.assertRaises(bookminer.PetaShockCancelled):
                bookminer.write_and_read_peta_book(book, reset, cancel=Event())
        reset.assert_not_called()
        with patch.object(bookminer, 'write_to_yaneuraou_book', return_value='saved.db'), \
             patch.object(bookminer, 'make_and_read_peta_book', side_effect=bookminer.PetaShockCancelled):
            with self.assertRaises(bookminer.PetaShockCancelled):
                bookminer.write_and_read_peta_book(book, reset, cancel=Event())
        reset.assert_called_once()

    def test_backup_does_not_overwrite_existing_same_second_file(self):
        book = bookminer.Book()
        book.body[bookminer.SFEN_START] = bookminer.PositionInfo([bookminer.MoveInfo('7g7f', 10)], 1)
        with tempfile.TemporaryDirectory() as directory:
            previous = Path(directory) / 'book_miner-20261029000000_1.db'
            previous.write_text('previous')
            with patch.object(bookminer, 'make_time_stamp', side_effect=['20261029000000', '20261029000001']), \
                 patch.object(bookminer.time, 'sleep'):
                saved = Path(bookminer.save_book_backup(book, directory))
            self.assertEqual(previous.read_text(), 'previous')
            self.assertEqual(saved.name, 'book_miner-20261029000001_1.db')
            self.assertIn('7g7f', saved.read_text())

    def test_cancel_during_manual_save_preserves_existing_file(self):
        book = bookminer.Book()
        book.body[bookminer.SFEN_START] = bookminer.PositionInfo([bookminer.MoveInfo('7g7f', 0)], 1)
        cancel = Event()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'book.db'
            target.write_text('previous')
            snapshot = bookminer.snapshot_book_position_for_write
            def cancel_after_snapshot(*args):
                result = snapshot(*args)
                cancel.set()
                return result
            with patch.object(bookminer, 'snapshot_book_position_for_write', side_effect=cancel_after_snapshot):
                with self.assertRaises(bookminer.PetaShockCancelled):
                    bookminer.write_yaneuraou_book_records(book, str(target), None, cancel=cancel)
            self.assertEqual(target.read_text(), 'previous')
            self.assertEqual(list(target.parent.glob('.book-*.db')), [])

    def test_uppercase_source_uses_temporary_hard_link_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                for extension in ('.DB', '.YBB'):
                    with self.subTest(extension=extension):
                        source = backup / ('book_miner-20261029000000_1' + extension)
                        source.write_text('source')
                        aliases = []
                        def run(original, settings, cancel, *, engine_source_path):
                            alias = Path(engine_source_path)
                            aliases.append(alias)
                            self.assertEqual(original, str(source))
                            self.assertEqual(alias.suffix, extension.lower())
                            self.assertTrue(os.path.samefile(source, alias))
                            raise ValueError('engine failed')
                        with patch.object(bookminer, '_run_peta_shock_makebook', side_effect=run):
                            with self.assertRaisesRegex(ValueError, 'engine failed'):
                                bookminer.run_peta_shock_makebook(str(source))
                        self.assertFalse(aliases[0].exists())
                        self.assertEqual(source.read_text(), 'source')
                        self.assertEqual(bookminer.parse_regular_book_backup_name(str(source)), ('20261029000000', 1))
            finally:
                os.chdir(old_cwd)

    def test_engine_input_failure_stops_child_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / 'book' / 'backup'
            backup.mkdir(parents=True)
            source = backup / 'book_miner-20261029000000_1.db'
            source.write_text('source')
            engine = root / 'engine'
            engine.touch()
            destination = backup / 'peta_book-20261029000000_1.db'
            destination.write_text('previous')
            temporary = []
            process = Mock()
            process.stdout = io.StringIO('')
            process.stdin = Mock()
            process.stdin.write.side_effect = BrokenPipeError('closed')
            process.poll.return_value = None
            def start(*_args, **_kwargs):
                path = next(backup.glob('peta-tmp-*')) / 'result.db'
                path.write_text('partial')
                temporary.append(path)
                return process
            settings = bookminer.BookMinerSettings(peta_shock_engine_path=str(engine))
            old_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.object(bookminer, '__file__', str(root / 'BookMiner.py')), \
                     patch.object(bookminer.subprocess, 'Popen', side_effect=start):
                    with self.assertRaises(BrokenPipeError):
                        bookminer.run_peta_shock_makebook(str(source), settings)
            finally:
                os.chdir(old_cwd)
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
            self.assertFalse(temporary[0].exists())
            self.assertEqual(destination.read_text(), 'previous')

    def test_cancel_interrupts_wait_for_another_backup(self):
        cancel, stopped = Event(), Event()
        def save():
            try:
                bookminer.save_book_backup(bookminer.Book(), 'unused', cancel=cancel)
            except bookminer.PetaShockCancelled:
                stopped.set()
        bookminer.BOOK_SAVE_LOCK.acquire()
        try:
            thread = Thread(target=save, daemon=True)
            thread.start()
            cancel.set()
            self.assertTrue(stopped.wait(2))
            thread.join(timeout=2)
        finally:
            bookminer.BOOK_SAVE_LOCK.release()

    def test_cancelled_peta_read_preserves_previous_book(self):
        previous = bookminer.Book()
        previous.body['previous'] = object()
        cancel = Event()
        original = bookminer.peta_book
        bookminer.peta_book = previous
        try:
            def cancelled_read(book, _path, _label, _cancel):
                book.body['partial'] = object()
                cancel.set()
            with patch.object(bookminer, 'resolve_peta_book_path', return_value='peta.db'), \
                 patch.object(bookminer, 'read_regular_book_fast', side_effect=cancelled_read):
                with self.assertRaises(bookminer.PetaShockCancelled):
                    bookminer.read_peta_book('peta.db', cancel)
            self.assertIs(bookminer.peta_book, previous)
            self.assertIn('previous', bookminer.peta_book.body)
        finally:
            bookminer.peta_book = original

    def test_failed_peta_read_clears_partial_book_and_stale_probe(self):
        old_book = bookminer.peta_book
        try:
            def failed_read(book, _path, _label):
                book.body['partial'] = object()
                raise ValueError('corrupt')
            with patch.object(bookminer, 'resolve_peta_book_path', return_value='broken.ybb'), \
                 patch.object(bookminer, 'read_regular_book_fast', side_effect=failed_read):
                with self.assertRaisesRegex(ValueError, 'corrupt'):
                    bookminer.read_peta_book('broken.ybb')
            self.assertFalse(bookminer.peta_book.body)
        finally:
            bookminer.peta_book = old_book

    def test_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json5'
            path.write_text('{}', encoding='utf8')
            settings = bookminer.load_book_miner_settings(str(path))
            self.assertEqual(settings.peta_shock_engine_path, 'YO-MATERIAL.exe')
            self.assertEqual(settings.peta_shock_threads, 4)
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
            legacy_temporary = backup / 'tmp-peta_book-20261029000000_1.db'
            legacy_temporary.write_text('unrelated')
            engine = root / 'engine directory' / 'custom engine'
            engine.parent.mkdir()
            engine.write_text('#!/usr/bin/env python3\nimport pathlib, sys\ncommands = sys.stdin.read()\npathlib.Path("commands.txt").write_text(commands)\ncommand = next(line for line in commands.splitlines() if line.startswith("makebook "))\noutput = command.split()[3]\n(pathlib.Path("book") / output).write_text("result")\nif pathlib.Path("simulate-error").exists(): print("info string Error! : write failed")\nelif not pathlib.Path("simulate-incomplete").exists(): print("Making a peta-shock book has been completed.")\n', encoding='utf8')
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
                    for marker in ('simulate-error', 'simulate-incomplete'):
                        (root / marker).touch()
                        with self.assertRaisesRegex(Exception, 'failed or did not report completion'):
                            bookminer.run_peta_shock_makebook(str(source), settings)
                        self.assertEqual(Path(output).read_text(), 'result')
                        self.assertEqual(list(backup.glob('peta-tmp-*')), [])
                        self.assertEqual(legacy_temporary.read_text(), 'unrelated')
                        (root / marker).unlink()
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
