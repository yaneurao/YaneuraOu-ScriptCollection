import sys
import unittest
from pathlib import Path

import cshogi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from peta_next import (MoveInfo, PositionInfo, RootPosition, peta_next_one_turn)


def sfen_after(moves):
    board = cshogi.Board()
    for move in moves:
        board.push_usi(move)
    return board.sfen()


def key(sfen):
    return sfen.rsplit(' ', 1)[0]


class PetaNextSearchTests(unittest.TestCase):
    def test_position_with_no_moves_is_a_leaf(self):
        start, child = sfen_after([]), sfen_after(['7g7f'])
        book = {key(start): PositionInfo([MoveInfo('7g7f', 0)]),
                key(child): PositionInfo([])}
        leaves = peta_next_one_turn(book, [RootPosition('startpos', start)], 10, 10, 1, verbose=False)
        self.assertEqual([leaf.position_cmd for leaf in leaves], ['startpos moves 7g7f'])

    def test_overlapping_roots_keep_distinct_evaluation_thresholds(self):
        start, child = sfen_after([]), sfen_after(['7g7f'])
        book = {
            key(start): PositionInfo([MoveInfo('7g7f', 100)]),
            key(child): PositionInfo([MoveInfo('3c3d', 0), MoveInfo('8c8d', -50)]),
        }
        roots = [RootPosition('startpos', start), RootPosition('sfen ' + child, child)]
        leaves = peta_next_one_turn(book, roots, 10, 20, 1, verbose=False)
        self.assertEqual(len(leaves), 2)
        self.assertEqual({key(leaf.sfen_with_ply) for leaf in leaves},
                         {key(sfen_after(['7g7f', '3c3d'])), key(sfen_after(['7g7f', '8c8d']))})

    def test_missing_root_over_max_ply_is_not_output(self):
        root = RootPosition('startpos moves 7g7f', sfen_after(['7g7f']))
        self.assertEqual(peta_next_one_turn({}, [root], 10, 1, 1, verbose=False), [])

    def test_bestmove_tie_uses_only_first_move_on_own_turn(self):
        start = sfen_after([])
        book = {key(start): PositionInfo([MoveInfo('7g7f', 0), MoveInfo('2g2f', 0)])}
        leaves = peta_next_one_turn(book, [RootPosition('startpos', start)], 10, 10, 1, verbose=False)
        self.assertEqual([leaf.position_cmd for leaf in leaves], ['startpos moves 7g7f'])

    def test_side_to_move_uses_sfen_not_ply_parity(self):
        board = cshogi.Board()
        sfen = board.sfen().replace(' b ', ' w ')
        book = {key(sfen): PositionInfo([MoveInfo('3c3d', 0), MoveInfo('8c8d', -5)])}
        leaves = peta_next_one_turn(book, [RootPosition('sfen ' + sfen, sfen)], 10, 10, 1, verbose=False)
        self.assertEqual(len(leaves), 2)


if __name__ == '__main__':
    unittest.main()
