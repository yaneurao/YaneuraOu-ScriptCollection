import os
import random
import datetime
from dataclasses import dataclass
from threading import Lock
import subprocess

from typing import Any

# ============================================================
#                         type alias
# ============================================================

# USIプロトコルのSFEN文字列。ただし先頭の"sfen "と末尾の手数は記されていないものとする。
Sfen = str

# USIプロトコルのposition文字列。
# USIのpositionコマンドの1行のうち"position "の文字を省いたもの。
# "startpos"とか"startpos moves ..."とか"sfen ..."みたいな文字列。
PositionStr = str

# 指し手文字列(USIプロトコルの形式)
Move  = str

# やねうら王形式の定跡DBファイルに書き出す時の指し手に対応する評価値
Eval  = int

# 無限大に相当する評価値(mate 1は、VALUE_INF-1)
VALUE_INF                    =  1000000
# その指し手の評価値が定まっていない時の定数(定跡として選択されないようにするために-INFみたいな値にしておく。-VALUE_INFは負けの指し手でそれよりはマシだろうから、9999にしておく。)
VALUE_NONE                   =   -99999


# ============================================================
#                    helper functions
# ============================================================

def mkdir(path:str):
    '''フォルダを(なければ)作成する'''
    try:
        dirpath = os.path.dirname(path)
        os.mkdir(dirpath)
    except:
        # すでに存在すると例外が飛んでくるので握りつぶす。
        pass


def make_time_stamp()->str:
    '''現在時刻を文字列化したものを返す。ファイル名に付与するのに用いる。'''
    t_delta = datetime.timedelta(hours=9)
    JST = datetime.timezone(t_delta, 'JST')
    now = datetime.datetime.now(JST)
    d = now.strftime('%Y%m%d%H%M%S')
    return d


def trim_sfen(sfen:str)->Sfen:
    ''' "sfen"で開始される形式のsfen文字列(ただし先頭の"sfen"は含まず)に対して、末尾の手数を取り除いて返す。 '''
    s = sfen.split()

    # 先頭にsfenが含まれていたら除去
    if s[0] == 'sfen':
        del s[0]

    try:
        # 末尾が数字なのかテストする
        int(s[-1])
        del s[-1]
    except:
        # 数字が付与されてないんじゃ？
        pass

    return " ".join(s)


def rand(r:int)->int:
    '''0からr-1までの整数乱数を返す。'''
    return random.randint(0,r-1)


def index_of(a:list[Any] | str, x:Any):
    ''' 配列 a から xを探し、何番目の要素であるかを返す。見つからなければ-1が返る。'''

    # try～exceptで書くと例外が発生しうるのでデバッガで追いかけるときに
    # 邪魔になる可能性がある。
    return a.index(x) if x in a else -1


def evalstr_to_int(s1:str,s2:str)->Eval:
    ''' cp 100 なら 100。mate 1 なら 99999 を返す'''
    if s1 == 'cp':
        return int(s2)
    if s1 == "mate":
        # 単に '+'とか'-'のことがある。
        if s2 == '+' or s2 == '-':
            # mate 1のスコアにしておく。
            return VALUE_INF-1 if s2=='+' else -VALUE_INF+1
        x = int(s2)
        return VALUE_INF-x if x > 0 else -VALUE_INF-x
    raise Exception(f"Error! : parse error {s1},{s2}")

# ============================================================
#                        Engine
# ============================================================

class KifManager:
    """ 棋譜を保存する時に用いる。write_kif()で一局分の棋譜を書き出す。 """

    def __init__(self):
        # 棋譜ファイルに書き出す時のlock
        self.lock : Lock = Lock()

        # 棋譜ファイルのhandle
        self.kif_file = None

        # 棋譜保存フォルダ
        self.kif_folder : str = "kif"


    def write_kif(self, kif:str):
        '棋譜を一行書き出す'
        with self.lock:
            if self.kif_file is None:
                kif_filename = os.path.join(self.kif_folder , f'{make_time_stamp()}.txt')
                self.kif_file = open(kif_filename, 'w', encoding='utf-8')
            self.kif_file.write(kif + '\n')
            self.kif_file.flush()

class Engine:
    """エンジン操作class"""

    def __init__(self, engine_path, thread_id: int):
        """
        path : エンジンの実行ファイルのpath
        thread_id : 0から連番なスレッドID
        """
        # 探索中のsfen
        self.searching_sfen = ""

        # エンジンの実行ファイルへのpath
        path : str = engine_path

        # スレッドID
        self.thread_id = thread_id

        # readyokをエンジンから受け取ったか。
        self.readyok = False

        # 思考エンジンのprocessの起動。
        # sshしたいなら、pathに"ssh 2698a suisho6"のようなsshコマンドを書いておけば良い。
        if path.startswith("ssh"):
            # この場合、コマンドはlistで渡してやらないといけないらしい。
            self.engine = subprocess.Popen(path.split(), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                encoding="UTF-8")

        else:
            if not os.path.isfile(path):
                self.raise_exception(f"Engine not Found , path = {path}")

            # 思考エンジンの実行ファイルが存在するフォルダをworking directoryとして
            # 指定しておかないと評価関数ファイルなど、実行ファイル相対で配置するファイルが
            # 思考エンジンから読み込めなくてエラーになるのでworking directorを指定する。
            working_directory = os.path.dirname(path)

            self.engine = subprocess.Popen(path, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                cwd=working_directory,
                                                encoding="UTF-8")

        # 思考エンジンに対してisreadyコマンドを送信して、
        # エンジン側からreadyokが返ってくるのを待つ。
        self.send_usi("isready")
        self.wait_usi("readyok") # readyokを待つ

        # すべてのエンジンの開始をここで待機。
        # while not global_settings.all_readyok:
        #     time.sleep(1)

        # Debug用に実際に探索させてみる。
        # print_log(self.go("startpos"))


    def send_usi(self, command:str):
        ''' 思考エンジンに対してUSIコマンドを送信する。 '''

        # デバッグモードならエンジンへの入出力をすべて標準出力へ。
        # if self.global_settings.debug_engine:
        #     print_log(f'[{self.thread_settings.thread_id}]<{command}')

        self.engine.stdin.write(command+"\n") # type:ignore
        self.engine.stdin.flush()             # type:ignore

    def receive_usi(self)->str:
        ''' 思考エンジンから1行もらう。改行は取り除いて返す。'''
        mes = self.engine.stdout.readline().strip() # type:ignore

        # デバッグモードならエンジンへの入出力をすべて標準出力へ。
        # if self.global_settings.debug_engine:
        #     print_log(f'[{self.thread_settings.thread_id}]>{mes}')

        # エンジンのprocessが死んでたら例外を出す。
        if self.engine.poll() is not None:
            self.raise_exception(f"Engine is terminated.")
        return mes


    def wait_usi(self,wait_text:str):
        ''' 指定したコマンドが来るまで待つ '''
        while True:
            mes = self.receive_usi()
            # エンジンから送られてきたメッセージにErrorの文字列があるなら、
            # これは致命的なエラーなので例外を出して終了。
            if 'Error' in mes:
                self.raise_exception(f"Engine Error! : {mes}")
            if mes==wait_text:
                break

    def go(self,sfen:PositionStr, nodes:int)->tuple[Move,Eval]:
        '''
        思考エンジンに探索させる。
        sfen  : 局面(USIのpositionコマンドで指定できる形式)
        nodes : 探索ノード数
        
        返し値 : 最終的なbestmoveとその時の評価値が返る。
                例) ('7g7f',120)
                mate 1は、99999と数値化されて返る。
        '''

        # "position"コマンドを思考エンジンに送信する。
        self.search_sfen = sfen
        self.send_usi(f"position {sfen}")

        # "go"コマンドを思考エンジンに送信する。
        self.send_usi(f"go nodes {nodes}")

        # "bestmove"は必ず返ってくるはずなのでそれを待つ。
        # 読み筋(PV)の初手と最終的な評価値とbestmoveをparseして返す。

        # "bestmove"は必ず返ってくるはずなのでそれを待つ。
        # 読み筋(PV)の初手と最終的な評価値とbestmoveをparseして返す。

        # 最善手
        bestmove : Move = ""
        # 最終的な評価値
        besteval : Eval = 0

        while True:
            ret = self.receive_usi()
            rets = ret.split()
            # これはUSIプロトコルでエンジン側から送られてくる文字列の"bestmove"
            if "bestmove" in ret:
                # 実戦だとこの指し手が'resign'とか'win'の可能性もあるが、評価値が先に振り切るので定跡掘る時には考えない。
                bestmove : Move = rets[1]
                return bestmove , besteval
            else:
                # 読み筋に対して、そのpvの初手を蓄積していく。
                # また最終的な評価値も保存しておく。
                
                # info nodes XX score cp YY pv ZZ ... の形なので、"cp"と"pv"を起点として、その直後の文字を取得する。
                if not rets or rets[0]!='info':
                    # infoと違う。何か関係ないメッセージっぽい。
                    continue

                idx = index_of(rets,"nodes")
                nodes = 0
                if idx != -1:
                    nodes = int(rets[idx+1])

                idx = index_of(rets,'score')
                if idx != -1:
                    besteval = evalstr_to_int(rets[idx+1],rets[idx+2])

                # idx = index_of(rets,'pv')
                # if idx != -1:
                #     # 'pv'の次のtokenが存在することを仮定している。
                #     # 実戦だとこの指し手が'resign'とか'win'の可能性もあるが、評価値が先に振り切るので定跡掘る時には考えない。
                #     first_move = rets[idx+1]
                #     # 発見した順序が保たれて欲しいので順番に追記していく。
                #     if nodes >= min_nodes and not first_move in first_moves:
                #         first_moves += first_move,
                #     if not first_move in first_moves_all:
                #         first_moves_all += first_move,
    
    def raise_exception(self, error_message:str):
        ''' 例外を発生させる。エンジンの詳細を出力する。'''
        raise Exception(f"{error_message} , search_sfen : {self.search_sfen}")

# ============================================================
#                      Read/Write Parameters
# ============================================================

# パラメーターファイルで、そのパラメーターを使っていないことを示す文字列。
NOT_USED_STR = "[[NOT USED]]" 

# パラメーターファイルの1行を表現する型
@dataclass
class Entry:
    # パラメーター名
    name: str

    # パラメーターの型("int" | "str")
    type : str

    # パラメーターの現在値
    v: float

    # パラメーターの最小値
    min : float

    # パラメーターの最大値
    max : float

    # step(の最終値) : C_End
    step : float

    # 一回の移動量(の最終値) : R_End
    delta : float

    # コメント("//"以降)
    comment : str

    # このパラメーターを使わないのか
    not_used : bool


def read_parameters(params_file : str)->list[Entry]:
    """
        パラメーターファイルを読み込む
        パラメーターファイルにはEntry構造体の定義順でデータがカンマ区切りで並んでいるものとする。
    """

    print(f"read parameters, path = {params_file}", end="")

    if not os.path.exists(params_file):
        raise Exception(f"Error : {params_file} not found.")
        
    l : list[Entry] = []

    with open(params_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for lineNo, line in enumerate(lines, 1):
        line = line.rstrip()
        if not line:
            continue  # 空行は無視

        not_used = False
        if NOT_USED_STR in line:
            line.replace(NOT_USED_STR, "")
            not_used = True

        if "//" in line:
            val_part, comment = line.split("//", 1)
        else:
            val_part, comment = line , ""

        values = [v.strip() for v in val_part.split(",")]
        if len(values) < 7:
            raise Exception(f"Error: insufficient params , {params_file}({lineNo}) : {line}")
            
        e = Entry(name = values[0], type = str(values[1]),
                    v = float(values[2]),
                    min = float(values[3]), max = float(values[4]),
                    step = float(values[5]), delta = float(values[6]),
                    comment = comment, not_used = not_used)

        l.append(e)

    print(f", {len(l)} parameters.")

    return l


def write_parameters(params_file : str , entries : list[Entry]):
    """
        パラメーターファイルに書き込む。
        パラメーターファイルにはEntry構造体の定義順でデータがカンマ区切りで並んでいるものとする。
    """

    with open(params_file, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(f"{e.name}, {e.type}, {e.v}, {e.min}, {e.max}, {e.step}, {e.delta}{' //' + e.comment if e.comment else ""}{NOT_USED_STR if e.not_used else ""}\n")

    print(f"write parameter file, {len(entries)} parameters.")

