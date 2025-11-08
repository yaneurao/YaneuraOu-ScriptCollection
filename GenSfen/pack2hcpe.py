# .pack形式のファイルを.hcpe形式に変換する

import sys
import argparse
from ShogiCommonLib import *

def game_data_read_write_test():
    # テストコード

    gd = GameDataEncoder()
    gd.set_startsfen("lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1")

    board = gd.board
    move = board.move_from_usi("7g7f") & 0xffff # type:ignore

    gd.write_uint16(move) # 指し手1
    gd.write_int16(100) # 評価値+1234
    board.push_move16(move)

    move = board.move_from_usi("3c3d") & 0xffff # type:ignore

    gd.write_uint16(move) # 指し手1
    gd.write_int16(-100) # 評価値+1234

    gd.write_uint16(0x0081) # 先手側の勝ち
    gd.write_uint8(255) # status何か。

    decoder = GameDataDecoder(gd.get_bytes())
    sfen = decoder.get_sfen()
    print(f"sfen={sfen}")
    move16 = decoder.read_uint16()
    print(f"move={move16:04x}")
    eval16 = decoder.read_int16()
    print(f"eval={eval16}")

    move16 = decoder.read_uint16()
    print(f"move={move16:04x}")
    eval16 = decoder.read_int16()
    print(f"eval={eval16}")

    move16 = decoder.read_uint16()
    print(f"game_result={move16:04x}")
    status = decoder.read_uint8()
    print(f"status={status}")

    print(gd.data)
    print(len(gd.data))

    # 棋譜に書き出してみる。
    kif = KifWriter(1000)
    kif.write_game(gd)
    kif.close()

    # 書き出したkifファイルをhcpe形式に変換してみる。
    kif_path = kif.get_kif_filename()
    pack_file_to_hcpe(kif_path, kif_path + ".hcpe")


def main():
    parser = argparse.ArgumentParser(description="2つのファイルパスを表示します（2つ目は省略可）。")
    parser.add_argument("file1", nargs="?", help="1つ目のファイルのパス")
    parser.add_argument("file2", nargs="?", default=None, help="2つ目のファイルのパス（省略可）")

    args = parser.parse_args()

    pack_path = args.file1
    hcpe_path = args.file2

    pack_path = "kif/kif_20251108133357_100000.pack"

    # file1 が指定されていない場合 → help を表示して終了
    if pack_path is None:
        parser.print_help(sys.stderr)
        return

    if hcpe_path is None:
        # 変換後のファイルpathが指定されていないので、変換前のファイルに".hcpe"を付加したものにする。
        hcpe_path = pack_path + ".hcpe"

    print("File 1:", pack_path)
    print("File 2:", hcpe_path)

    pack_file_to_hcpe(pack_path, hcpe_path)

if __name__ == "__main__":
    main()
    # game_data_read_write_test()

