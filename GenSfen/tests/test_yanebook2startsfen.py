import sys
import tempfile
import unittest
from pathlib import Path

import cshogi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'GenSfen'))
from yanebook2startsfen import retrieve_yanebook


class StartSfenExtractionTests(unittest.TestCase):
    def test_comments_and_blank_lines_are_not_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.db', Path(directory) / 'output.txt'
            source.write_text(f'\ufeff#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              '# comment\n\n7g7f\tnone 0 1 1\n// comment\nnone none 0 0 0\n',
                              encoding='utf-8')
            retrieve_yanebook(str(source), str(output))
            board = cshogi.Board()
            board.push_usi('7g7f')
            self.assertEqual(output.read_text().strip(), board.sfen())

    def test_illegal_move_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.db', Path(directory) / 'output.txt'
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              '7g7f+ none 0 1 1\n', encoding='utf-8')
            output.write_text('old output')
            with self.assertRaisesRegex(ValueError, 'illegal book move'):
                retrieve_yanebook(str(source), str(output))
            self.assertEqual(output.read_text(), 'old output')


if __name__ == '__main__':
    unittest.main()
