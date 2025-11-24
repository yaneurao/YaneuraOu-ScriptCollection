import os
import math
import time
import json5
import traceback
from dataclasses import dataclass, field
from threading import Thread

from ShogiCommonLib import *
from ParamLib import *

# SPSAするための対局スクリプト

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION               = "V0.02"

# 設定ファイル
SETTING_PATH                 = "settings/SPSA-settings.json5"

# レート差出力は何局に1回か
RATE_OUTPUT_INTERVAL         = 10

# 熱温度。パラメーターの移動のしやすさ。勾配を加算するときの係数。
# 大きな値(10～200)から、徐々に1.0に近づけていく。
# 'm'コマンドでこの値を変更できる。
MOBILITY                     = 1.0

# パラメーターから勾配を出すときに、方向ベクトルの何倍先を見るか。
# 's'コマンドで変更できる。大きな値(2.0～3.0)から徐々に1.0に近づけていく。
# 💡 SCALE を 0にすると、元のパラメーターで対戦することになる。元のパラメーターで
#    基準ソフトとのR差を計測したい時に用いる。
SCALE                        = 1.0

# 📓  m = 100.0 , s = 2.0 ぐらいでスタートして徐々に小さくするのがいいと思う。
#    sを大きくすると大きくパラメーターを動かしたもので対局させるため、見かけのRはかなり下がることに注意。

# 対局結果の出力の列数(1～N)
# 💡 一時的に 大きくして、直近に近い勝率を確認したりできる。
RESULT_TABLE_COLS            = 4

# ============================================================
#                         Game Match
# ============================================================

class WinManager:
    def __init__(self):
        # 勝ったプレイヤーの履歴 0..player0の勝ち、1..player1の勝ち、2..draw。
        self.win_count : list[int] = []

        # ↑を書き換える時のlock
        self.lock = Lock()
    
    def update(self, winner:int):
        with self.lock:
            self.win_count.append(winner)

            # 途中経過の表示用に勝敗を1文字で出力してやる。
            # print("LWD"[winner], end="")

            # 総対局回数
            total = len(self.win_count)

            if total % RATE_OUTPUT_INTERVAL == 0:
                
                summary = []

                # 直近nの勝率、レート差などを出力。
                n = RATE_OUTPUT_INTERVAL
                while len(self.win_count) >= n:
                    # n が RATE_OUTPUT_INTERVAL × 2^m である

                    win  = self.win_count[-n:].count(1) # player 1の勝利回数
                    lose = self.win_count[-n:].count(0) # player 0の勝利回数
                    draw = self.win_count[-n:].count(2)

                    if win + lose == 0:
                        win_rate = "?"
                        rate_diff = "?"
                    else:
                        win_rate = win / (win + lose)
                        if win_rate == 0:
                            rate_diff = "-INF"
                        elif win_rate == 1:
                            rate_diff = "+INF"
                        else:
                            rate_diff = f"{-400 * math.log10(1 / win_rate - 1):.1f}"
                            win_rate  = f"{win_rate:.3f}"

                    # Last N win-draw-lose 勝率 R差
                    summary.append(f"{n} {win}-{draw}-{lose}, {win_rate}, R{rate_diff}")

                    # レートだけ表示用に積むか。
                    # summary.append(f"R{rate_diff}")

                    n *= 2

                # '|' 区切りでNの降順で直近 RESULT_TABLE_COLS つ出力
                summary.reverse()
                print_log(f"{total} : {' | '.join(summary[:RESULT_TABLE_COLS])}")

class GameMatcher:
    """
    GameMatchを並列対局数分だけ起動して対局を開始させる。
    """
    def __init__(self, shared:"SharedState"):

        engine_settings = shared.engine_settings
        threads_all = []
        thread_id = 0
        for i, engine_setting in enumerate(engine_settings):
            threads = []
            for e in engine_setting:
                # {
                #     "path":"D:/doc/VSCodeProject/YaneuraOu/ShogiBookMiner2025/engines/suisho10/YO860kai_AVX2.exe",
                #     "name":"suisho10",
                #     "nodes":10000,
                #     "multi":32 // 32個起動する。
                # },
                for _ in range(e["multi"]):
                    t = EngineSettings()
                    t.engine_path  = e["path"] 
                    t.engine_name  = e["name"]
                    t.engine_nodes = e["nodes"]
                    t.thread_id = thread_id
                    thread_id += 1
                    threads.append(t)
                    print_log(f"player {i}, engine {len(threads)} : name = {t.engine_name}, path = {t.engine_path} , nodes = {t.engine_nodes}, thread_id = {t.thread_id}")
            threads_all.append(threads)

        if len(threads_all[0]) != len(threads_all[1]):
            print_log(f"Warnging! : Number of engines mismatch, {len(threads_all[0])} != {len(threads_all[1])}")

        # 対局情報を格納
        self.shared      = shared
        self.threads_all = threads_all

    def start_games(self):
        """すべての並列対局を開始させる"""

        print("start games")

        # 並列対局数
        num = len(self.threads_all[0])

        shogi_matches = []
        for i, (t1, t2) in enumerate(zip(self.threads_all[0], self.threads_all[1])):
            print(f"game match No. {i}, {t1.engine_path} VS {t2.engine_path} is starting..")
            shogi_match = ShogiMatch(t1,t2,self.shared)
            shogi_matches.append(shogi_match)

            # ここで小さなsleepがないとネットワーク越しだと、その初期化に時間がかかり、
            # networkがtime outになる可能性がある。
            time.sleep(0.3)

        self.shogi_matches = shogi_matches

        for shogi_match in self.shogi_matches:
            shogi_match.start()

        print("All shogi games have started. Please wait.")


# 全対局スレッドが共通で(同じものを参照で)持っている構造体
class SharedState:
    def __init__(self, settings):
        # コンストラクタで渡された設定
        self.settings = settings

        # 棋譜保存用
        self.kif_manager = KifManager()
        
        # 対局開始局面(互角局面集から読み込む)
        self.root_sfens : list[Sfen] = self.read_start_sfens(settings["START_SFENS_PATH"])

        # エンジン設定
        self.engine_settings = self.read_engine_settings(settings["ENGINE_SETTINGS"])

        # パラメーターファイル
        self.parameters : list[Entry] = read_parameters(settings["PARAMETERS_PATH"])
        # ↑のvを書き換える時用のlock object
        self.param_lock = Lock()

        # 勝率マネージャー
        self.win_manager = WinManager()

        # 標準的な将棋盤なのか
        self.standard_board = self.settings["STANDARD_BOARD"]


    def read_start_sfens(self, path:str)->list[Sfen]:
        # 対局開始局面を読み込む。

        print_log(f"Read start sfens, path = {path}")
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        print_log(f"..done. {len(lines)} positions.")
        return lines

    def read_engine_settings(self, paths:list[str])->list[Any]:
        # エンジン定義ファイルを読み込む。

        engine_settings = []
        for engine_settings_path in paths:
            print_log(f"Read engine settings, path = {engine_settings_path}")
            with open(engine_settings_path, 'r', encoding='utf-8') as f:
                engine_settings.append(json5.load(f))
        return engine_settings

    def write_parameters(self):
        # パラメーターを元のファイルを書き出す。
        if self.parameters is None:
            return

        with self.param_lock:
            write_parameters(self.settings["PARAMETERS_PATH"], self.parameters)

    def print_parameters(self):
        # 現在のパラメーターを出力する。
        if self.parameters is None:
            return

        for param in self.parameters:
            print_log(f"{param.name} {param.v:.5f} [{param.min}, {param.max}]")


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


# 対局スレッド
class ShogiMatch:
    def __init__(self, t1 : EngineSettings, t2 : EngineSettings, shared : SharedState):
        self.t : list[EngineSettings]= [t1, t2]
        self.shared = shared

        engine1 = Engine(t1.engine_path, t1.thread_id)
        engine2 = Engine(t2.engine_path, t2.thread_id)
        self.engines = [engine1, engine2]

        # ゲームが終了したのか？
        self.gameover = False

    def start(self):
        """
        threadを生成して対局を開始する。
        """
        self.thread = Thread(target=self.game, daemon=True)
        self.thread.start() # 対局開始

    def game(self):
        """
        対局用worker。
        """
        try:

            # 開始局面で先に着手するplayer(開始局面が先手の局面とは限らないのでこの書き方で)
            start_player = rand(2)

            # alias of params
            params = self.shared.parameters

            # 試合結果に対してplayer nが勝った時の変位量(⚠ drawのときはn==2)
            winner_to_step = [-1.0 , +1.0 , 0]

            # 連続対局させる。
            # SPSAのために 現在のパラメーター P に対して、微小な方向 C と その逆方向 -C で対局させる。
            while True:

                # 変異させたパラメーターを取得
                shift = self.generate_shift_params(params)
                p_shift_plus  = self.add_params(params, shift, +SCALE)
                p_shift_minus = self.add_params(params, shift, -SCALE)

                # 変異させたパラメーターを思考エンジンに設定
                self.set_engine_options(params, p_shift_plus)

                # 対局
                winner = self.game_play(start_player)

                # 勝ち数のカウント
                self.shared.win_manager.update(winner)

                step = winner_to_step[winner] * +1.0

                # 次の対局の手番を入れ替える。
                start_player ^= 1

                # 逆方向に変異させたパラメーターを思考エンジンに設定
                self.set_engine_options(params, p_shift_minus)

                winner = self.game_play(start_player)
                self.shared.win_manager.update(winner)
                step += winner_to_step[winner] * -1.0

                # パラメーターをshift(方角)×step分だけ変異させる。
                # / 2は中心差分近似のときに出てくる 2。
                self.add_grad(params, shift, step / 2)

                # 次の対局の手番を入れ替える。
                start_player ^= 1

        except Exception as e:
            print_log(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")
            # この時のパラメーターを出力してやる。パラメーターの条件がillegalで落ちたのかもしれないので。
            self.shared.print_parameters()


    def game_play(self, start_player : int):
        """ 1局だけ対局する """

        # 対局開始前のisready送信
        # (パラメーターが変更になったかも知れないので毎回`isready`で初期化)
        for engine in self.engines:
            engine.isready()

        board = Board() if self.shared.standard_board else NonStandardBoard()

        # 対局開始局面(互角局面集からランダム)
        root_sfen = self.shared.root_sfens[rand(len(self.shared.root_sfens))].rstrip()

        board.set_position(root_sfen)

        # 現在の手数
        ply = board.ply()

        kif = f"{root_sfen}{'' if 'moves' in root_sfen else ' moves'}"

        # start_color : 手番のあるプレイヤー番号
        # player : 現在の手番側のプレイヤー番号
        player = start_player

        # 試合の結果 0 : 先手勝ち , 1 : 後手勝ち , 2 : 引き分け
        winner : int = -1

        while True:
            nodes = self.t[player].engine_nodes
            sfen = board.sfen()
            bestmove,_ = self.engines[player].go(sfen, nodes)

            # print(nodes)

            # 相手番にする。
            player ^= 1
            
            # 定跡込みで240手を超えたら引き分け扱いでいいや。
            ply += 1
            if ply >= 240 or board.is_draw() == 1: # REPETITION_DRAW:
                bestmove = "draw"
                winner = 2

            if bestmove == "resign":
                # 投了。勝ったのは、相手番(すでにそうなっている)
                winner = player
            elif bestmove == "win":
                # 宣言勝ち。勝ったのは、元の手番側のプレイヤー
                winner = player ^ 1

            kif += ' ' + bestmove

            if bestmove == "draw" or bestmove == "resign" or bestmove == "win":
                break

            board.push_usi(bestmove)

            # print(bestmove)

        # print_log(f"game end, winner = {winner}")

        # 対局棋譜は1局ずつ書き出す。
        # (sfen局面の方は書き出そうにも重複を除去しないといけないのでこのタイミングでは書き出さない)
        self.shared.kif_manager.write_kif(kif)

        return winner
    
    def generate_shift_params(self, params:list[Entry]):
        # -1と1の二値変数を1/2の確率でとる(Rademacher分布)
        # ここに、各要素にstepを掛け算(アダマール積)した分だけパラメーターを動かして対局させる。
        return [random.choice([param.step, -param.step]) for param in params]

    def add_params(self, params:list[Entry], shift:list[float], k:float)->list[float]:
        # params + shift * k を返す。
        # min,maxで制限する。
        v_result = []
        for param,s in zip(params, shift):
            # vにこのstep * kを加算すると、min,maxの範囲を超えてしまうなら、抑制する。
            v = param.v + s * k
            v = min(param.max, v)
            v = max(param.min, v)
            v_result.append(v)
        return v_result
    
    def set_engine_options(self, params:list[Entry], p:list[float]):
        # 思考エンジンに、変異したパラメーターを設定する
        for param, v in zip(params, p):
            # print(f"{param.name} = {v}")

            # 未使用パラメーターであるならskip
            if param.not_used:
                continue

            if param.type == "int":
                v = int(v + 0.5) # 丸め処理を入れておく。(±0.5増えたら隣の値になって欲しいので)

            # 送信する思考エンジンは[0]は基準エンジンだから、engines[1]固定でいいや。
            self.engines[1].send_usi(f"setoption name {param.name} value {v}")

    def add_grad(self, params:list[Entry], shift:list[float], step:float):
        # パラメーターを勾配分だけ変異させる。

        with self.shared.param_lock:
            for param,s in zip(params, shift):
                if param.not_used:
                    continue

                # 変異させる方向はs*step。この方向に、param.delta分だけ変異させる。
                # sは元はparam.stepに-1か1を乗算したものだから、結局、param.step * param.delta分だけ +1 , -1倍したところに移動させる意味。
                delta = s * step * param.delta * MOBILITY
                # last_v = param.v
                v = param.v + delta
                v = min(param.max, v)
                v = max(param.min, v)
                param.v = v
                # print(f"param {param.name} : {last_v} -> {v}")

# ============================================================
#                             main
# ============================================================

def user_input():
    """
    ユーザーからの入力受付。
    """

    global SCALE, MOBILITY, RESULT_TABLE_COLS

    # 設定ファイルの読み込み
    with open(SETTING_PATH, "r", encoding="utf-8") as f:
        settings = json5.load(f)

    # これは全対局スレッドが同じものを指す。
    shared = SharedState(settings)

    # 並列対局管理用
    matcher = GameMatcher(shared)

    # ログ記録を自動的に開始する。
    enable_print_log()

    # このタイミングでパラメーターを一度ログに出力しておく。(あとで比較するため)
    shared.print_parameters()

    # stepのスケールとパラメーターの移動性
    print_log(f"Step Scale = {SCALE}, Param Mobility = {MOBILITY}")

    while True:
        try:
            print_log("[Q]uit [G]ame [P]rint [W]rite [H]elp> ", end='')
            inp = input().split()
            if not inp:
                continue
            i = inp[0].lower()

            if i == 'h':
                print_log("Help : ")
                print_log("  Q : Quit")
                print_log("  ! : quit without saving")
                print_log("  G : start Games(SPSA)")
                print_log("  P : Print parameters")
                print_log("  W : Write parameters")
                print_log("  M : param Mobility [1.0 - 100.0]")
                print_log("  S : Step Scale     [1.0 -  10.0]")
                print_log("  R : Result table cols [1 - N]")

            elif i == 'g':
                print_log("start games(spsa)")
                matcher.start_games()

            elif i == 'p':
                shared.print_parameters()

            elif i == 'w':
                shared.write_parameters()

            elif i == 'm':
                if len(inp) >= 2:
                    lastMOBILITY = MOBILITY
                    MOBILITY = float(inp[1])
                    print_log(f"Param Mobility = {lastMOBILITY} -> {MOBILITY}")

            elif i == 's':
                if len(inp) >= 2:
                    lastSCALE = SCALE
                    SCALE = float(inp[1])
                    print_log(f"Step Scale     = {lastSCALE} -> {SCALE}")

            elif i == 'r':
                if len(inp) >= 2:
                    r = int(inp[1])
                    RESULT_TABLE_COLS = max(1, r)
                    print_log(f"Result table columns = {RESULT_TABLE_COLS}")

            elif i == 'q':
                # 終了時には自動セーブ
                shared.write_parameters()
                print_log("quit")
                break
            
            elif i == '!':
                print_log("quit without saving")
                break

        except Exception as e:
            print_log(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")


def main():
    user_input()

if __name__ == '__main__':
    main()

