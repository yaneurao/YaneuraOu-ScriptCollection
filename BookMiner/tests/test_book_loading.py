import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_loading_test', MODULE_PATH)
bookminer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bookminer
spec.loader.exec_module(bookminer)


class BookLoadingTests(unittest.TestCase):
    def test_flipped_duplicate_keeps_shortest_ply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flipped.db'
            flipped = bookminer.flipped_sfen(bookminer.SFEN_START)
            move = bookminer.flipped_move('2g2f')
            path.write_text('#YANEURAOU-DB2016 1.00\n'
                            f'sfen {bookminer.SFEN_START} 20\n7g7f none 10 0\n'
                            f'sfen {flipped} 5\n{move} none 5 0\n')
            book = bookminer.Book()
            bookminer.read_yaneuraou_book(book, str(path))
            position = book.body[bookminer.SFEN_START]
            self.assertEqual(position.ply, 5)
            self.assertEqual({m.move for m in position.moveinfos}, {'7g7f', '2g2f'})

    def test_duplicate_position_blocks_merge_moves_and_shortest_ply(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.db'
            path.write_text('#YANEURAOU-DB2016 1.00\n'
                            f'sfen {bookminer.SFEN_START} 20\n7g7f none 10 0\n'
                            f'sfen {bookminer.SFEN_START} 5\n2g2f none 5 0\n7g7f none 30 7\n')
            for reader in (bookminer.read_bookminer_backup, bookminer.read_yaneuraou_book):
                with self.subTest(reader=reader.__name__):
                    book = bookminer.Book()
                    reader(book, str(path))
                    position = book.body[bookminer.SFEN_START]
                    self.assertEqual(position.ply, 5)
                    self.assertEqual({m.move: (m.eval, m.depth) for m in position.moveinfos},
                                     {'7g7f': (30, 7), '2g2f': (5, 0)})
                    self.assertEqual(len(position.moveinfos), 2)


if __name__ == '__main__':
    unittest.main()
