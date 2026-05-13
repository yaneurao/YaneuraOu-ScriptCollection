import time
import json5
import traceback
import random
import threading
import sys
from pathlib import Path
from tqdm import tqdm

from threading import Thread

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneShogiLib import *

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION             = "V0.03"

# 設定ファイル
SETTING_JSON_PATH          = "settings/gensfen-settings.json5"

# プログレスバーのフォーマット
BAR_FORMAT = "{desc:<15}: {percentage:3.0f}%|{bar:40}| {n_fmt}/{total_fmt}"

# ============================================================

# 全対局スレッドが共通で(同じものを参照で)持っている構造体
class SharedState:
    def __init__(self, settings):
        # コンストラクタで渡された設定
        self.settings = settings

        # 最大手数(これを超えると引き分けになる)
        self.max_game_ply = settings["MAX_GAME_PLY"]

        # 教師生成の1局面あたりの探索ノード数
        self.nodes = settings["NODES"]

        # 出力形式
        #   pack  : 従来形式。../teacher/pack2hcpe.pyでHCPEへ変換する。
        #   hcpe3 : MultiPVから疑似訪問回数を作り、HCPE3を直接出力する。
        self.output_format = str(settings.get("OUTPUT_FORMAT", "pack")).lower()
        if self.output_format not in ["pack", "hcpe3"]:
            raise Exception(f"Unknown OUTPUT_FORMAT: {self.output_format}")

        # HCPE3出力用設定
        self.multipv = max(1, int(settings.get("MULTIPV", 4 if self.output_format == "hcpe3" else 1)))
        if self.output_format == "pack":
            self.multipv = 1
        self.hcpe3_visits_sum = max(1, int(settings.get("HCPE3_VISITS_SUM", 65535)))
        self.hcpe3_temperature = float(settings.get("HCPE3_TEMPERATURE", 100.0))
        self.hcpe3_eval_drop_threshold = int(settings.get("HCPE3_EVAL_DROP_THRESHOLD", 500))
        self.hcpe3_mate_score = int(settings.get("HCPE3_MATE_SCORE", VALUE_MATE))
        self.hcpe3_resign_eval = settings.get("HCPE3_RESIGN_EVAL", None)
        if self.hcpe3_resign_eval is not None:
            self.hcpe3_resign_eval = int(self.hcpe3_resign_eval)

        # 教師保存用
        if self.output_format == "hcpe3":
            self.teacher_writer = Hcpe3Writer(self.nodes)
        else:
            self.teacher_writer = KifWriter(self.nodes)
        self.kif_writer = self.teacher_writer
        
        # # 対局開始局面(互角局面集から読み込む)
        # self.root_sfens : list[Sfen] = self.read_start_sfens(settings["START_SFENS_PATH"])

        # エンジン設定
        self.engine_settings = settings["ENGINE_SETTING"]

        # 対局開始局面の集合
        self.startpos_sfens : list[str] = []
        self.startpos_lock = Lock()

        # pauseの設定
        # これがTrueだと生成を一時的にpauseする。
        self.pause_event = threading.Event()
        self.pause_event.set()

    def read_startpos_sfens(self)->list[str]:
        # 対局開始局面を読み込む。

        file_path = self.settings["START_SFENS_PATH"]

        # 対局開始局面の読み込み
        print_log(f"\nloading startpos sfens, PATH = {file_path}")

        # ファイルサイズを取得して、進捗を出力する。
        file_size = os.path.getsize(file_path)
        startpos_sfens = []

        # ★ 進捗の出力のためにtell()を使いたいので、テキストではなくバイナリで開く
        with open(file_path, "rb") as fb:
            pbar = tqdm(total=file_size, unit='B', unit_scale=True, desc=f"{'Read Sfens':<12}", ncols=80, bar_format=BAR_FORMAT)

            for raw_line in fb:  # raw_line は bytes
                pos = fb.tell()
                pbar.update(pos - pbar.n)

                line = raw_line.decode("utf-8").strip()
                if line:
                    startpos_sfens.append(line)

            pbar.close()

        print_log("\n..random shuffling")
        random.shuffle(startpos_sfens)

        print_log(f"..loaded {len(startpos_sfens)} startpos sfens.")
        if not startpos_sfens:
            raise Exception("No startpos sfens loaded.")

        return startpos_sfens


    def get_next_startpos_sfen(self) -> str:
        """
        次の対局開始局面を取得する。
        position文字列が返る。
        """

        with self.startpos_lock:
            if not self.startpos_sfens:
                self.startpos_sfens = self.read_startpos_sfens()

            # ひとつpopして返す。
            sfen = self.startpos_sfens.pop()

            return sfen


class EngineSettings:
    '''探索スレッド固有の設定を集めた構造体'''

    def __init__(self):

        # エンジンのpath
        self.engine_path : str = ""

        # エンジンの表示名
        self.engine_name : str = ""

        # エンジンが開始できる状態になったのか？
        self.readyok : bool = False

        # スレッドid
        self.thread_id : int = 0


class ShogiMatch:
    """
    1対局分のエンジン同士の対局を管理するクラス。
    """
    def __init__(self, engine1:EngineSettings, engine2:EngineSettings, shared:"SharedState"):

        self.engine_settings = [engine1, engine2]
        self.shared  = shared
        self.engines = [Engine(engine1.engine_path,engine1.thread_id), Engine(engine2.engine_path,engine2.thread_id)] 

        for engine in self.engines:
            engine.send_usi(f"setoption name MultiPV value {self.shared.multipv}")
            engine.isready()

        self.quit = False

        # 対局スレッド
        self.match_thread = None

    def start(self):
        """対局スレッドを開始させる"""

        self.match_thread = Thread(target=self.thread_worker)
        self.match_thread.start()

    def thread_worker(self):
        """対局スレッドのメインループ"""

        # 対局開始
        # print_log(f"Game start between {self.engine_settings[0].engine_name} and {self.engine_settings[1].engine_name}")

        try:
            while True:
                # 対局処理1回分。
                kif = self.start_game()
                self.shared.teacher_writer.write_game(kif)

        except Exception as e:
            # quitするときの例外ではないならそれを出力する。
            if not self.quit:
                print_log(f"Exception in game between {self.engine_settings[0].engine_name} and {self.engine_settings[1].engine_name} : {type(e).__name__}{e}\n{traceback.format_exc()}")

        # print_log(f"Game end between {self.engine1.engine_name} and {self.engine2.engine_name}")

    def start_game(self):
        """
        1対局を開始させる
        """
        if self.shared.output_format == "hcpe3":
            return self.start_game_hcpe3()

        # 対局棋譜の保存用
        game_data = GameDataEncoder()

        # 対局開始局面を取得
        try:
            startpos_sfen = self.shared.get_next_startpos_sfen()
            game_data.set_startsfen(startpos_sfen)
            board = game_data.board

        except Exception as e:
            print_log(f"Exception : {e}")
            return game_data

        # 対局前の初期化
        for engine in self.engines:
            engine.send_usi('usinewgame')

        while board.move_number <= self.shared.max_game_ply:

            # pauseの処理(手抜き)
            self.shared.pause_event.wait()

            if board.is_draw() == cshogi.REPETITION_DRAW: # type: ignore
                # 千日手引き分け
                game_data.write_game_result(0)
                game_data.write_uint8(1) # 終局理由: draw
                break

            # 現在の局面をSFEN形式で取得
            sfen = board.sfen()

            engine = self.engines[board.turn]  # 手番側のエンジンを取得
            usi_move, eval_int = engine.go(sfen, self.shared.nodes)

            if usi_move == "resign":
                # 投了
                winner = board.turn ^ 1  # 非手番側の勝ち black=0, white=1
                game_data.write_game_result(winner + 1)
                game_data.write_uint8(0) # 終局理由: resign
                break

            if usi_move == "win":
                # 入玉宣言勝ち
                winner = board.turn  # 手番側の勝ち black=0, white=1
                game_data.write_game_result(winner + 1)
                game_data.write_uint8(10) # 終局理由: win by csa_rule24
                break

            # 指し手文字列をAperyのmove16形式に変換
            move = board.move_from_usi(usi_move) & 0xffff

            # 棋譜データに追加
            game_data.write_uint16(move)
            game_data.write_eval(eval_int)

            # エンジンの指し手で局面を進める
            board.push_usi(usi_move)

            if self.quit:
                raise Exception("quit requested")

        else:
            # 千日手引き分け
            game_data.write_game_result(0)
            game_data.write_uint8(2) # 終局理由: draw by max moves

        return game_data

    def hcpe3_result_from_winner(self, winner:int)->int:
        if winner == BLACK:
            return HCPE3_BLACK_WIN
        if winner == WHITE:
            return HCPE3_WHITE_WIN
        return HCPE3_DRAW

    def make_hcpe3_candidate_visits(
        self,
        board,
        selected_move:int,
        selected_eval:int,
        multipv_candidates:list[tuple[Move, Eval]],
    )->tuple[int, list[tuple[int, int]]]:
        """
        USI MultiPV候補をHCPE3のMoveVisitsへ変換する。
        """
        candidates : list[tuple[int, int]] = []
        seen_moves = set()

        for usi_move, score in multipv_candidates:
            try:
                move = board.move_from_usi(usi_move)
            except Exception:
                continue

            if move in seen_moves or not board.is_legal(move):
                continue

            seen_moves.add(move)
            candidates.append((move, score))

        if selected_move not in seen_moves:
            fallback_score = candidates[0][1] if candidates else selected_eval
            candidates.insert(0, (selected_move, fallback_score))
            seen_moves.add(selected_move)

        if len(candidates) > self.shared.multipv:
            truncated = candidates[:self.shared.multipv]
            if selected_move not in [move for move, _score in truncated]:
                selected_candidate = next(
                    ((move, score) for move, score in candidates if move == selected_move),
                    (selected_move, selected_eval),
                )
                truncated = truncated[:self.shared.multipv - 1] + [selected_candidate]
            candidates = truncated

        if self.shared.hcpe3_eval_drop_threshold >= 0 and candidates:
            best_score = max(score for _move, score in candidates)
            filtered = [
                (move, score)
                for move, score in candidates
                if best_score - score <= self.shared.hcpe3_eval_drop_threshold or move == selected_move
            ]
            if filtered:
                candidates = filtered

        scores = [score for _move, score in candidates]
        visits = visits_from_scores(scores, self.shared.hcpe3_visits_sum, self.shared.hcpe3_temperature)
        candidate_visits = [(move & 0xffff, visit) for (move, _score), visit in zip(candidates, visits)]

        for move, score in candidates:
            if move == selected_move:
                selected_eval = score
                break

        return selected_eval, candidate_visits

    def start_game_hcpe3(self) -> Hcpe3GameData:
        """
        1対局を開始させ、HCPE3 1局分のデータを返す。
        """

        game_data = Hcpe3GameData()

        try:
            startpos_sfen = self.shared.get_next_startpos_sfen()
            board = board_from_position_string(startpos_sfen)
            game_data = Hcpe3GameData(board_to_hcp_bytes(board))

        except Exception as e:
            print_log(f"Exception : {e}")
            return game_data

        for engine in self.engines:
            engine.send_usi('usinewgame')

        while board.move_number <= self.shared.max_game_ply:

            self.shared.pause_event.wait()

            if board.is_draw() == cshogi.REPETITION_DRAW: # type: ignore
                game_data.set_result(HCPE3_DRAW, HCPE3_RESULT_REPETITION)
                break

            sfen = board.sfen()
            engine = self.engines[board.turn]
            usi_move, eval_int, multipv_candidates = engine.go_multipv(
                sfen,
                self.shared.nodes,
                self.shared.hcpe3_mate_score,
            )

            if usi_move == "resign":
                winner = board.turn ^ 1
                game_data.set_result(self.hcpe3_result_from_winner(winner))
                break

            if usi_move == "win":
                winner = board.turn
                game_data.set_result(self.hcpe3_result_from_winner(winner), HCPE3_RESULT_NYUGYOKU)
                break

            try:
                selected_move = board.move_from_usi(usi_move)
            except Exception:
                winner = board.turn ^ 1
                game_data.set_result(self.hcpe3_result_from_winner(winner))
                break

            if not board.is_legal(selected_move):
                winner = board.turn ^ 1
                game_data.set_result(self.hcpe3_result_from_winner(winner))
                break

            selected_eval, candidate_visits = self.make_hcpe3_candidate_visits(
                board,
                selected_move,
                eval_int,
                multipv_candidates,
            )
            game_data.add_record(selected_move & 0xffff, selected_eval, candidate_visits)

            mover = board.turn
            board.push_usi(usi_move)

            if self.shared.hcpe3_resign_eval is not None and selected_eval <= -abs(self.shared.hcpe3_resign_eval):
                winner = mover ^ 1
                game_data.set_result(self.hcpe3_result_from_winner(winner))
                break

            if self.quit:
                raise Exception("quit requested")

        else:
            game_data.set_result(HCPE3_DRAW, HCPE3_RESULT_MAX_MOVES)

        return game_data


    def join(self):
        """対局スレッドの終了を待つ"""

        self.quit = True
        if self.match_thread:
            self.match_thread.join()
            self.match_thread = None

class GameMatcher:
    """
    GameMatchを並列対局数分だけ起動して対局を開始させる。
    """
    def __init__(self, shared:"SharedState"):

        engine_settings = shared.engine_settings
        engine_threads = []
        thread_id = 0

        max_instance = sum(engine_setting["multi"] for engine_setting in engine_settings)
        pbar = tqdm(total=max_instance, desc=f"{'Launching':<12}", ncols=80, bar_format=BAR_FORMAT)

        for engine_setting in engine_settings:
            threads = []

            # {
            #     "path":"D:/doc/VSCodeProject/YaneuraOu/ShogiBookMiner2025/engines/suisho10/YO860kai_AVX2.exe",
            #     "name":"suisho10",
            #     "multi":32 // 32個起動する。
            # },

            for _ in range(engine_setting["multi"]):
                t = EngineSettings()
                t.engine_path  = engine_setting["path"] 
                t.engine_name  = engine_setting["name"]
                t.thread_id = thread_id
                thread_id += 1
                threads.append(t)

                # print_log(f"engine {len(threads)} : name = {t.engine_name}, path = {t.engine_path} , thread_id = {t.thread_id}")
                pbar.update()

            engine_threads.extend(threads)

        # 対局情報を格納
        self.shared         = shared
        self.engine_threads = engine_threads

        self.shogi_matches = []

    def start_games(self):
        """すべての並列対局を開始させる"""

        # 並列対局数
        num = len(self.engine_threads)

        shogi_matches = []

        max_instances = len(self.engine_threads)
        pbar = tqdm(total=max_instances, desc=f"{'Game Match':<12}", ncols=80, bar_format=BAR_FORMAT)

        for i, t in enumerate(self.engine_threads, 1):
            # print_log(f"game match No. {i}, {t.engine_path} is starting..")

            # 同じエンジンインスタンス同士で対局させる。
            shogi_match = ShogiMatch(t,t,self.shared)
            shogi_matches.append(shogi_match)

            # ここで小さなsleepがないとネットワーク越しだと、その初期化に時間がかかり、
            # networkがtime outになる可能性がある。
            # time.sleep(0.3)
            # → ShogiMatchで`readyok`待ってるから大丈夫か…。

            pbar.update()

        self.shogi_matches = shogi_matches

        for shogi_match in self.shogi_matches:
            shogi_match.start()

        print_log("\nAll shogi games have started. Please wait.")

    def wait_all_threads(self):
        """すべての対局スレッドの終了を待つ。"""

        print_log("\nWaiting for all threads to finish...")

        with tqdm(total=len(self.shogi_matches), desc=f"{'Join':<12}", ncols=80, bar_format=BAR_FORMAT) as pbar:
            for shogi_match in self.shogi_matches:
                shogi_match.join()
                pbar.update()

        print()


# ============================================================
#                             main
# ============================================================

def user_input():
    """
    ユーザーからの入力受付。
    """

    # ログ記録を自動的に開始する。
    enable_print_log()

    print_log(f"GenSfen Script, Version = {SCRIPT_VERSION}")
    print_log(f"Loading setting JSON, SETTING_JSON_PATH = {SETTING_JSON_PATH}")

    # 設定ファイルの読み込み
    with open(SETTING_JSON_PATH, "r", encoding="utf-8") as f:
        settings = json5.load(f)

    # これは全対局スレッドが同じものを指す。
    shared = SharedState(settings)

    # 並列対局管理用
    matcher = GameMatcher(shared)

    while True:
        try:
            print_log("[Q]uit [G]ensfen [P]ause [H]elp> ", end='')
            inp = input().split()
            if not inp:
                continue
            i = inp[0].lower()

            if i == 'h':
                print_log("Help : ")
                print_log("  Q or ! : Quit")
                print_log("  G : GenSfen [nodes]")
                print_log("  P : Pause")

            elif i == 'g':
                # まだ対局が組まれていなければ開始する。
                if not matcher.shogi_matches:
                    print_log(f"Start GenSfen, OUTPUT_FORMAT = {shared.output_format}, NODES = {shared.nodes}, MAX_GAME_PLY = {shared.max_game_ply}, MULTIPV = {shared.multipv}")
                    matcher.start_games()

            elif i == 'q' or i == '!':
                # pause解除
                if not shared.pause_event.is_set():
                    shared.pause_event.set()     # resume にする
                    print_log("Resumed")

                # 終了時には自動セーブ
                print_log("quit")
                break

            elif i == 'p':
                if shared.pause_event.is_set():
                    shared.pause_event.clear()   # pause にする
                    print_log("Paused")
                else:
                    shared.pause_event.set()     # resume にする
                    print_log("Resumed")

        except Exception as e:
            print_log(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")

    # 全スレッドの終了を待つ..
    matcher.wait_all_threads()

    # 教師ファイルをclose
    shared.teacher_writer.close()


if __name__ == '__main__':
    user_input()
