from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cshogi

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
from YaneShogiLib import flipped_sfen, flipped_move
from YaneuraOuBookLib import read_yaneuraou_book, write_ybb_book

SCRIPT = ROOT / 'makebook' / 'book_with_master_to_think_sfens.py'


def position(moves):
    board = cshogi.Board()
    for move in moves:
        board.push_usi(move)
    return board.sfen()


def write_db(path, entries):
    with path.open('w', encoding='utf8') as f:
        f.write('#YANEURAOU-DB2016 1.00\n')
        for sfen, moves in entries:
            f.write('sfen ' + sfen + '\n')
            for move in moves:
                f.write(f'{move} none 0 0 1\n')


def to_ybb(path):
    target = path.with_suffix('.ybb')
    write_ybb_book(read_yaneuraou_book(str(path)), target)
    return target


def run(master, source, output):
    return subprocess.run([sys.executable, str(SCRIPT), str(master), str(source), str(output),
                           '--progress-interval', '0'], text=True, capture_output=True)


class BookWithMasterTest(unittest.TestCase):
    def test_formats_and_master_connection_and_unmatched(self):
        with tempfile.TemporaryDirectory(prefix='master book ') as folder:
            root = Path(folder)
            master = root / 'master.db'
            source = root / 'input.db'
            write_db(master, [(position([]), ['7g7f'])])
            write_db(source, [(position(['7g7f']), ['3c3d']),
                              (position(['7g7f', '3c3d']), ['2g2f']),
                              (position(['2g2f']), ['8c8d']),
                              (position(['2g2f', '8c8d']), ['7g7f'])])
            master_ybb, source_ybb = to_ybb(master), to_ybb(source)
            for m in (master, master_ybb):
                for s in (source, source_ybb):
                    with self.subTest(master=m.suffix, source=s.suffix):
                        output = root / 'result.txt'
                        result = run(m, s, output)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        lines = output.read_text(encoding='utf8').splitlines()
                        self.assertEqual(len(lines), 2, result.stdout)
                        self.assertIn('startpos moves 7g7f 3c3d', lines)
                        self.assertTrue(any(line.startswith('sfen ') and line.endswith(' moves 8c8d') for line in lines), lines)
                        self.assertIn('roots matched=1, unmatched=1', result.stdout)

    def test_flipped_root_converts_suffix_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'master.db', root/'source.db', root/'output.txt'
            black_root = position(['7g7f'])
            next_position = position(['7g7f', '3c3d'])
            flipped_root = flipped_sfen(black_root) + ' 2'
            flipped_next = flipped_sfen(next_position) + ' 3'
            write_db(master, [(position([]), ['7g7f'])])
            write_db(source, [(flipped_root, [flipped_move('3c3d')]), (flipped_next, [flipped_move('2g2f')])])
            result = run(master, source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text().splitlines(), ['startpos moves 7g7f 3c3d'])

    def test_cycle_component_and_bad_input_preserves_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'master.db', root/'source.db', root/'output.txt'
            write_db(master, [(position([]), [])])
            prefix = ['7g7f', '3c3d']
            cycle = ['5i6h', '5a6b', '6h5i', '6b5a']
            entries = [(position(prefix + cycle[:i]), [move] + (['2g2f'] if i == 0 else []))
                       for i, move in enumerate(cycle)]
            entries.append((position(prefix + ['2g2f']), ['8c8d']))
            write_db(source, entries)
            result = run(master, source, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('cycle seeds=1', result.stdout)
            self.assertEqual(len(output.read_text().splitlines()), 1)
            output.write_text('preserve me', encoding='utf8')
            write_db(source, [(position([]), ['not-a-move'])])
            result = run(master, source, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), 'preserve me')


if __name__ == '__main__':
    unittest.main()
