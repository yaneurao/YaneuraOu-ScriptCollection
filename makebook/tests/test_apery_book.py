import sys
import tempfile
import unittest
from pathlib import Path

import cshogi
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convert_from_apery import read_apery_entries
from convert_to_apery import convert_to_apery, usi_to_apery_move16


class AperyBookTests(unittest.TestCase):
    def test_invalid_move_is_rejected_without_overwriting_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'book.db'
            output = Path(directory) / 'book.bin'
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              'bad-move none 12 1 1\n')
            output.write_bytes(b'old data')
            with self.assertRaisesRegex(ValueError, 'illegal or non-board move'):
                convert_to_apery(str(source), str(output))
            self.assertEqual(output.read_bytes(), b'old data')

    def test_same_board_at_different_plies_uses_earliest_ply(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'book.db'
            output = Path(directory) / 'book.bin'
            base = cshogi.Board().sfen().rsplit(' ', 1)[0]
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {base} 3\n2g2f none 0 1 10\n'
                              f'sfen {base} 1\n7g7f none 12 1 1\n')
            convert_to_apery(str(source), str(output))
            entries = np.fromfile(output, dtype=cshogi.BookEntry)
            self.assertEqual(len(entries), 1)
            self.assertEqual(int(entries[0]['fromToPro']), usi_to_apery_move16('7g7f'))
            self.assertEqual(int(entries[0]['score']), 12)

    def test_truncated_record_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'book.bin'
            source.write_bytes(bytes(np.dtype(cshogi.BookEntry).itemsize) + b'\x01')
            with self.assertRaisesRegex(ValueError, 'truncated Apery book'):
                read_apery_entries(str(source))


if __name__ == '__main__':
    unittest.main()
