import os
import math
import time
import json5
import traceback
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneShogiLib import *
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

        # 基準エンジンごとの勝敗履歴。
        self.win_count_by_opponent : dict[str, list[int]] = {}

        # node倍率ごとの勝敗履歴。
        self.win_count_by_node_multiplier : dict[str, list[int]] = {}

        # 基準エンジン × node倍率ごとの勝敗履歴。
        self.win_count_by_opponent_and_node_multiplier : dict[str, list[int]] = {}

        # ↑を書き換える時のlock
        self.lock = Lock()

    def build_summary(self, results:list[int])->str:
        summary = []

        # 直近nの勝率、レート差などを出力。
        n = RATE_OUTPUT_INTERVAL
        while len(results) >= n:
            # n が RATE_OUTPUT_INTERVAL × 2^m である

            win  = results[-n:].count(1) # player 1の勝利回数
            lose = results[-n:].count(0) # player 0の勝利回数
            draw = results[-n:].count(2)

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

            # Last N SPSA対象エンジンから見た win-draw-lose 勝率 R差
            summary.append(f"{n} {win}-{draw}-{lose}, {win_rate}, R{rate_diff}")

            # レートだけ表示用に積むか。
            # summary.append(f"R{rate_diff}")

            n *= 2

        # '|' 区切りでNの降順で直近 RESULT_TABLE_COLS つ出力
        summary.reverse()
        return ' | '.join(summary[:RESULT_TABLE_COLS])

    def update(self, winner:int, opponent_name:str, node_multiplier:float):
        with self.lock:
            self.win_count.append(winner)
            opponent_results = self.win_count_by_opponent.setdefault(opponent_name, [])
            opponent_results.append(winner)
            node_multiplier_name = format_node_multiplier(node_multiplier)
            node_results = self.win_count_by_node_multiplier.setdefault(node_multiplier_name, [])
            node_results.append(winner)
            opponent_node_name = f"{opponent_name} node x{node_multiplier_name}"
            opponent_node_results = self.win_count_by_opponent_and_node_multiplier.setdefault(opponent_node_name, [])
            opponent_node_results.append(winner)

            # 途中経過の表示用に勝敗を1文字で出力してやる。
            # print("LWD"[winner], end="")

            # 総対局回数
            total = len(self.win_count)

            if total % RATE_OUTPUT_INTERVAL == 0:
                print_log(f"{total} : ALL : {self.build_summary(self.win_count)}")

                for name in sorted(self.win_count_by_opponent.keys()):
                    results = self.win_count_by_opponent[name]
                    if len(results) < RATE_OUTPUT_INTERVAL:
                        continue
                    print_log(f"{total} : {name} : {len(results)} games : {self.build_summary(results)}")

                for name in sorted(self.win_count_by_node_multiplier.keys(), key=node_multiplier_sort_key):
                    results = self.win_count_by_node_multiplier[name]
                    if len(results) < RATE_OUTPUT_INTERVAL:
                        continue
                    print_log(f"{total} : node x{name} : {len(results)} games : {self.build_summary(results)}")

                for name in sorted(self.win_count_by_opponent_and_node_multiplier.keys()):
                    results = self.win_count_by_opponent_and_node_multiplier[name]
                    if len(results) < RATE_OUTPUT_INTERVAL:
                        continue
                    print_log(f"{total} : {name} : {len(results)} games : {self.build_summary(results)}")

def format_node_multiplier(value:float)->str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"

def node_multiplier_sort_key(value:str)->float:
    try:
        return float(value)
    except ValueError:
        return 0.0

def scaled_nodes(nodes:int, node_multiplier:float)->int:
    return max(1, int(nodes * node_multiplier + 0.5))

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

        if len(threads_all) != 2:
            raise ValueError(f"ENGINE_SETTINGS must contain exactly 2 engine setting files, actual = {len(threads_all)}")

        if len(threads_all[0]) != len(threads_all[1]):
            raise ValueError(f"Number of engines mismatch, {len(threads_all[0])} != {len(threads_all[1])}")

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

        # 対局ごとに掛けるnode倍率。未指定なら従来通り1倍固定。
        self.node_multipliers = self.read_node_multipliers(settings)
        self.node_multiplier_index = 0
        self.node_multiplier_lock = Lock()

        # パラメーターファイル
        self.parameters : list[Entry] = read_parameters(settings["PARAMETERS_PATH"])
        # ↑のvを書き換える時用のlock object
        self.param_lock = Lock()

        self.validate_tunable_options_are_not_fixed()

        # 勝率マネージャー
        self.win_manager = WinManager()

        # 標準的な将棋盤なのか
        self.standard_board = self.settings["STANDARD_BOARD"]

        print_log(f"Node multipliers = {', '.join(format_node_multiplier(x) for x in self.node_multipliers)}")


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
            if not os.path.exists(engine_settings_path):
                sample_path = engine_settings_path.replace(".json5", "-sample.json5")
                raise FileNotFoundError(f"{engine_settings_path} not found. Copy {sample_path} and edit it.")
            with open(engine_settings_path, 'r', encoding='utf-8') as f:
                engine_settings.append(json5.load(f))
        return engine_settings

    def read_node_multipliers(self, settings:dict)->list[float]:
        multipliers = settings.get("NODE_MULTIPLIERS", settings.get("node_multipliers", [1]))
        if not isinstance(multipliers, list) or not multipliers:
            raise ValueError("NODE_MULTIPLIERS must be a non-empty list.")

        result : list[float] = []
        for value in multipliers:
            multiplier = float(value)
            if not math.isfinite(multiplier) or multiplier <= 0:
                raise ValueError(f"NODE_MULTIPLIERS contains invalid value: {value}")
            result.append(multiplier)
        return result

    def next_node_multiplier(self)->float:
        with self.node_multiplier_lock:
            multiplier = self.node_multipliers[self.node_multiplier_index % len(self.node_multipliers)]
            self.node_multiplier_index += 1
            return multiplier

    def validate_tunable_options_are_not_fixed(self):
        """
        やねうら王は engine_options.txt / eval_options.txt で読み込まれた項目を fixed にする。
        fixed になった項目は通常の setoption では変更できないので、SPSA対象に含めると
        対局時には値が変わらないまま、パラメーターファイルだけが更新されてしまう。
        """

        if len(self.engine_settings) < 2:
            return

        tunable_names = {p.name for p in self.parameters if not p.not_used}
        if not tunable_names:
            return

        conflicts : list[str] = []
        for engine_setting in self.engine_settings[1]:
            engine_path = engine_setting.get("path", "")
            if engine_path.startswith("ssh"):
                continue

            engine_file = Path(engine_path).expanduser()
            if not engine_file.is_absolute():
                engine_file = Path(engine_file)
            engine_dir = engine_file.resolve().parent

            option_files = [engine_dir / "engine_options.txt"]
            eval_dir = self.read_option_value(option_files[0], "EvalDir")
            if eval_dir:
                eval_path = Path(eval_dir)
                if not eval_path.is_absolute():
                    eval_path = engine_dir / eval_path
                option_files.append(eval_path / "eval_options.txt")
            else:
                option_files.append(engine_dir / "eval" / "eval_options.txt")

            for option_file in option_files:
                fixed_options = self.read_option_names(option_file)
                duplicated = sorted(tunable_names & fixed_options)
                if duplicated:
                    conflicts.append(f"{option_file}: {', '.join(duplicated)}")

        if conflicts:
            raise ValueError(
                "SPSA target engine fixes tunable option(s) in engine_options.txt/eval_options.txt. "
                "Remove these entries, otherwise setoption from SPSA is ignored:\n"
                + "\n".join(conflicts))

    def read_option_value(self, path:Path, name:str)->str|None:
        for option_name, option_value in self.read_options_file(path):
            if option_name.lower() == name.lower():
                return option_value
        return None

    def read_option_names(self, path:Path)->set[str]:
        return {name for name, _ in self.read_options_file(path)}

    def read_options_file(self, path:Path)->list[tuple[str, str]]:
        if not path.exists():
            return []

        options : list[tuple[str, str]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parsed = self.parse_option_line(line)
                if parsed is not None:
                    options.append(parsed)
        return options

    def parse_option_line(self, line:str)->tuple[str, str]|None:
        line = line.split("//", 1)[0].strip()
        if not line:
            return None

        if "=" in line and not line.lower().startswith("option "):
            name, value = line.split("=", 1)
            return name.strip(), value.strip()

        tokens = line.split()
        if not tokens:
            return None

        if tokens[0].lower() != "option":
            return tokens[0], " ".join(tokens[1:])

        lower_tokens = [t.lower() for t in tokens]
        if "name" not in lower_tokens:
            return None

        name_pos = lower_tokens.index("name") + 1
        end_pos = len(tokens)
        for marker in ("type", "default", "value", "min", "max", "var"):
            if marker in lower_tokens[name_pos:]:
                end_pos = min(end_pos, lower_tokens.index(marker, name_pos))

        name = " ".join(tokens[name_pos:end_pos]).strip()
        value = ""
        if "default" in lower_tokens:
            default_pos = lower_tokens.index("default") + 1
            value = tokens[default_pos] if default_pos < len(tokens) else ""
        return (name, value) if name else None

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
                root_sfen = self.pick_root_sfen()
                node_multiplier = self.shared.next_node_multiplier()

                # 変異させたパラメーターを思考エンジンに設定
                self.set_engine_options(params, p_shift_plus)

                # 対局
                winner = self.game_play(start_player, root_sfen, node_multiplier)

                # 勝ち数のカウント
                self.shared.win_manager.update(winner, self.t[0].engine_name, node_multiplier)

                step = winner_to_step[winner] * +1.0

                # 次の対局の手番を入れ替える。
                start_player ^= 1

                # 逆方向に変異させたパラメーターを思考エンジンに設定
                self.set_engine_options(params, p_shift_minus)

                winner = self.game_play(start_player, root_sfen, node_multiplier)
                self.shared.win_manager.update(winner, self.t[0].engine_name, node_multiplier)
                step += winner_to_step[winner] * -1.0

                # パラメーターをshift(方角)×step分だけ変異させる。
                # / 2は中心差分近似のときに出てくる 2。
                self.add_grad(params, shift, step / 2)

                # 次の対局の手番を入れ替える。
                start_player ^= 1

        except Exception as e:
            print_log(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")


    def pick_root_sfen(self)->str:
        return self.shared.root_sfens[rand(len(self.shared.root_sfens))].rstrip()

    def game_play(self, start_player : int, root_sfen:str|None = None, node_multiplier:float = 1.0):
        """ 1局だけ対局する """

        # 対局開始前のisready送信
        # (パラメーターが変更になったかも知れないので毎回`isready`で初期化)
        for engine in self.engines:
            engine.isready()

        board = Board() if self.shared.standard_board else NonStandardBoard()

        # 対局開始局面(互角局面集からランダム)
        if root_sfen is None:
            root_sfen = self.pick_root_sfen()

        board.set_position(root_sfen)

        # 現在の手数
        ply = board.ply()

        kif = f"{root_sfen}{'' if 'moves' in root_sfen else ' moves'}"

        # start_player : 開始局面で手番を持つプレイヤー番号。
        # root_sfen の手番色が先手とは限らないので、先手/後手ではなく player で管理する。
        # player : 現在の手番側のプレイヤー番号
        player = start_player

        # 試合の結果 0 : player0勝ち , 1 : player1勝ち , 2 : 引き分け
        # player0は基準エンジン、player1はSPSA対象エンジン。
        winner : int = -1

        while True:
            nodes = scaled_nodes(self.t[player].engine_nodes, node_multiplier)
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
    if not os.path.exists(SETTING_PATH):
        sample_path = SETTING_PATH.replace(".json5", "-sample.json5")
        raise FileNotFoundError(f"{SETTING_PATH} not found. Copy {sample_path} and edit it.")

    with open(SETTING_PATH, "r", encoding="utf-8") as f:
        settings = json5.load(f)

    # これは全対局スレッドが同じものを指す。
    shared = SharedState(settings)

    # 並列対局管理用
    matcher = GameMatcher(shared)

    # ログ記録を自動的に開始する。
    enable_print_log()

    # このタイミングでパラメーターを一度ログに出力しておく。(あとで比較するため)
    # shared.print_parameters()

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

