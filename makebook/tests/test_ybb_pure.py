import random
import sys
import tempfile
import unittest
from pathlib import Path

import cshogi
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ybb_db_converter_pure as pure
import convert_db_to_ybb as native_to_ybb
import convert_ybb_to_db as native_to_db
from YaneuraOuBookLib import read_yaneuraou_book


class PureYbbTests(unittest.TestCase):
    def test_sfen_invalid_zero_counts_and_trailing_tokens_are_rejected(self):
        start = cshogi.Board().sfen()
        board, turn, _, ply = start.split()
        for sfen in (f'{board} {turn} 0P {ply}',
                     f'0{board} {turn} - {ply}', f'{start} trailing'):
            with self.subTest(sfen=sfen), self.assertRaises(ValueError):
                pure.pack_sfen_text(sfen)

    def test_duplicate_move_keeps_one_entry_with_latest_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.db'
            source.write_text(f'#YANEURAOU-DB2016 1.00\nsfen {cshogi.Board().sfen()}\n'
                              '7g7f none 1 10 1\n2g2f none 20 20 1\n7g7f none 30 40 1\n')
            for module in (pure, native_to_ybb):
                work = root / module.__name__
                work.mkdir()
                output = root / f'{module.__name__}.ybb'
                module.convert_db_to_ybb(source, output, work, 10, 1_000_000, 2, True)
                moves = next(iter(read_yaneuraou_book(str(output)).values()))
                self.assertEqual([(move.move, move.value, move.depth) for move in moves],
                                 [('7g7f', 30, 40), ('2g2f', 20, 20)])

    def test_full_conversion_matches_cshogi_with_multiple_runs(self):
        rng = random.Random(85)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.db'
            board = cshogi.Board()
            with source.open('w', encoding='utf-8') as out:
                out.write('#YANEURAOU-DB2016 1.00\n')
                for _ in range(60):
                    moves = list(board.legal_moves)
                    if not moves:
                        break
                    out.write(f'sfen {board.sfen()}\n')
                    for move in rng.sample(moves, min(3, len(moves))):
                        out.write(f'{cshogi.move_to_usi(move)} none {rng.randrange(-500, 500)} 20 1\n')
                    board.push(rng.choice(moves))
            work1, work2 = root / 'work1', root / 'work2'
            work1.mkdir()
            work2.mkdir()
            native, python = root / 'native.ybb', root / 'python.ybb'
            args = (7, 1_000_000, 2, True)
            native_to_ybb.convert_db_to_ybb(source, native, work1, *args)
            pure.convert_db_to_ybb(source, python, work2, *args)
            self.assertEqual(native.read_bytes(), python.read_bytes())
            db1, db2 = root / 'native.db', root / 'python.db'
            native_to_db.convert_ybb_to_db(native, db1, work1, 7, 1_000_000, 2)
            pure.convert_ybb_to_db(python, db2, work2, 7, 1_000_000, 2)
            self.assertEqual(db1.read_bytes(), db2.read_bytes())

    def test_duplicate_kings_are_not_silently_discarded(self):
        sfen = '9/9/9/9/4K4/9/9/9/4K4 b - 1'
        with self.assertRaisesRegex(ValueError, 'duplicate king'):
            pure.pack_sfen_text(sfen)

    def test_packed_positions_and_moves_match_cshogi(self):
        rng = random.Random(123)
        board = cshogi.Board()
        packed = np.empty(1, dtype=cshogi.PackedSfen)
        for _ in range(300):
            sfen = board.sfen()
            board.to_psfen(packed)
            expected = packed[0]['sfen'].tobytes()
            self.assertEqual(pure.pack_sfen_text(sfen), expected)
            self.assertEqual(pure.unpack_sfen_text(expected, int(sfen.split()[-1])), sfen)
            moves = list(board.legal_moves)
            if not moves:
                break
            for move in rng.sample(moves, min(8, len(moves))):
                usi = cshogi.move_to_usi(move)
                encoded = int(cshogi.move16_to_psv(cshogi.move16(move)))
                self.assertEqual(pure.usi_to_move16(usi), encoded)
                self.assertEqual(pure.move16_to_usi(encoded), usi)
            board.push(rng.choice(moves))

    def test_drop_with_trailing_characters_is_invalid(self):
        for move in ('P*5e+', 'P*5efoo'):
            with self.subTest(move=move), self.assertRaises(ValueError):
                pure.usi_to_move16(move)


if __name__ == '__main__':
    unittest.main()
