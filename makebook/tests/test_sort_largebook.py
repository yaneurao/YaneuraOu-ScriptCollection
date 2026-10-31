import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cshogi


ROOT = Path(__file__).resolve().parents[2]


class LargeBookSortTests(unittest.TestCase):
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
