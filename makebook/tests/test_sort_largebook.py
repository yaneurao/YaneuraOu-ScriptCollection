import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cshogi


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'makebook'))
import sort_largebook
import merge_largebook
import YaneuraOuBookLib as booklib


class LargeBookSortTests(unittest.TestCase):
    def test_small_book_writer_keeps_previous_output_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output.db'
            output.write_bytes(b'previous')
            book = {cshogi.Board().sfen(): [booklib.BookMove('7g7f', 'none', 0, 1, 1)]}
            with patch.object(booklib, 'write_yaneuraou_book_block', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    booklib.write_yaneuraou_db_book(book, str(output))
            self.assertEqual(output.read_bytes(), b'previous')
            self.assertFalse(list(root.glob('.output.db.*.tmp')))

    def test_failed_sort_and_merge_preserve_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'input.db', root / 'output.db'
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              '7g7f none 0 10 1\n')
            options = dict(tmp_dir=str(root), chunk_positions=10, chunk_bytes=1024,
                           max_open_runs=2, ignore_book_ply=False, keep_temp=False)
            def fail_sort(runs, destination, *args):
                Path(destination).write_bytes(b'partial')
                raise OSError('disk full')
            def fail_merge(first, second, destination, mode):
                Path(destination).write_bytes(b'partial')
                raise OSError('disk full')
            for action in ('sort', 'merge'):
                with self.subTest(action=action):
                    output.write_bytes(b'existing')
                    if action == 'sort':
                        with patch.object(sort_largebook, 'merge_runs_bounded', side_effect=fail_sort):
                            with self.assertRaisesRegex(OSError, 'disk full'):
                                sort_largebook.sort_largebook(str(source), str(output), **options)
                    else:
                        with patch.object(merge_largebook, 'run_sort_largebook'), \
                             patch.object(merge_largebook, 'merge_sorted_books', side_effect=fail_merge):
                            with self.assertRaisesRegex(OSError, 'disk full'):
                                merge_largebook.merge_largebook(str(source), str(source), str(output), mode='', **options)
                    self.assertEqual(output.read_bytes(), b'existing')
                    self.assertFalse(list(root.glob('.output.db.*.tmp')))

    def test_tab_separated_moves_keep_values_when_sorting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.db'
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              '7g7f\t3c3d\t-120\t31\t42\n', encoding='utf-8')
            for script in ('sort.py', 'sort_largebook.py'):
                output = root / (script + '.db')
                result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script), str(source), str(output),
                                         *(['--tmp-dir', str(root)] if script == 'sort_largebook.py' else [])],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('7g7f 3c3d -120 31 42', output.read_text())

    def test_many_runs_merge_in_multiple_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, small, large = folder / 'input.db', folder / 'small.db', folder / 'large.db'
            board = cshogi.Board()
            lines = ['#YANEURAOU-DB2016 1.00']
            for move in ('7g7f', '3c3d', '2g2f', '8c8d', '2f2e', '8d8e'):
                lines.extend([f'sfen {board.sfen()}', f'{move} none 0 10 1'])
                board.push_usi(move)
            source.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            for script, output, extra in (
                ('sort.py', small, []),
                ('sort_largebook.py', large, ['--chunk-positions', '1', '--max-open-runs', '2',
                                               '--tmp-dir', str(folder)]),
            ):
                result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script),
                                         str(source), str(output), *extra], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(small.read_text(), large.read_text())

    def test_empty_earlier_ply_does_not_hide_later_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "input.db"
            small = folder / "small.db"
            large = folder / "large.db"
            base = cshogi.Board().sfen().rsplit(" ", 1)[0]
            source.write_text(
                f"#YANEURAOU-DB2016 1.00\nsfen {base} 1\n"
                f"sfen {base} 3\n7g7f none 42 10 1\n", encoding="utf-8"
            )
            for script, output, extra in (
                ("sort.py", small, []),
                ("sort_largebook.py", large, ["--chunk-positions", "1", "--tmp-dir", str(folder)]),
            ):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "makebook" / script), str(source), str(output), *extra],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(small.read_text(), large.read_text())
            self.assertIn("7g7f none 42 10 1", large.read_text())


if __name__ == "__main__":
    unittest.main()
