# HCPE ファイルの各局面を ONNX モデルで再評価し、HCPE3 形式 (1 hcpe = moveNum=1 のゲーム) で書き出す。
#
# DeepLearningShogi/dlshogi/utils/hcpe3_re_eval.py をベースに、入力を HCPE に差し替えたもの。
#  - selectedMove16 = 元の hcpe の bestMove16
#  - eval = モデル value → value_to_score
#  - MoveVisits = モデル policy を softmax して p * 65535 で uint16 量子化 (policy 蒸留)
#  - 各 hcpe を「moveNum=1 の 1 ゲーム」として並べる
#
# hcpe → hcpe3 学習パイプラインの蒸留入力を作る用途を想定。
# policy は MCTS の visit 分布ではなくモデル予測分布になる点に注意。

import argparse
import os
import numpy as np
import onnxruntime
from tqdm import tqdm

from cshogi import (
    Board,
    HuffmanCodedPosAndEval,
    dtypeHcp, dtypeMove16, dtypeEval,
    move16,
)
from cshogi.dlshogi import (
    make_input_features, make_move_label,
    FEATURES1_NUM, FEATURES2_NUM,
)

# ============================================================
#                     HCPE3 dtype 定義 (本家 hcpe3_re_eval.py と同一)
# ============================================================

HuffmanCodedPosAndEval3 = np.dtype([
    ('hcp', dtypeHcp),      # 開始局面 (32B)
    ('moveNum', np.uint16), # 手数
    ('result', np.uint8),   # 結果 (下位2bit: 勝敗, bit2:千日手, bit3:入玉宣言, bit4:最大手数)
    ('gameInfo', np.uint8), # bit0-1:対戦相手, bit2-3:最大手数
])

MoveInfo = np.dtype([
    ('selectedMove16', dtypeMove16),
    ('eval', dtypeEval),
    ('candidateNum', np.uint16),
])

MoveVisits = np.dtype([
    ('move16', dtypeMove16),
    ('visitNum', np.uint16),
])

HCPE_SIZE = HuffmanCodedPosAndEval.itemsize  # 38

# policy 量子化のスケール (visit 上限)
VISIT_SCALE = 65535


# ============================================================
#                     value / policy 変換
# ============================================================

def value_to_score(values: np.ndarray, a: float) -> np.ndarray:
    """value (0..1) → score (cp)。本家 hcpe3_re_eval.py と同一。"""
    scores = np.empty_like(values)
    scores[values == 1] = 30000
    scores[values == 0] = -30000
    mask = (values != 1) & (values != 0)
    scores[mask] = -a * np.log(1 / values[mask] - 1)
    scores = np.clip(scores, -30000, 30000)
    return scores


def softmax_1d(logits: np.ndarray) -> np.ndarray:
    """1 次元 logits に softmax。"""
    m = float(np.max(logits))
    e = np.exp(logits - m)
    return e / float(np.sum(e))


def gameresult_to_hcpe3_result(gr: int) -> int:
    """
    HCPE の gameResult (cshogi: 0=Draw, 1=BlackWin, 2=WhiteWin) を
    HCPE3 result の下位 2bit に詰める。

    HCPE3 result の bit 配置 (本家 hcpe3_re_eval.py のコメントより):
      xxxxxx11: 勝敗
      xxxxx1xx: 千日手
      xxxx1xxx: 入玉宣言
      xxx1xxxx: 最大手数

    cshogi 値をそのまま下位 2bit に入れる。Draw=0 のままだと「不明」と区別できないが、
    1 hcpe = 1 局面の独立データなので、学習側で result を重視しない設定 (alpha_r=0 等) で
    使うのが想定用途。result を厳密に必要とするパイプラインで使う場合は呼び出し側で
    別途設定する。
    """
    return int(gr) & 0x3


# ============================================================
#                     main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "HCPE の各局面を ONNX モデルで再評価し、HCPE3 形式 "
            "(1 hcpe = moveNum=1 ゲーム、policy 蒸留付き) で書き出す。"
        ),
    )
    parser.add_argument('model', help="ONNX モデル")
    parser.add_argument('hcpe', help="入力 HCPE ファイル")
    parser.add_argument('out_hcpe3', help="出力 HCPE3 ファイル")
    parser.add_argument('--a', type=float, default=756.0864962951762,
                        help="value → score の係数。default=756.0864962951762")
    parser.add_argument('--batch-size', '-b', type=int, default=1024,
                        help="推論バッチサイズ (HCPE レコード単位)。default=1024")
    parser.add_argument('--tensorrt', action='store_true',
                        help="TensorRT Execution Provider を優先する。")
    args = parser.parse_args()

    providers = (
        ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']
        if args.tensorrt
        else ['CUDAExecutionProvider', 'CPUExecutionProvider']
    )
    session = onnxruntime.InferenceSession(args.model, providers=providers)

    file_size = os.path.getsize(args.hcpe)
    if file_size % HCPE_SIZE != 0:
        raise ValueError(
            f"HCPE file size {file_size} is not divisible by record size {HCPE_SIZE}"
        )
    total = file_size // HCPE_SIZE

    board = Board()

    with open(args.hcpe, 'rb') as f_in, \
         open(args.out_hcpe3, 'wb') as f_out, \
         tqdm(total=total, desc="re-eval", unit='pos') as pbar:

        while True:
            chunk = f_in.read(HCPE_SIZE * args.batch_size)
            if not chunk:
                break
            batch = np.frombuffer(chunk, HuffmanCodedPosAndEval)
            n = len(batch)

            x1 = np.empty((n, FEATURES1_NUM, 9, 9), dtype=np.float32)
            x2 = np.empty((n, FEATURES2_NUM, 9, 9), dtype=np.float32)
            # 各局面ごとの (legal moves の policy ラベル, 対応する move16) を保持
            labels_per_pos: list[np.ndarray] = []
            m16_per_pos: list[np.ndarray] = []

            for i in range(n):
                board.set_hcp(batch[i]['hcp'])
                assert board.is_ok()
                make_input_features(board, x1[i], x2[i])

                moves = list(board.legal_moves)
                if moves:
                    labels = np.fromiter(
                        (make_move_label(m, board.turn) for m in moves),
                        dtype=np.int64, count=len(moves),
                    )
                    m16s = np.fromiter(
                        (move16(m) for m in moves),
                        dtype=np.uint16, count=len(moves),
                    )
                else:
                    labels = np.empty(0, dtype=np.int64)
                    m16s = np.empty(0, dtype=np.uint16)
                labels_per_pos.append(labels)
                m16_per_pos.append(m16s)

            # ONNX 推論
            io_binding = session.io_binding()
            io_binding.bind_cpu_input('input1', x1)
            io_binding.bind_cpu_input('input2', x2)
            io_binding.bind_output('output_policy')
            io_binding.bind_output('output_value')
            session.run_with_iobinding(io_binding)
            policies, values = io_binding.copy_outputs_to_cpu()
            # policies shape: (n, POLICY_DIM)、values shape: (n, 1) or (n,)
            scores = value_to_score(values.reshape(-1), args.a)

            # 書き出し
            for i in range(n):
                hcpe = batch[i]
                labels = labels_per_pos[i]
                m16s = m16_per_pos[i]
                n_moves = len(labels)

                if n_moves > 0:
                    # 合法手の policy だけ抽出 → softmax → uint16 量子化
                    logits = policies[i, labels]
                    probs = softmax_1d(logits)
                    visits = np.clip((probs * VISIT_SCALE).astype(np.int64), 0, VISIT_SCALE).astype(np.uint16)
                else:
                    visits = np.empty(0, dtype=np.uint16)

                # HCPE3 ヘッダ
                header = np.zeros(1, dtype=HuffmanCodedPosAndEval3)[0]
                header['hcp'] = hcpe['hcp']
                header['moveNum'] = 1
                header['result'] = gameresult_to_hcpe3_result(int(hcpe['gameResult']))
                header['gameInfo'] = 0
                f_out.write(header.tobytes())

                # MoveInfo (1 件)
                mi = np.zeros(1, dtype=MoveInfo)[0]
                mi['selectedMove16'] = hcpe['bestMove16']
                mi['eval'] = int(scores[i])
                mi['candidateNum'] = n_moves
                f_out.write(mi.tobytes())

                # MoveVisits (合法手数 件)
                if n_moves > 0:
                    mv = np.empty(n_moves, dtype=MoveVisits)
                    mv['move16'] = m16s
                    mv['visitNum'] = visits
                    f_out.write(mv.tobytes())

                pbar.update(1)


if __name__ == '__main__':
    main()
