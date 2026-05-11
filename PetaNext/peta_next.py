# ペタショック化されたやねうら王形式の定跡から、
# 「次に掘ると良い leaf node の sfen」を書き出すスクリプト。
#
# 開始局面 (root) は root_sfen.txt のようなテキストで指定する。
#
# 詳細は同フォルダの README.md と、定跡ツリー上での leaf 選出アルゴリズムについては
# やねうら王プロジェクト内の peta-shock 仕様メモを参照。

import argparse
import os
from dataclasses import dataclass
from itertools import zip_longest

from tqdm import tqdm

from ShogiCommonLib import (
    Sfen, Move, Eval, PositionStr,
    Board,
    flipped_sfen, flipped_move,
    trim_sfen, trim_sfen_ply,
    SFEN_START_PLY1,
)


# ShogiCommonLib.Board は内部で cshogi.Board.set_position() を呼ぶが、
# set_position() は素の SFEN 文字列は受け付けず、"startpos" か "sfen <SFEN>" を要求する。
# このスクリプトでは ply 付き SFEN 文字列を多用するので、それ用の薄いヘルパーを用意する。
def board_from_sfen(sfen_with_ply: Sfen) -> Board:
    return Board(f"sfen {sfen_with_ply}")

# ============================================================
#                     定数
# ============================================================

# やねうら王形式の定跡DBファイルのヘッダー
YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"


# ============================================================
#                     型定義
# ============================================================

@dataclass
class MoveInfo:
    move : Move
    # peta_book に書かれている指し手は基本 int の eval を持つ。
    # eval が欠損しているレコードは無視する。
    eval : Eval


@dataclass
class PositionInfo:
    # 注: peta_book では eval 降順で書かれている前提 (やねうら王が出力する形式)。
    # bestmove は moveinfos[0]。
    moveinfos : list[MoveInfo]


# ============================================================
#                     I/O
# ============================================================

def read_peta_book(path: str) -> dict[Sfen, PositionInfo]:
    """
    ペタショック化済みのやねうら王形式定跡を読み込み、dict[Sfen(ply無し), PositionInfo] を返す。

    やねうら王形式:
        #YANEURAOU-DB2016 1.00
        sfen <SFEN> <ply>
        <move> <ponder> <eval> <depth>
        ...
    """
    book : dict[Sfen, PositionInfo] = {}

    sfen : Sfen | None = None
    moveinfos : list[MoveInfo] = []

    def flush():
        nonlocal sfen, moveinfos
        if sfen is not None:
            book[sfen] = PositionInfo(moveinfos)
        sfen = None
        moveinfos = []

    total_bytes = os.path.getsize(path)
    # バイトモードで開いて、tqdm でバイト単位の progress を出す。
    # (テキストモードでは f.tell() が改行バッファリングで正確に取れないため)
    with open(path, 'rb') as fb, tqdm(
        total=total_bytes, unit='B', unit_scale=True, desc='read peta_book',
    ) as pbar:
        first = True
        for raw_line in fb:
            pbar.update(len(raw_line))
            try:
                line = raw_line.decode('utf-8')
            except UnicodeDecodeError:
                continue
            if first:
                # BOM 付きの場合があるので in で判定
                if YANEURAOU_BOOK_HEADER_V1 not in line:
                    print(f"warning: illegal YaneuraOu Book Header: {line.rstrip()}")
                first = False
                continue
            line = line.rstrip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('sfen '):
                flush()
                sfen = trim_sfen(line)
                moveinfos = []
            else:
                # move ponder eval depth [...]
                parts = line.split()
                if len(parts) < 3:
                    continue
                move_str = parts[0]
                try:
                    eval_v = int(parts[2])
                except ValueError:
                    continue
                moveinfos.append(MoveInfo(move_str, eval_v))
        flush()

    return book


def decode_position_string(s: PositionStr) -> Sfen:
    """
    入力を ply 付き SFEN に変換する。受け付ける形式:
      - "startpos"
      - "startpos moves <move> <move> ..."
      - "sfen <SFEN>"  (SFEN は board+turn+hand、末尾 ply は任意)
      - "sfen <SFEN> moves <move> ..."
      - 先頭に "position " が付いていてもよい
      - 直接 SFEN 文字列のみでもよい (board+turn+hand[+ply])
    """
    s = s.strip()
    if not s:
        return SFEN_START_PLY1

    if s.startswith('position '):
        s = s[len('position '):].strip()

    if ' moves' in s or s.endswith(' moves'):
        head, _, moves_part = s.partition('moves')
        head = head.strip()
        moves = moves_part.split()
    else:
        head = s
        moves = []

    if head.startswith('sfen '):
        head = head[len('sfen '):].strip()

    if head == '' or head == 'startpos':
        head = SFEN_START_PLY1

    board = board_from_sfen(head)
    for m in moves:
        board.push_usi(m)
    return board.sfen()


def read_root_sfens(path: str) -> list[Sfen]:
    """root sfen ファイルを読み、各行を decode_position_string で ply 付き SFEN にして返す。"""
    sfens : list[Sfen] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            sfens.append(decode_position_string(line))
    if not sfens:
        # ファイルは存在するが全行がコメント/空行だった場合は startpos のみで動かす。
        return [SFEN_START_PLY1]
    return sfens


# ============================================================
#                     コアアルゴリズム
# ============================================================

def lookup(peta_book: dict[Sfen, PositionInfo], sfen: Sfen) -> tuple[PositionInfo | None, bool]:
    """
    peta_book を sfen で引く。無ければ flipped_sfen を引く。
    返り値は (PositionInfo or None, flipped で hit したか)。
    """
    if sfen in peta_book:
        return peta_book[sfen], False
    sfen_f = flipped_sfen(sfen)
    if sfen_f in peta_book:
        return peta_book[sfen_f], True
    return None, False


def get_best_eval(pos: PositionInfo) -> int | None:
    """先頭の eval。空またはNoneならNone。"""
    if not pos.moveinfos:
        return None
    e = pos.moveinfos[0].eval
    return e if isinstance(e, int) else None


def peta_next_one_turn(
    peta_book: dict[Sfen, PositionInfo],
    root_sfens: list[Sfen],
    peta_eval_diff: int,
    max_ply: int,
    turn: int,
) -> list[Sfen]:
    """
    指定された turn (1=先手定跡, 0=後手定跡) について、leaf node の sfen 一覧を返す。

    アルゴリズム:
      - 各 root から BFS で展開する。
      - 手番側 (ply % 2 == turn) では bestmove のみを辿る。
      - 非手番側では (現在局面の手番側から見た) root_best_eval - peta_eval_diff 以上の eval の指し手を辿る。
      - peta_book に存在しない局面に出たら leaf として記録する。
      - root_best_eval は手番側視点で持ち回るため、1手進めるごとに符号を反転する。
      - 局面の ply (手数) が max_ply を超えたらその局面は掘らない (leaf にも記録しない)。

    root_best_eval を絶対基準として持つことで、BFS 深さ方向に累積で評価値が下がり続けて
    現実的でない leaf に到達することを防ぐ (Discord の説明にある 2a の制限)。
    """
    # 順序を保つために dict をセット代わりに使う
    leaf_sfens : dict[Sfen, None] = {}

    # 訪問済み局面 (ply無し sfen で管理)
    visited : set[Sfen] = set()

    # 現周の局面集合: sfen(ply付き) -> (root_best_eval(手番側視点), eval_diff)
    current_sfens : dict[Sfen, tuple[int, int]] = {}

    for sfen_with_ply in root_sfens:
        sfen_trim, _ply = trim_sfen_ply(sfen_with_ply)
        pos, _flipped = lookup(peta_book, sfen_trim)
        if pos is None:
            # root 自体が定跡に無い。これも leaf として記録する。
            leaf_sfens[sfen_with_ply] = None
            continue
        root_best = get_best_eval(pos)
        if root_best is None:
            leaf_sfens[sfen_with_ply] = None
            continue
        current_sfens[sfen_with_ply] = (root_best, peta_eval_diff)
        print(f"root sfen: {sfen_with_ply}  root_best = {root_best}")

    step = 1
    while current_sfens:
        next_sfens : dict[Sfen, tuple[int, int]] = {}

        # この step で実際に展開対象になった局面の ply を集めて表示用に使う。
        # (root_sfens に異なる ply の局面が混在しうるので、min/max を出す)
        plys_this_step : list[int] = []

        for sfen_with_ply, (root_best_eval, eval_diff) in current_sfens.items():
            sfen_trim, ply = trim_sfen_ply(sfen_with_ply)

            # max_ply を超えたら掘らない (leaf にも記録しない)
            if ply > max_ply:
                continue

            sfen_f = flipped_sfen(sfen_trim)
            if sfen_trim in visited or sfen_f in visited:
                continue
            visited.add(sfen_trim)
            plys_this_step.append(ply)

            pos, flipped_hit = lookup(peta_book, sfen_trim)
            if pos is None:
                # 定跡ツリーを出た -> leaf
                leaf_sfens[sfen_with_ply] = None
                continue

            moveinfos = pos.moveinfos
            if not moveinfos:
                continue
            best_eval = moveinfos[0].eval
            if not isinstance(best_eval, int):
                continue

            is_my_turn = (ply % 2 == turn)
            # 手番側: bestmove のみ。非手番側: root_best_eval - eval_diff 以上の eval を持つ手すべて。
            eval_low = best_eval if is_my_turn else (root_best_eval - eval_diff)

            for mi in moveinfos:
                if not isinstance(mi.eval, int):
                    continue
                if mi.eval < eval_low:
                    # 既に eval 降順なので、ここで break しても良いが、None 混在の保険で continue にしておく。
                    continue

                # peta_book は先手局面に統一されている前提なので、flipped_hit のときは move も flip して戻す。
                move = flipped_move(mi.move) if flipped_hit else mi.move

                board = board_from_sfen(sfen_with_ply)
                board.push_usi(move)
                next_sfen_with_ply = board.sfen()

                # 次局面では root_best_eval を符号反転 (手番側視点で持つため)
                next_sfens[next_sfen_with_ply] = (-root_best_eval, eval_diff)

        if plys_this_step:
            pmin, pmax = min(plys_this_step), max(plys_this_step)
            ply_str = f"{pmin}" if pmin == pmax else f"{pmin}..{pmax}"
        else:
            ply_str = "-"
        print(f"step={step}  ply={ply_str}  next={len(next_sfens)}  leaf={len(leaf_sfens)}")

        current_sfens = next_sfens
        step += 1

    return list(leaf_sfens.keys())


# ============================================================
#                     main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="ペタショック化されたやねうら王定跡から、次に掘ると良い leaf node の sfen を書き出す。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--peta-book', default='peta_book.db',
                        help="ペタショック化済みのやねうら王形式定跡DBファイル。default=peta_book.db")
    parser.add_argument('--root-sfen', default=None,
                        help="開始局面ファイル。各行は USI position 形式 (startpos / startpos moves ... / sfen ...) か SFEN 文字列。指定しない場合は startpos (平手の開始局面) のみを root とする。")
    parser.add_argument('--out-dir', default='.',
                        help="出力ディレクトリ。default=カレント")
    parser.add_argument('--peta-eval-diff', type=int, default=10,
                        help="BookEvalDiff (cp)。root の bestmove の評価値からこの幅まで非手番側の指し手を辿る (下限のみ)。default=10")
    parser.add_argument('--max-ply', type=int, default=200,
                        help="BFS で掘る最大手数 (ply)。これを超える局面は展開しない。default=200")

    side_group = parser.add_mutually_exclusive_group()
    side_group.add_argument('--black-only', action='store_true',
                            help="先手定跡 (turn=1) のみ出力する。")
    side_group.add_argument('--white-only', action='store_true',
                            help="後手定跡 (turn=0) のみ出力する。")

    args = parser.parse_args()

    print(f"reading peta book: {args.peta_book}")
    peta_book = read_peta_book(args.peta_book)
    print(f"loaded {len(peta_book)} positions")

    if args.root_sfen is None:
        root_sfens = [SFEN_START_PLY1]
        print("no --root-sfen specified, using startpos as the only root")
    else:
        root_sfens = read_root_sfens(args.root_sfen)
        print(f"loaded {len(root_sfens)} root sfens from {args.root_sfen}")

    os.makedirs(args.out_dir, exist_ok=True)

    # 先手定跡 = turn 1, 後手定跡 = turn 0
    sides : list[tuple[str, int]] = []
    if not args.white_only:
        sides.append(('black', 1))
    if not args.black_only:
        sides.append(('white', 0))

    outputs : dict[str, list[Sfen]] = {}
    for label, turn in sides:
        print(f"--- {label} (turn={turn}) ---")
        leafs = peta_next_one_turn(peta_book, root_sfens, args.peta_eval_diff, args.max_ply, turn)
        out_path = os.path.join(args.out_dir, f"think_sfens-{label}.txt")
        with open(out_path, 'w', encoding='utf-8') as w:
            for s in leafs:
                w.write(s + '\n')
        print(f"write {out_path}, {len(leafs)} leaf sfens")
        outputs[label] = leafs

    # 先手・後手両方ある場合のみ、交互マージした合体ファイルを作る。
    if len(outputs) == 2:
        bw_path = os.path.join(args.out_dir, "think_sfens.txt")
        b = outputs.get('black', [])
        w = outputs.get('white', [])
        with open(bw_path, 'w', encoding='utf-8') as fbw:
            for lb, lw in zip_longest(b, w, fillvalue=None):
                if lb is not None:
                    fbw.write(lb + '\n')
                if lw is not None:
                    fbw.write(lw + '\n')
        print(f"write {bw_path}, {len(b) + len(w)} leaf sfens (merged)")


if __name__ == '__main__':
    main()
