# やねうら王の定跡ファイルから、開始局面のSFENを書いたテキストフォーマットに変換します。

import sys
import argparse
from pathlib import Path
import cshogi

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneShogiLib import *

# やねうら王定跡DBのheader
YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"


def retrieve_yanebook(yanebook_path: str, startsfens_path: str):

    # 先端局面
    startsfens : dict[str, int] = {}

    # 定跡局面
    book : set[str] = set()

    # 現在のparse中の局面と手数
    sfen : str | None = None
    ply : int | None = None

    moves : list[str] = []

    def append_to_book():
        nonlocal sfen, ply, moves

        if sfen == None:
            return

        # A frontier found earlier may later appear as a book position.
        sfen_f = flipped_sfen(sfen)
        startsfens.pop(sfen, None)
        startsfens.pop(sfen_f, None)
        book.add(sfen)
        board = cshogi.Board(sfen)
        board.move_number = ply
        for move in moves:
            if move in ("none", "None", "null", "0000", "pass", "resign", "win"):
                continue
            move32 = board.move_from_usi(move)
            if not move32 or move32 not in board.legal_moves:
                raise ValueError(f"illegal book move {move!r}: {board.sfen()}")
            board.push(move32)
            next_sfen, next_ply = trim_sfen_ply(board.sfen())
            board.pop()
            next_sfen_f = flipped_sfen(next_sfen)
            if next_sfen in book or next_sfen_f in book:
                continue
            existing = next_sfen if next_sfen in startsfens else next_sfen_f
            if existing in startsfens:
                startsfens[existing] = min(startsfens[existing], next_ply)
            else:
                startsfens[next_sfen] = next_ply
            if len(startsfens) % 10000 == 0:
                print(len(startsfens))

        sfen = None
        moves = []

    first = True
    with open(yanebook_path, 'r', encoding='utf-8-sig') as source:
        for line in source:
            if first:
                if YANEURAOU_BOOK_HEADER_V1 not in line:
                    print("warning : illegal YaneuraOu Book Header")
                first = False
                continue
            line = line.strip()
            if not line or line.startswith(('#', '//')):
                continue
            if line.startswith('sfen '):
                append_to_book()
                sfen, ply = trim_sfen_ply(line)
                if ply == 0:
                    # ペタショック定跡の手数0は1として扱う。
                    ply = 1
            else:
                move_str, *_ = line.split()
                moves.append(move_str)
    append_to_book()

    # ファイルに書き出す。
    count = 0
    with open(startsfens_path, "w") as f:
        for sfen, ply in startsfens.items():
            f.write(f"{sfen} {ply} \n")
            count += 1
            if count % 10000 == 0:
                print(f"count = {count}")


def main():
    parser = argparse.ArgumentParser(description="2つのファイルパスを表示します（2つ目は省略可）。")
    parser.add_argument("path1", nargs="?", help="やねうら王の定跡ファイルのファイルのパス")
    parser.add_argument("path2", nargs="?", default=None, help="書き出すファイルパス（省略可）")

    args = parser.parse_args()

    yanebook_path   = args.path1
    startsfens_path = args.path2

    # yanebook_path = "yanebook.txt"
    # yanebook_path = r"C:\Users\yaneen\largefile\Shogi\Shogidokoro\Engine\tanuki-dr5_with_petabook\book\user_book1.db"

    # file1 が指定されていない場合 → help を表示して終了
    if yanebook_path is None:
        parser.print_help(sys.stderr)
        return

    if startsfens_path is None:
        # 変換後のファイルpathが指定されていないので、変換前のファイルに".hcpe"を付加したものにする。
        startsfens_path = yanebook_path + "-startsfens.txt"

    print("yanebook_path   : ", yanebook_path)
    print("startsfens_path : ", startsfens_path)

    retrieve_yanebook(yanebook_path, startsfens_path)

if __name__ == "__main__":
    main()
    # game_data_read_write_test()

