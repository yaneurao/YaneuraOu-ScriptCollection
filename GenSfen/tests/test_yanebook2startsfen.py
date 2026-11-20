import sys
import tempfile
import unittest
from pathlib import Path

import cshogi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'GenSfen'))
from yanebook2startsfen import retrieve_yanebook
from YaneShogiLib import flipped_sfen, trim_sfen_ply


class StartSfenExtractionTests(unittest.TestCase):
    def test_duplicate_book_blocks_contribute_all_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / 'input.db', Path(directory) / 'out.txt'
            root = cshogi.Board()
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {root.sfen()}\n7g7f none 0 1 1\n'
                              f'sfen {root.sfen()}\n2g2f none 0 1 1\n', encoding='utf-8')
            retrieve_yanebook(str(source), str(output))
            expected = set()
            for move in ('7g7f', '2g2f'):
                board = cshogi.Board()
                board.push_usi(move)
                expected.add(board.sfen())
            self.assertEqual(set(output.read_text().splitlines()), {s + ' ' for s in expected})

    def test_mirrored_book_position_is_not_a_frontier(self):
        root = cshogi.Board()
        child = cshogi.Board()
        child.push_usi('7g7f')
        child_key, child_ply = trim_sfen_ply(child.sfen())
        mirrored = flipped_sfen(child_key) + f' {child_ply}'
        blocks = [f'sfen {root.sfen()}\n7g7f none 0 1 1\n', f'sfen {mirrored}\n']
        for order in (blocks, blocks[::-1]):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / 'input.db', Path(directory) / 'out.txt'
                source.write_text('#YANEURAOU-DB2016 1.00\n' + ''.join(order), encoding='utf-8')
                retrieve_yanebook(str(source), str(output))
                self.assertEqual(output.read_text(), '')

    def test_only_leaf_positions_are_output_in_either_book_order(self):
        root = cshogi.Board()
        child = cshogi.Board()
        child.push_usi('7g7f')
        leaf = cshogi.Board(child.sfen())
        leaf.push_usi('3c3d')
        blocks = [f'sfen {root.sfen()}\n7g7f none 0 1 1\n',
                  f'sfen {child.sfen()}\n3c3d none 0 1 1\n']
        for order in (blocks, blocks[::-1]):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory) / 'input.db', Path(directory) / 'out.txt'
                source.write_text('#YANEURAOU-DB2016 1.00\n' + ''.join(order), encoding='utf-8')
                retrieve_yanebook(str(source), str(output))
                self.assertEqual(output.read_text().splitlines(), [leaf.sfen() + ' '])

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
