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
import sys
from pathlib import Path


def _add_nvidia_dll_dirs() -> None:
    """
    Windows で `pip install nvidia-cudnn-cu12` などで cuDNN / cuBLAS を入れた場合、
    DLL は `...\\site-packages\\nvidia\\<lib>\\bin\\` に置かれるが、これは Windows の
    既定 DLL 検索パスに入らないため、ONNX Runtime がロードできずに `cudnn64_9.dll
    is missing` 系のエラーになる。

    onnxruntime を import する前に、pip インストール済みの nvidia.* パッケージの
    bin ディレクトリを os.add_dll_directory で登録しておく。
    """
    if sys.platform != 'win32':
        return
    for mod_name in ('cudnn', 'cublas', 'cuda_runtime', 'cuda_nvrtc'):
        try:
            m = __import__('nvidia.' + mod_name, fromlist=['*'])
        except ImportError:
            continue
        bin_dir = os.path.join(os.path.dirname(m.__file__), 'bin')
        if not os.path.isdir(bin_dir):
            continue
        # 1) %PATH% の先頭に挿入。ONNX Runtime の依存 DLL (例: cudnn64_9.dll) を
        #    LoadLibraryW で解決する経路はこちらしか効かないため必須。
        path_parts = os.environ.get('PATH', '').split(os.pathsep)
        if bin_dir not in path_parts:
            os.environ['PATH'] = bin_dir + os.pathsep + os.environ.get('PATH', '')
        # 2) os.add_dll_directory も登録 (LOAD_LIBRARY_SEARCH_USER_DIRS 経路向け)。
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(bin_dir)
            except (FileNotFoundError, OSError):
                pass


_add_nvidia_dll_dirs()


import numpy as np
import onnxruntime
from tqdm import tqdm

from cshogi import (
    Board,
    move16,
)
from cshogi.dlshogi import (
    make_input_features, make_move_label,
    FEATURES1_NUM, FEATURES2_NUM,
)

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherFormatLib import (  # noqa: E402
    HCPE,
    HCPE_SIZE,
    HCPE3_HEADER,
    MOVE_INFO,
    MOVE_VISITS,
    hcpe_game_result_to_hcpe3_result,
    validate_fixed_record_file,
)

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
    parser.add_argument('--top-k', type=int, default=8,
                        help="MoveVisits に書き出す候補手数。policy 上位 K 手だけを softmax → uint16 量子化して書く。合法手が K より少ない局面ではその全合法手。default=8")
    parser.add_argument('--tensorrt', action='store_true',
                        help="TensorRT Execution Provider を優先する。")
    args = parser.parse_args()
    if args.top_k <= 0:
        raise ValueError("--top-k must be positive")

    providers = (
        ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']
        if args.tensorrt
        else ['CUDAExecutionProvider', 'CPUExecutionProvider']
    )
    session = onnxruntime.InferenceSession(args.model, providers=providers)

    total = validate_fixed_record_file(Path(args.hcpe), HCPE_SIZE, "HCPE")

    board = Board()

    with open(args.hcpe, 'rb') as f_in, \
         open(args.out_hcpe3, 'wb') as f_out, \
         tqdm(total=total, desc="re-eval", unit='pos') as pbar:

        while True:
            chunk = f_in.read(HCPE_SIZE * args.batch_size)
            if not chunk:
                break
            batch = np.frombuffer(chunk, HCPE)
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
                    logits = policies[i, labels]
                    # policy 上位 top_k 手を確率降順で抽出。合法手が top_k より少なければ全合法手。
                    if n_moves <= args.top_k:
                        order = np.argsort(-logits)
                    else:
                        idx = np.argpartition(-logits, args.top_k - 1)[:args.top_k]
                        order = idx[np.argsort(-logits[idx])]
                    sel_logits = logits[order]
                    sel_m16s = m16s[order]
                    # 抽出後の logit だけで softmax → uint16 量子化 (top-k 内で確率が合計 1 になる)
                    probs = softmax_1d(sel_logits)
                    visits = np.clip((probs * VISIT_SCALE).astype(np.int64), 0, VISIT_SCALE).astype(np.uint16)
                    n_kept = len(order)
                else:
                    sel_m16s = np.empty(0, dtype=np.uint16)
                    visits = np.empty(0, dtype=np.uint16)
                    n_kept = 0

                # HCPE3 ヘッダ
                header = np.zeros(1, dtype=HCPE3_HEADER)[0]
                header['hcp'] = hcpe['hcp']
                header['moveNum'] = 1
                header['result'] = hcpe_game_result_to_hcpe3_result(int(hcpe['gameResult']))
                header['gameInfo'] = 0
                f_out.write(header.tobytes())

                # MoveInfo (1 件)
                mi = np.zeros(1, dtype=MOVE_INFO)[0]
                mi['selectedMove16'] = hcpe['bestMove16']
                mi['eval'] = int(scores[i])
                mi['candidateNum'] = n_kept
                f_out.write(mi.tobytes())

                # MoveVisits (n_kept 件)
                if n_kept > 0:
                    mv = np.empty(n_kept, dtype=MOVE_VISITS)
                    mv['move16'] = sel_m16s
                    mv['visitNum'] = visits
                    f_out.write(mv.tobytes())

                pbar.update(1)


if __name__ == '__main__':
    main()
