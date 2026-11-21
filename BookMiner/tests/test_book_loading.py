import importlib.util
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'BookMiner.py'
spec = importlib.util.spec_from_file_location('bookminer_loading_test', MODULE_PATH)
bookminer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bookminer
spec.loader.exec_module(bookminer)


class BookLoadingTests(unittest.TestCase):
    def test_db_comments_and_indented_lines_load_and_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'comments.db'
            path.write_text('  #YANEURAOU-DB2016 1.00\r\n'
                            '  // comment\r\n\t  \r\n'
                            f'  sfen {bookminer.SFEN_START} 1  \r\n'
                            '  7g7f none 10 0 \r\n  // another comment\r\n'
                            '\t2g2f none 20 0\r\n', encoding='utf-8-sig')
            for reader in (bookminer.read_bookminer_backup, bookminer.read_yaneuraou_book):
                book = bookminer.Book()
                reader(book, str(path))
                self.assertEqual([m.move for m in book.body[bookminer.SFEN_START].moveinfos], ['2g2f', '7g7f'])
            with bookminer.BookLib.open_book_probe(path) as probe:
                self.assertEqual({move.move for move in probe.probe(bookminer.SFEN_START)}, {'7g7f', '2g2f'})

    def test_db_accepts_bom_only_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'book.db'
            path.write_text('\ufeff\n#YANEURAOU-DB2016 1.00\n'
                            f'sfen {bookminer.SFEN_START} 1\n7g7f none 10 0\n', encoding='utf8')
            book = bookminer.Book()
            bookminer.read_bookminer_backup(book, str(path))
            self.assertEqual(book.body[bookminer.SFEN_START].moveinfos[0].move, '7g7f')

    def test_fast_load_ignores_unscored_moves_and_saves_scored_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.db'
            board = bookminer.cshogi.Board()
            board.push_usi('7g7f')
            child = bookminer.trim_sfen(board.sfen())
            source.write_text('#YANEURAOU-DB2016 1.00\n'
                              f'sfen {bookminer.SFEN_START} 1\n'
                              '7g7f none 100 0\n2g2f none None 0\n'
                              f'sfen {child} 2\n3c3d none None 0\n')
            book = bookminer.Book()
            bookminer.read_bookminer_backup(book, str(source))
            self.assertEqual([move.move for move in book.body[bookminer.SFEN_START].moveinfos], ['7g7f'])
            self.assertNotIn(child, book.body)
            output = root / 'output.db'
            bookminer.write_yaneuraou_book_records(book, str(output), None)
            self.assertIn('7g7f none 100', output.read_text())

    def test_simultaneous_saves_use_distinct_temporary_files(self):
        book = bookminer.Book()
        book.body[bookminer.SFEN_START] = bookminer.PositionInfo([bookminer.MoveInfo('7g7f', 10)], 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'book.db'
            original_replace = os.replace
            barrier = Barrier(2)
            temporary_names = []
            def replace(source, destination):
                temporary_names.append(source)
                barrier.wait(timeout=5)
                original_replace(source, destination)
            with patch.object(bookminer.os, 'replace', side_effect=replace), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(bookminer.write_yaneuraou_book_records, book, str(path), None) for _ in range(2)]
                for future in futures:
                    future.result(timeout=10)
            self.assertEqual(len(set(temporary_names)), 2)
            self.assertIn('7g7f', path.read_text())
            self.assertEqual(list(path.parent.glob('.book-*.db')), [])

    def test_save_preserves_unrelated_fixed_temp_on_success_and_failure(self):
        book = bookminer.Book()
        book.body[bookminer.SFEN_START] = bookminer.PositionInfo([bookminer.MoveInfo('7g7f', 10)], 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for extension in ('.db', '.ybb'):
                with self.subTest(extension=extension):
                    output = root / ('book' + extension)
                    legacy = root / ('tmp-book' + extension)
                    legacy.write_text('unrelated')
                    output.write_text('previous')
                    with patch.object(bookminer.os, 'replace', side_effect=PermissionError('locked')):
                        with self.assertRaisesRegex(PermissionError, 'locked'):
                            bookminer.write_yaneuraou_book_records(book, str(output), None)
                    self.assertEqual(legacy.read_text(), 'unrelated')
                    self.assertEqual(output.read_text(), 'previous')
                    self.assertEqual(set(root.glob('.book-*' + extension)), set())
                    bookminer.write_yaneuraou_book_records(book, str(output), None)
                    self.assertEqual(legacy.read_text(), 'unrelated')

    def test_tied_min_int16_scores_save_without_underflow(self):
        book = bookminer.Book()
        book.body[bookminer.SFEN_START] = bookminer.PositionInfo([
            bookminer.MoveInfo('7g7f', -32768), bookminer.MoveInfo('2g2f', -32768)], 1)
        with tempfile.TemporaryDirectory() as directory:
            for extension in ('.db', '.ybb'):
                with self.subTest(extension=extension):
                    path = str(Path(directory) / ('book' + extension))
                    bookminer.write_yaneuraou_book_records(book, path, None)
                    reloaded = bookminer.Book()
                    bookminer.read_bookminer_backup(reloaded, path)
                    self.assertEqual([move.eval for move in reloaded.body[bookminer.SFEN_START].moveinfos],
                                     [-32768, -32768])
        self.assertEqual([move.eval for move in book.body[bookminer.SFEN_START].moveinfos], [-32768, -32768])

    def test_duplicate_move_in_single_block_keeps_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'duplicates.db'
            db.write_text('#YANEURAOU-DB2016 1.00\n'
                          f'sfen {bookminer.SFEN_START} 1\n'
                          '7g7f none 100 1\n2g2f none 30 2\n7g7f none 20 3\n')
            ybb = root / 'duplicates.ybb'
            move = bookminer.BookLib.BookMove
            bookminer.BookLib.write_ybb_book({bookminer.SFEN_START_PLY1: [
                move('7g7f', 'none', 100, 1, 1), move('2g2f', 'none', 30, 2, 1),
                move('7g7f', 'none', 20, 3, 1)]}, ybb)
            for path in (db, ybb):
                for reader in (bookminer.read_bookminer_backup, bookminer.read_yaneuraou_book):
                    with self.subTest(path=path, reader=reader.__name__):
                        book = bookminer.Book()
                        reader(book, str(path))
                        self.assertEqual([(m.move, m.eval, m.depth) for m in book.body[bookminer.SFEN_START].moveinfos],
                                         [('2g2f', 30, 2), ('7g7f', 20, 3)])

    def test_readers_sort_first_block_and_after_merging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'unsorted.db'
            db.write_text('#YANEURAOU-DB2016 1.00\n'
                          f'sfen {bookminer.SFEN_START} 1\n'
                          '7g7f none 10 0\n2g2f none 100 1\n')
            ybb = root / 'unsorted.ybb'
            move = bookminer.BookLib.BookMove
            bookminer.BookLib.write_ybb_book({bookminer.SFEN_START_PLY1: [
                move('7g7f', 'none', 10, 0, 1), move('2g2f', 'none', 100, 1, 1)]}, ybb)
            # The writer sorts moves; reverse its records to exercise an unsorted input.
            data = bytearray(ybb.read_bytes())
            offset = bookminer.BookLib.YBB_HEADER_STRUCT.size + bookminer.BookLib.YBB_INDEX_STRUCT.size
            size = bookminer.BookLib.YBB_MOVE_DEPTH_STRUCT.size
            data[offset:offset + 2 * size] = data[offset + size:offset + 2 * size] + data[offset:offset + size]
            ybb.write_bytes(data)
            for path in (db, ybb):
                for reader in (bookminer.read_bookminer_backup, bookminer.read_yaneuraou_book):
                    with self.subTest(path=path, reader=reader.__name__):
                        book = bookminer.Book()
                        reader(book, str(path))
                        self.assertEqual([m.move for m in book.body[bookminer.SFEN_START].moveinfos],
                                         ['2g2f', '7g7f'])

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
