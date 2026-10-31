import sys
import tempfile
import unittest
from pathlib import Path

import cshogi
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
sys.path.insert(0, str(ROOT / 'teacher'))
from TeacherConvertLib import convert_hcpe3_to_hcpe_file, convert_hcpe3_to_psv_file
from TeacherFormatLib import HCPE3_HEADER, MOVE_INFO
from concat_hcpe3_round_robin import GameRecord, iter_hcpe3_position_records


def make_game(first_move):
    header = np.zeros(1, dtype=HCPE3_HEADER)
    board = cshogi.Board()
    board.to_hcp(header['hcp'])
    header['moveNum'] = 2
    moves = np.zeros(2, dtype=MOVE_INFO)
    moves[0]['selectedMove16'] = first_move
    return header.tobytes() + moves.tobytes()


class Hcpe3MoveValidationTests(unittest.TestCase):
    def test_illegal_move_is_rejected_before_replaying_next_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.hcpe3'
            data = make_game(0)
            path.write_bytes(data)
            for converter in (convert_hcpe3_to_hcpe_file, convert_hcpe3_to_psv_file):
                with self.subTest(converter=converter.__name__), tempfile.TemporaryFile() as output:
                    with self.assertRaisesRegex(ValueError, 'illegal selectedMove16'):
                        converter(path, output, no_progress=True)
            record = GameRecord(1, path.parent, path, 1, 1, data)
            with self.assertRaisesRegex(RuntimeError, 'illegal selectedMove16'):
                list(iter_hcpe3_position_records(record))

    def test_legal_move_can_be_replayed(self):
        board = cshogi.Board()
        move = cshogi.move16(board.move_from_usi('7g7f'))
        data = make_game(move)
        record = GameRecord(1, Path('.'), Path('game.hcpe3'), 1, 1, data)
        self.assertEqual(len(list(iter_hcpe3_position_records(record))), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'good.hcpe3'
            path.write_bytes(data)
            for converter in (convert_hcpe3_to_hcpe_file, convert_hcpe3_to_psv_file):
                with tempfile.TemporaryFile() as output:
                    self.assertEqual(converter(path, output, no_progress=True).positions, 2)


if __name__ == '__main__':
    unittest.main()
