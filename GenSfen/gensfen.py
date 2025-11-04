import time
import json5
import traceback

from ShogiCommonLib import *

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION               = "V0.01"

# 設定ファイル
SETTING_PATH                 = "settings/gensfen-settings.json5"

# ============================================================


# 全対局スレッドが共通で(同じものを参照で)持っている構造体
class SharedState:
    def __init__(self, settings):
        # コンストラクタで渡された設定
        self.settings = settings

        # # 棋譜保存用
        # self.kif_manager = KifManager()
        
        # # 対局開始局面(互角局面集から読み込む)
        # self.root_sfens : list[Sfen] = self.read_start_sfens(settings["START_SFENS_PATH"])

        # エンジン設定
        self.engine_settings = settings["ENGINE_SETTING"]

        # gensfenするときのnodes
        self.nodes = 0

        print(self.engine_settings)


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
        self.engine1 = engine1
        self.engine2 = engine2
        self.shared  = shared

        # 対局スレッド
        # self.match_thread = ShogiMatchThread(engine1, engine2, shared)

    # def start(self):
    #     """対局スレッドを開始させる"""
    #     self.match_thread.start()


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
            #     "nodes":10000,
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

    def start_games(self):
        """すべての並列対局を開始させる"""

        print_log("start games")

        # 並列対局数
        num = len(self.engine_threads) // 2

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

        # TODO : あとで書く。

        # for shogi_match in self.shogi_matches:
        #     shogi_match.start()

        print_log("All shogi games have started. Please wait.")

# ============================================================
#                             main
# ============================================================

def user_input():
    """
    ユーザーからの入力受付。
    """

    # ログ記録を自動的に開始する。
    enable_print_log()

    # 設定ファイルの読み込み
    with open(SETTING_PATH, "r", encoding="utf-8") as f:
        settings = json5.load(f)

    # これは全対局スレッドが同じものを指す。
    shared = SharedState(settings)

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


def main():
    user_input()

if __name__ == '__main__':
    main()

