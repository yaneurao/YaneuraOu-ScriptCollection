import sys
import tempfile
import unittest
from pathlib import Path

import cshogi
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from convert_from_apery import read_apery_entries


class AperyBookTests(unittest.TestCase):
    def test_truncated_record_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'book.bin'
            source.write_bytes(bytes(np.dtype(cshogi.BookEntry).itemsize) + b'\x01')
            with self.assertRaisesRegex(ValueError, 'truncated Apery book'):
                read_apery_entries(str(source))


if __name__ == '__main__':
    unittest.main()
