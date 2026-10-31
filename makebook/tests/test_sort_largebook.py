import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cshogi


ROOT = Path(__file__).resolve().parents[2]


class LargeBookSortTests(unittest.TestCase):
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
