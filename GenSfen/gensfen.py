import time
import json5
import traceback
import random

from threading import Thread

from ShogiCommonLib import *

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION             = "V0.01"

# 設定ファイル
SETTING_JSON_PATH          = "settings/gensfen-settings.json5"

# 対局開始局面集のPATH(SFEN形式)
STARTPOS_SFENS_PATH        = "settings/startpos-sfens.txt"

# 対局の最大手数
MAX_PLY                    = 320

# ============================================================

# 全対局スレッドが共通で(同じものを参照で)持っている構造体
class SharedState:
    def __init__(self, settings, kif_writer:KifWriter):
        # コンストラクタで渡された設定
        self.settings = settings

        # # 棋譜保存用
        self.kif_writer = kif_writer
        
        # # 対局開始局面(互角局面集から読み込む)
        # self.root_sfens : list[Sfen] = self.read_start_sfens(settings["START_SFENS_PATH"])

        # エンジン設定
        self.engine_settings = settings["ENGINE_SETTING"]

        # gensfenするときのnodes
        self.nodes = 0

        # 対局開始局面の集合
        self.startpos_sfens : list[str] = []
        self.startpos_lock = Lock()

    def get_next_startpos_sfen(self) -> str:
        """次の対局開始局面を取得する。"""

        with self.startpos_lock:
            if not self.startpos_sfens:

                # 対局開始局面の読み込み
                print("loading startpos sfens, PATH = ", STARTPOS_SFENS_PATH)
                with open(STARTPOS_SFENS_PATH, "r", encoding="utf-8") as f:
                    startpos_sfens = [line.strip() for line in f if line.strip()]
                random.shuffle(startpos_sfens)
                startpos_sfens = startpos_sfens
                print(f"..loaded {len(startpos_sfens)} startpos sfens.")

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

        # エンジンの探索node数
        self.engine_nodes : int = 500000

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

    def start_game(self) -> bytearray:
        """1対局を開始させる"""

        # 対局棋譜の保存用
        game_data = GameDataEncoder()

        # 対局開始局面を取得
        startpos_sfen = self.shared.get_next_startpos_sfen()
        game_data.set_startsfen(startpos_sfen)

        # 対局処理
        board = cshogi.Board(startpos_sfen) # type: ignore

        while board.ply() <= MAX_PLY:

            if board.is_draw() == cshogi.REPETITION_DRAW: # type: ignore
                # 千日手引き分け
                game_data.write_game_result(0)
                game_data.write_uint8(1) # 終局理由: draw
                break

            # 現在の局面をSFEN形式で取得
            sfen = board.sfen()

            engine = self.engines[board.turn()]  # 手番側のエンジンを取得
            usi_move, eval = engine.go(sfen, self.shared.nodes)

            if usi_move == "resign":
                # 投了
                winner = board.turn() ^ 1  # 非手番側の勝ち black=0, white=1
                game_data.write_game_result(winner + 1)
                game_data.write_uint8(0) # 終局理由: resign
                break

            if usi_move == "win":
                # 入玉宣言勝ち
                winner = board.turn()  # 手番側の勝ち black=0, white=1
                game_data.write_game_result(winner + 1)
                game_data.write_uint8(10) # 終局理由: win by csa_rule24
                break

            # エンジン1の指し手を取得
            board.push_usi(usi_move)

            # 棋譜データに追加
            move = board.move_from_usi(usi_move)

            game_data.write_uint16(move)
            game_data.write_int16(eval)

            engine_num ^= 1  # 手番交代

            if self.quit:
                raise Exception("quit requested")

        else:
            # 千日手引き分け
            game_data.write_game_result(0)
            game_data.write_uint8(2) # 終局理由: draw by max moves

        return game_data.get_bytes()


    def join(self):
        """対局スレッドの終了を待つ"""

        self.quit = True
        if self.match_thread:
            self.match_thread.join()

class GameMatcher:
    """
    GameMatchを並列対局数分だけ起動して対局を開始させる。
    """
    def __init__(self, shared:"SharedState"):

        engine_settings = shared.engine_settings
        engine_threads = []
        thread_id = 0
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
                t.engine_nodes = shared.nodes
                t.thread_id = thread_id
                thread_id += 1
                threads.append(t)
                print_log(f"engine {len(threads)} : name = {t.engine_name}, path = {t.engine_path} , thread_id = {t.thread_id}")

            engine_threads.extend(threads)

        # 対局情報を格納
        self.shared         = shared
        self.engine_threads = engine_threads

        self.shogi_matches = []

    def start_games(self):
        """すべての並列対局を開始させる"""

        print_log("start games")

        # 並列対局数
        num = len(self.engine_threads)

        shogi_matches = []
        for i, t in enumerate(self.engine_threads, 1):
            print_log(f"game match No. {i}, {t.engine_path} is starting..")

            # 同じエンジンインスタンス同士で対局させる。
            shogi_match = ShogiMatch(t,t,self.shared)
            shogi_matches.append(shogi_match)

            # ここで小さなsleepがないとネットワーク越しだと、その初期化に時間がかかり、
            # networkがtime outになる可能性がある。
            time.sleep(0.3)

        self.shogi_matches = shogi_matches

        for shogi_match in self.shogi_matches:
            shogi_match.start()

        print_log("All shogi games have started. Please wait.")

    def wait_all_threads(self):
        """すべての対局スレッドの終了を待つ。"""

        print_log("Waiting for all threads to finish...")

        for shogi_match in self.shogi_matches:
            shogi_match.join()
            print(".", end='', flush=True)



# ============================================================
#                             main
# ============================================================

def user_input():
    """
    ユーザーからの入力受付。
    """

    # ログ記録を自動的に開始する。
    enable_print_log()

    print("GenSfen script start. version =", SCRIPT_VERSION)
    print("SETTING_JSON_PATH = ", SETTING_JSON_PATH)
    print("STARTPOS_SFENS_PATH = ", STARTPOS_SFENS_PATH)

    # 設定ファイルの読み込み
    with open(SETTING_JSON_PATH, "r", encoding="utf-8") as f:
        settings = json5.load(f)

    # 棋譜書き出し用のclass
    kif_writer = KifWriter()

    # これは全対局スレッドが同じものを指す。
    shared = SharedState(settings, kif_writer)

    # 並列対局管理用
    matcher = GameMatcher(shared)

    while True:
        try:
            print_log("[Q]uit [G]ame [H]elp> ", end='')
            inp = input().split()
            if not inp:
                continue
            i = inp[0].lower()

            if i == 'h':
                print_log("Help : ")
                print_log("  Q or ! : Quit")
                print_log("  G : GenSfen nodes")

            elif i == 'g':

                # default nodes
                nodes = 100000
                if len(inp) >= 2:
                    # 引数で指定されているなら、それで差し替える。
                    nodes = int(inp[1])
                shared.nodes = nodes
                print_log(f"start gensfen nodes = {nodes}")
                matcher.start_games()

            elif i == 'q' or i == '!':
                # 終了時には自動セーブ
                print_log("quit")
                break

        except Exception as e:
            print_log(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")

    # 全スレッドの終了を待つ..
    matcher.wait_all_threads()

    # 棋譜ファイルをclose
    kif_writer.close()


if __name__ == '__main__':
    user_input()

