import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cshogi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
from YaneuraOuBookLib import read_yaneuraou_book, write_ybb_book


class LargeBookMergeTests(unittest.TestCase):
    def test_db_and_ybb_inputs_match_standard_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = cshogi.Board()
            left, right = root / 'left.db', root / 'right.db'
            left.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {board.sfen()}\n7g7f none 10 30 1\n')
            board.push_usi('7g7f')
            right.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {board.sfen()}\n3c3d none 5 20 1\n')
            for path in (left, right):
                write_ybb_book(read_yaneuraou_book(str(path)), path.with_suffix('.ybb'))
            for lhs in (left, left.with_suffix('.ybb')):
                for rhs in (right, right.with_suffix('.ybb')):
                    with self.subTest(lhs=lhs.suffix, rhs=rhs.suffix):
                        small, large = root / 'small.db', root / 'large.db'
                        for script, dest, extra in (
                            ('merge.py', small, []),
                            ('merge_largebook.py', large, ['--tmp-dir', str(root), '--max-open-runs', '2']),
                        ):
                            result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script),
                                                     str(lhs), str(rhs), str(dest), *extra],
                                                    capture_output=True, text=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(small.read_text(), large.read_text())

    def test_same_position_at_different_ply_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left, right = root / 'left.db', root / 'right.db'
            small, large = root / 'small.db', root / 'large.db'
            base = cshogi.Board().sfen().rsplit(' ', 1)[0]
            left.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {base} 1\n7g7f none 0 10 1\n')
            right.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {base} 3\n2g2f none 0 20 1\n')
            for script, dest, extra in (
                ('merge.py', small, []),
                ('merge_largebook.py', large, ['--tmp-dir', str(root)]),
            ):
                result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script),
                                         str(left), str(right), str(dest), *extra],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(small.read_text(), large.read_text())

    def test_duplicate_position_across_sort_runs_keeps_first_move_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left, right = root / 'left.db', root / 'right.db'
            small, large = root / 'small.db', root / 'large.db'
            board = cshogi.Board()
            start = board.sfen()
            board.push_usi('7g7f')
            next_sfen = board.sfen()
            left.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {start}\n'
                            f'7g7f none 100 30 1\nsfen {next_sfen}\n3c3d none 0 10 1\n'
                            f'sfen {start}\n2g2f none 0 1 50\n')
            right.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {start}\n2g2f none 10 20 5\n')
            for script, dest, extra in (
                ('merge.py', small, []),
                ('merge_largebook.py', large, ['--tmp-dir', str(root), '--chunk-positions', '1', '--max-open-runs', '2']),
            ):
                result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script),
                                         str(left), str(right), str(dest), *extra],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(small.read_text(), large.read_text())

    def test_original_first_move_depth_selects_same_book(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left, right = root / 'left.db', root / 'right.db'
            small, large = root / 'small.db', root / 'large.db'
            sfen = cshogi.Board().sfen()
            left.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {sfen}\n'
                            '7g7f none 100 30 1\n2g2f none 0 1 50\n')
            right.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {sfen}\n'
                             '2g2f none 10 20 5\n')
            for script, dest, extra in (
                ('merge.py', small, []),
                ('merge_largebook.py', large, ['--tmp-dir', str(root), '--chunk-positions', '1']),
            ):
                result = subprocess.run([sys.executable, str(ROOT / 'makebook' / script),
                                         str(left), str(right), str(dest), *extra],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(small.read_text(), large.read_text())
            self.assertIn('7g7f none 100 30 1', large.read_text())


if __name__ == '__main__':
    unittest.main()
