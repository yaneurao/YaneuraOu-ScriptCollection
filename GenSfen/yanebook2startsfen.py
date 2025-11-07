# やねうら王の定跡ファイルから、開始局面のSFENを書いたテキストフォーマットに変換します。

import sys
import argparse
from ShogiCommonLib import *

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

        if sfen in book:
            # 登録されているなら無視
            pass
        else:
            sfen_f     = flipped_sfen(sfen)
            # flipped sfenのほうも調べる。
            if sfen_f in book:
                # flipした局面が登録されているなら無視
                pass
            else:
                # movesをfrontier nodesに登録する。
                board = cshogi.Board(sfen) # type:ignore
                board.move_number = ply
                for move in moves:
                    # moveで1手進める。
                    board.push_usi(move)
                    next_sfen , next_ply = trim_sfen_ply(board.sfen())

                    # 1手進めた結果、flipped sfenのほうが登録されている可能性が出てくる。

                    if next_sfen in startsfens:
                        # 登録されている。手数が若いか比較する。
                        book_ply = startsfens[next_sfen]
                        if next_ply < book_ply:
                            # plyは見つかった最短の手数にする必要がある。
                            startsfens[next_sfen] = next_ply
                    else:
                        next_sfen_f = flipped_sfen(next_sfen)
                        # flipした局面が登録されているか
                        if next_sfen_f in startsfens:
                            book_ply = startsfens[next_sfen_f]
                            if next_ply < book_ply:
                                # plyは見つかった最短の手数にする必要がある。
                                startsfens[next_sfen_f] = next_ply
                        else:
                            # 登録されていないことが確定したので、flipしていないほうとして登録する。
                            startsfens[next_sfen] = next_ply

                    # 進捗を出力する。
                    if len(startsfens) % 10000:
                        print(len(startsfens))

                    board.pop()
            
        sfen = None
        moves = []

    first = True
    for line in open(yanebook_path, 'r', encoding='utf-8'):
        if first:
            # lineの先頭に BOM がついていることがあるので、これと一致するかの判定は in で行う。
            if YANEURAOU_BOOK_HEADER_V1 not in line:
                print("warning : illegal YaneuraOu Book Header")
            first = False
            continue
        line = line.rstrip()
        if line.startswith('sfen '):
            append_to_book()
            sfen , ply = trim_sfen_ply(line)
        else:
            move_str, *_ = line.split(' ')
            moves.append(move_str)
    append_to_book()

    # ファイルに書き出す。
    with open(startsfens_path, "w") as f:
        for sfen, ply in startsfens.items():
            f.write(f"{sfen} {ply} \n")


def main():
    parser = argparse.ArgumentParser(description="2つのファイルパスを表示します（2つ目は省略可）。")
    parser.add_argument("path1", nargs="?", help="やねうら王の定跡ファイルのファイルのパス")
    parser.add_argument("path2", nargs="?", default=None, help="書き出すファイルパス（省略可）")

    args = parser.parse_args()

    yanebook_path   = args.path1
    startsfens_path = args.path2

    # pack_path = "kif/kif_20251106213805.pack"

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

