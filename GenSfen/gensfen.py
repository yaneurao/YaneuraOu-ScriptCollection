import time
import json5
import traceback
import random
import threading
from tqdm import tqdm

from threading import Thread

from ShogiCommonLib import *

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION             = "V0.01"

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

        # 棋譜保存用
        self.kif_writer = KifWriter(self.nodes)
        
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
        self.engines = [Engine(engine1.engine_path,engine1.thread_id), Engine(engine2.engine_path,engine2.thread_id)] 

        for engine in self.engines:
            engine.send_usi(f"multipv 1")
            engine.isready()

        self.shared  = shared
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
                self.shared.kif_writer.write_game(kif)

        except Exception as e:
            # quitするときの例外ではないならそれを出力する。
            if not self.quit:
                print_log(f"Exception in game between {self.engine_settings[0].engine_name} and {self.engine_settings[1].engine_name} : {type(e).__name__}{e}\n{traceback.format_exc()}")

        # print_log(f"Game end between {self.engine1.engine_name} and {self.engine2.engine_name}")

    def start_game(self) -> GameDataEncoder:
        """
        1対局を開始させる
        """

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
                print_log(f"Start GenSfen, NODES = {shared.nodes}, MAX_GAME_PLY = {shared.max_game_ply}")
                matcher.start_games()

            elif i == 'q' or i == '!':
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

    # 棋譜ファイルをclose
    shared.kif_writer.close()


if __name__ == '__main__':
    user_input()

