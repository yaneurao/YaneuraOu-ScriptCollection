from pathlib import Path
import sys
import tempfile
import unittest

import cshogi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
from YaneuraOuBookLib import DbBookProbe


class DbBookProbeTests(unittest.TestCase):
    def test_probe_stops_at_the_next_distinct_position(self):
        first = cshogi.Board()
        second = cshogi.Board()
        second.push_usi('7g7f')
        blocks = [(first.sfen().rsplit(' ', 1)[0], '7g7f none 10 1 1'),
                  (second.sfen().rsplit(' ', 1)[0], '3c3d none 20 1 1')]
        blocks.sort()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'book.db'
            path.write_text('#YANEURAOU-DB2016 1.00\n' + ''.join(
                f'sfen {sfen} 1\n{move}\n' for sfen, move in blocks), encoding='utf-8')
            with DbBookProbe(path) as probe:
                for sfen, line in blocks:
                    moves = probe.probe(sfen)
                    self.assertEqual([m.move for m in moves], [line.split()[0]])

    def test_probe_combines_adjacent_blocks_for_same_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'book.db'
            board = cshogi.Board().sfen().rsplit(' ', 1)[0]
            path.write_text('#YANEURAOU-DB2016 1.00\n'
                            f'sfen {board} 1\n7g7f none 10 1 2\n'
                            f'sfen {board} 3\n2g2f none 20 2 1\n7g7f none 30 3 4\n',
                            encoding='utf-8')
            with DbBookProbe(path) as probe:
                moves = probe.probe(cshogi.Board().sfen())
            self.assertIsNotNone(moves)
            self.assertEqual({m.move: (m.value, m.depth, m.move_count) for m in moves},
                             {'7g7f': (30, 3, 6), '2g2f': (20, 2, 1)})
