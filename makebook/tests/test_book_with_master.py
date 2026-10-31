from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cshogi
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'CommonLib'))
from YaneShogiLib import flipped_sfen, flipped_move
from YaneuraOuBookLib import read_yaneuraou_book, write_ybb_book
sys.path.insert(0, str(ROOT / 'makebook'))
import book_with_master_to_think_sfens as compact
from book_to_think_sfens import decode_position_string, normalize_sfen_key

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


def run(master, source, output, mode='bidirectional'):
    return subprocess.run([sys.executable, str(SCRIPT), str(master), str(source), str(output),
                           '--progress-interval', '0', '--search-mode', mode], text=True, capture_output=True)


class BookWithMasterTest(unittest.TestCase):
    def test_reverse_index_handles_empty_nodes_and_flipped_edges(self):
        class Book:
            offsets = np.array([0, 0, 2, 2, 3], dtype=np.uint32)
        edges = np.array([2, compact.FLIP | 3, 3], dtype=np.uint32)
        offsets, parents = compact.reverse_index(Book(), edges, 4)
        self.assertEqual(offsets.tolist(), [0, 0, 0, 1, 3])
        self.assertEqual(parents[offsets[2]:offsets[3]].tolist(), [1])
        self.assertEqual(sorted(parents[offsets[3]:offsets[4]].tolist()), [1, 3])

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
                        forward = root / 'forward.txt'
                        forward_result = run(m, s, forward, 'forward')
                        self.assertEqual(forward_result.returncode, 0, forward_result.stderr)
                        self.assertEqual(set(lines), set(forward.read_text(encoding='utf8').splitlines()))

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
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_text().splitlines(), ['startpos moves 7g7f 3c3d'])

    def test_both_orientations_merge_their_distinct_moves(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'master.db', root/'source.db', root/'output.txt'
            initial = position(['7g7f'])
            reversed_initial = flipped_sfen(initial) + ' 2'
            write_db(master, [(position([]), ['7g7f'])])
            write_db(source, [(initial, ['3c3d']), (reversed_initial, [flipped_move('8c8d')]),
                              (position(['7g7f', '3c3d']), []),
                              (position(['7g7f', '8c8d']), [])])
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(set(output.read_text().splitlines()), {
                        'startpos moves 7g7f 3c3d', 'startpos moves 7g7f 8c8d'})

    def test_shared_master_prefix_connects_all_input_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'master.db', root/'source.db', root/'output.txt'
            write_db(master, [(position([]), ['7g7f']),
                              (position(['7g7f']), ['3c3d', '8c8d'])])
            write_db(source, [(position(['7g7f', '3c3d']), ['2g2f']),
                              (position(['7g7f', '8c8d']), ['2g2f'])])
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(set(output.read_text().splitlines()), {
                        'startpos moves 7g7f 3c3d', 'startpos moves 7g7f 8c8d'})
                    self.assertIn('roots matched=2, unmatched=0', result.stdout)

    def test_master_reaching_non_root_does_not_create_false_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'master.db', root/'source.db', root/'output.txt'
            master_path = ['2g2f', '8c8d', '7g7f', '3c3d']
            write_db(master, [(position(master_path[:i]), [move])
                              for i, move in enumerate(master_path)])
            source_path = ['7g7f', '3c3d', '2g2f']
            write_db(source, [(position(source_path), ['8c8d']),
                              (position(source_path + ['8c8d']), ['2f2e'])])
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('roots matched=0, unmatched=1', result.stdout)
                    self.assertTrue(output.read_text().startswith('sfen '))

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

    def test_empty_master_keeps_unmatched_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'empty.db', root/'source.db', root/'output.txt'
            write_db(master, [])
            write_db(source, [(position(['7g7f']), ['3c3d'])])
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output.read_text().startswith('sfen '))
                    self.assertIn('roots matched=0, unmatched=1', result.stdout)

    def test_drop_and_promotion_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            master, source, output = root/'empty.db', root/'source.db', root/'output.txt'
            write_db(master, [])
            board = cshogi.Board()
            board.set_sfen('4k4/9/4P4/9/9/9/9/9/4K4 b P 1')
            source_root = board.sfen()
            expected = set()
            entries = [(source_root, ['5c5b+', 'P*1e'])]
            for move in ('5c5b+', 'P*1e'):
                child = cshogi.Board(source_root)
                child.push_usi(move)
                expected.add(normalize_sfen_key(child.sfen()))
                entries.append((child.sfen(), []))
            write_db(source, entries)
            for mode in ('forward', 'bidirectional'):
                with self.subTest(mode=mode):
                    result = run(master, source, output, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    actual = {normalize_sfen_key(decode_position_string(line))
                              for line in output.read_text().splitlines()}
                    self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
