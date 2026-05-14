import os
import random
import datetime
import math
from threading import Lock
import subprocess

import numpy as np
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

# 無限大に相当する評価値
VALUE_INF                    =  1000000
# やねうら王のVALUE_MATE。pack出力ではscore mate Nをこの体系に写像する。
VALUE_MATE                   =    32000
# その指し手の評価値が定まっていない時の定数(定跡として選択されないようにするために-INFみたいな値にしておく。-VALUE_INFは負けの指し手でそれよりはマシだろうから、9999にしておく。)
VALUE_NONE                   =   -99999

# 平手の開始局面
SFEN_START_PLY1              = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"

# ============================================================
#                    helper functions
# ============================================================

def mkdir(path:str):
    '''pathまでのフォルダを(なければすべて)作成する'''
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)


# ログを書き出すかのフラグ
write_log : bool = False
# ログファイルのhandle
log_file : Any = None # _io.TextIOWrapper

def print_log(*args:Any,end:str='\n'):
    ''' このスクリプト内で用いるprint関数。 '''
    print(make_time_stamp2(), end='')
    print(*args,end=end)
    if write_log:
        # タイムスタンプの付与
        log_file.write(make_time_stamp2())
        # argsが空の時、これは単なる改行のためのprintであるから無視する。
        if args:
            log_file.write(*args)
        log_file.write(end)
        log_file.flush()

def enable_print_log():
    '''print logを有効化する。'''        
    global log_file , write_log
    write_log = True
    filename = f'log/log_{make_time_stamp()}.log'
    mkdir(filename)
    log_file = open(filename,'w',encoding='utf-8')


def make_time_stamp()->str:
    '''現在時刻を文字列化したものを返す。ファイル名に付与するのに用いる。'''
    t_delta = datetime.timedelta(hours=9)
    JST = datetime.timezone(t_delta, 'JST')
    now = datetime.datetime.now(JST)
    d = now.strftime('%Y%m%d%H%M%S')
    return d

def make_time_stamp2()->str:
    '''現在時刻を文字列化したものを返す。ログに付与するのに用いる。'''
    t_delta = datetime.timedelta(hours=9)
    JST = datetime.timezone(t_delta, 'JST')
    now = datetime.datetime.now(JST)
    d = now.strftime('[%Y/%m/%d %H:%M:%S] ')
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


def trim_sfen_ply(sfen:str)->tuple[Sfen,int]:
    '''
    "sfen"で開始される形式のsfen文字列(ただし先頭の"sfen"は含まず)に対して、末尾の手数を取り除いて返す。
    
    末尾の手数を返す。手数がついていなければ、ply=0として返す。 
    '''
    s = sfen.split()

    # 先頭にsfenが含まれていたら除去
    if s[0] == 'sfen':
        del s[0]

    try:
        # 末尾が数字なのかテストする
        ply = int(s[-1])
        del s[-1]
    except:
        # 数字が付与されてないんじゃ？
        ply = 0

    return " ".join(s) , ply


def rand(r:int)->int:
    '''0からr-1までの整数乱数を返す。'''
    return random.randint(0,r-1)


# 180°反転させる時のマス目文字列
FLIP = {'1':'9','2':'8','3':'7','4':'6','5':'5','6':'4','7':'3','8':'2','9':'1',
        'a':'i','b':'h','c':'g','d':'f','e':'e','f':'d','g':'c','h':'b','i':'a'}

def flipped_move(move:Move)->Move:
    '''flipさせた指し手を返す'''
    if not len(move) in [4,5]:
        raise Exception(f"moveが4,5文字でない{move}")
    is_drop = move[1] == '*'
    if is_drop:
        return f"{move[0]}*{FLIP[move[2]]}{FLIP[move[3]]}{move[4:]}"
    return f"{FLIP[move[0]]}{FLIP[move[1]]}{FLIP[move[2]]}{FLIP[move[3]]}{move[4:]}"

def flipped_sfen(sfen:str)->Sfen:
    """
    与えられたsfen文字列をflipしたsfen文字列にする。
    例) "lnsgkgsnl/1r5b1/3pppppp/9/9/7P1/PPPPPPP1P/1B5R1/LNSGKGSNL w P2p"
　　　→ "lnsgkgsnl/1r5b1/p1ppppppp/1p7/9/9/PPPPPP3/1B5R1/LNSGKGSNL b 2Pp"
    """

    # 余分なものを除去
    sfen = trim_sfen(sfen)
    # 手数は含まれていない。手駒はなしなら"-"だから、常にあるはず。
    sfen_board, turn, hands= sfen.split()
    # 逆順にして大文字小文字を入れ替えるだけ。
    # ただし成り駒は'+'が駒名の前につく。逆順にしているので、'+b' になるべきところが 'b+'になってしまう。
    # そこで、逆順にしたあと、"b+"を"+b"に修正する。
    # 逆順にする前にやるとboard[i]を見てboard[i+1]とswapすると、次のループでまたそれがswapされてまずい。
    l = list(sfen_board[::-1].swapcase())
    for i in range(1, len(l)):
        if l[i] == '+':
            l[i], l[i - 1] = l[i - 1], l[i]
    sfen_board = ''.join(l)
    # 手駒は後ろから見ていき、最初に大文字になるところをmとして、そこで区切って入れ替えてswapcase
    # Finding the index 'm' from the end where the first uppercase letter appears
    m = next((i for i in range(len(hands) - 1, -1, -1) if hands[i].isupper()), -1)
    hands = (hands[m+1:] + hands[:m+1]).swapcase()
    # 手番は反転
    turn = 'w' if turn == 'b' else 'b'

    return f"{sfen_board} {turn} {hands}" 

def is_black_sfen(sfen:Sfen)->bool:
    """
    先手のsfen表記であるかを判定する。
    (sfen文字列はwが含まれていれば後手番。)
    """
    return 'w' not in sfen


def index_of(a:list[Any] | str, x:Any):
    ''' 配列 a から xを探し、何番目の要素であるかを返す。見つからなければ-1が返る。'''

    # try～exceptで書くと例外が発生しうるのでデバッガで追いかけるときに
    # 邪魔になる可能性がある。
    return a.index(x) if x in a else -1


def usi_mate_to_yaneuraou_eval(mate_ply_str:str, mate_score:int = VALUE_MATE)->Eval:
    """USI score mate をやねうら王のmate scoreへ変換する。"""
    if mate_ply_str == '+' or mate_ply_str == '-':
        return mate_score - 1 if mate_ply_str == '+' else -mate_score + 1

    mate_ply = int(mate_ply_str)
    if mate_ply > 0:
        return mate_score - mate_ply
    if mate_ply < 0:
        return -mate_score - mate_ply

    # mate 0は通常来ないが、来た場合は即詰み扱いにしておく。
    return mate_score

def evalstr_to_int(s1:str,s2:str, mate_score:int | None = None)->Eval:
    ''' cp 100 なら 100。mate 1 なら 31999 を返す。'''
    if s1 == 'cp':
        return int(s2)
    if s1 == "mate":
        return usi_mate_to_yaneuraou_eval(s2, VALUE_MATE if mate_score is None else mate_score)
    raise Exception(f"Error! : parse error {s1},{s2}")

def clamp_int16(x:int)->int:
    """signed int16に収まる範囲へ丸める。"""
    return min(32767, max(-32767, int(x)))

def clamp_uint16(x:int)->int:
    """uint16に収まる範囲へ丸める。"""
    return min(65535, max(0, int(x)))

def visits_from_scores(scores:list[int], visits_sum:int, temperature:float)->list[int]:
    """
    MultiPVの評価値をHCPE3のvisitNumへ変換する。

    DeepLearningShogi/dlshogi/utils/usi_selfplay_multipv_to_hcpe3.py と同じ考え方で、
    評価値softmaxから疑似訪問回数を作る。すべての候補に最低1visitを与える。
    """
    if not scores:
        return []

    if visits_sum < len(scores):
        visits_sum = len(scores)
    visits_sum = min(visits_sum, 65535)

    if temperature <= 0:
        visits = [1] * len(scores)
        best = max(range(len(scores)), key=lambda i: scores[i])
        visits[best] += visits_sum - len(scores)
        return visits

    max_score = max(scores)
    weights = [math.exp(max(-700.0, min(700.0, (score - max_score) / temperature))) for score in scores]
    total = sum(weights)

    base = [1] * len(scores)
    remaining = visits_sum - len(scores)
    raw = [weight / total * remaining for weight in weights]
    extra = [int(math.floor(x)) for x in raw]
    visits = [b + e for b, e in zip(base, extra)]

    residue = visits_sum - sum(visits)
    order = sorted(range(len(scores)), key=lambda i: raw[i] - extra[i], reverse=True)
    for i in order[:residue]:
        visits[i] += 1

    return [min(65535, max(1, v)) for v in visits]

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
                mkdir(kif_filename)
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

        # 現在探索中のsfen
        self.search_sfen = ""

        # 思考エンジンのprocessの起動。
        # sshしたいなら、pathに"ssh 2698a suisho6"のようなsshコマンドを書いておけば良い。
        if path.startswith("ssh"):
            # この場合、コマンドはlistで渡してやらないといけないらしい。
            self.engine = subprocess.Popen(path.split(), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                encoding="UTF-8")

        else:
            # 相対pathで呼び出すと評価関数を読み込めない問題があるっぽい..(やねうら王V9.00系で発覚)
            path = os.path.abspath(os.path.normpath(path))

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

        # "isready"を送信して"readyok"が返ってくるのを待つ。
        self.isready()


    def isready(self):
        self.search_sfen = ""

        # 思考エンジンに対してisreadyコマンドを送信して、
        # エンジン側からreadyokが返ってくるのを待つ。
        self.send_usi("isready")
        self.wait_usi("readyok") # readyokを待つ

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


    def wait_usi(self, wait_text:str):
        ''' 指定したコマンドが来るまで待つ '''
        while True:
            mes = self.receive_usi()
            # エンジンから送られてきたメッセージにErrorの文字列があるなら、
            # これは致命的なエラーなので例外を出して終了。
            if 'Error' in mes or 'No such option' in mes:
                self.raise_exception(f"Engine Error! : '{mes}'")
            if mes==wait_text:
                break

    def go(self,sfen:PositionStr, nodes:int)->tuple[Move,Eval]:
        '''
        思考エンジンに探索させる。
        sfen  : 局面(USIのpositionコマンドで指定できる形式)
        nodes : 探索ノード数
        
        返し値 : 最終的なbestmoveとその時の評価値が返る。
                例) ('7g7f',120)
                mate 1は、やねうら王形式の31999と数値化されて返る。
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
        besteval : Eval | None = None

        while True:
            ret = self.receive_usi()
            rets = ret.split()
            # これはUSIプロトコルでエンジン側から送られてくる文字列の"bestmove"
            if "bestmove" in ret:
                # 実戦だとこの指し手が'resign'とか'win'の可能性もあるが、評価値が先に振り切るので定跡掘る時には考えない。
                bestmove : Move = rets[1]
                if besteval is None:
                    raise Exception("Error! : bestmove received before eval.")
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

    def go_multipv(self, sfen:PositionStr, nodes:int, mate_score:int)->tuple[Move, Eval, list[tuple[Move, Eval]]]:
        '''
        思考エンジンにMultiPVで探索させる。

        返し値:
            bestmove, best_eval, candidates

        candidates は [(pv初手, 評価値), ...] で、USI infoのmultipv番号順に並ぶ。
        評価値は手番側から見たもの。score mate Nはmate_score - Nへ写像する。
        '''

        self.search_sfen = sfen
        self.send_usi(f"position {sfen}")
        self.send_usi(f"go nodes {nodes}")

        bestmove : Move = ""
        besteval : Eval | None = None
        multipv_infos : dict[int, tuple[Move, Eval]] = {}

        while True:
            ret = self.receive_usi()
            rets = ret.split()

            if "bestmove" in ret:
                bestmove = rets[1]
                if besteval is None:
                    besteval = 0
                candidates = [multipv_infos[k] for k in sorted(multipv_infos)]
                return bestmove, besteval, candidates

            if not rets or rets[0] != 'info':
                continue

            score_idx = index_of(rets, 'score')
            if score_idx != -1 and score_idx + 2 < len(rets):
                score = evalstr_to_int(rets[score_idx + 1], rets[score_idx + 2], mate_score)
                multipv_idx = index_of(rets, 'multipv')
                multipv = 1
                if multipv_idx != -1 and multipv_idx + 1 < len(rets):
                    try:
                        multipv = int(rets[multipv_idx + 1])
                    except ValueError:
                        multipv = 1
                if multipv == 1:
                    besteval = score

                pv_idx = index_of(rets, 'pv')
                if pv_idx != -1 and pv_idx + 1 < len(rets):
                    first_move = rets[pv_idx + 1]
                    multipv_infos[multipv] = (first_move, score)
    
    def raise_exception(self, error_message:str):
        ''' 例外を発生させる。エンジンの詳細を出力する。'''
        raise Exception(f"{error_message} , search_sfen : {self.search_sfen}")

# ============================================================
#                      cshogi wrapper
# ============================================================

import cshogi

class Board:
    '''
    cshogiがCythonで書かれていてPylacnceが機能しないのでwrapperを書く。
    cshogi.Boardとだいたい等価。
    '''
    def __init__(self,position_str:str=''):
        self.board = cshogi.Board() # type:ignore
        self.board.set_position(position_str)

    def to_svg(self)->str:
        '''局面をSVG化した文字列を返す。'''
        return self.board.to_svg()
    
    def set_position(self,position_str:str):
        '''局面を設定する。'''
        self.board.set_position(position_str)

    @property
    def turn(self)->int:
        return self.board.turn

    def push_usi(self,move:str)->int:
        '''
        指し手で局面を進める。
        move = USIプロトコルの指し手文字列
        '''
        return self.board.push_usi(move)

    def pop(self):
        '''局面を1つ戻す。'''
        self.board.pop()

    def is_draw(self)->int:
        '''千日手になっているかの判定。'''
        return self.board.is_draw()

    @property
    def legal_moves(self)->list[int]:
        '''
        合法手をすべて返す。型は32bit整数なので注意。
        これはmove_to_usi()でUSIの指し手文字列に変換できる。
        '''
        return self.board.legal_moves
    
    def sfen(self)->str:
        '''
        sfen文字列を返す。末尾に手数がついているので注意。
        '''
        return self.board.sfen()

    def ply(self)->int:
        '''
        開始からの手数を返す。
        '''
        # cshogiにこのmethodはないのでsfen化して末尾の数字を返す。
        return int(self.sfen().split()[-1])


class NonStandardBoard:
    """
    cshogiで扱えない55将棋などの特殊将棋向けに、Board相当の最小interfaceを提供する。
    合法手生成や千日手判定は行わず、position文字列に指し手を追記していく。
    """
    def __init__(self, position_str:str='startpos'):
        self.position_str, self.game_ply = trim_sfen_ply(position_str)
        self.new_position = True

    def set_position(self, position_str:str):
        self.position_str, self.game_ply = trim_sfen_ply(position_str)
        self.new_position = True

    def sfen(self)->str:
        return self.position_str

    def push_usi(self, move:str):
        if self.new_position:
            self.position_str += " moves"
            self.new_position = False
        self.position_str += ' ' + move
        self.game_ply += 1

    def is_draw(self)->bool:
        return False

    def ply(self)->int:
        return self.game_ply


def move_to_usi(m:int)->str:
    '''legal_moves()で返ってきた32bit整数をUSIプロトコルの指し手文字列に変換する。'''
    return cshogi.move_to_usi(m)

# 先手番を表す定数
BLACK                     = cshogi.BLACK

# 後手番を表す定数
WHITE                     = cshogi.WHITE

# HCPE3の勝敗・終局理由flag
HCPE3_DRAW                = 0
HCPE3_BLACK_WIN           = 1
HCPE3_WHITE_WIN           = 2
HCPE3_RESULT_REPETITION   = 4
HCPE3_RESULT_NYUGYOKU     = 8
HCPE3_RESULT_MAX_MOVES    = 16

# ============================================================

def board_to_hcp_bytes(board)->bytes:
    """cshogi.BoardをHuffmanCodedPosの32 bytesへ変換する。"""
    hcps = np.empty(1, dtype=cshogi.HuffmanCodedPos) # type:ignore
    board.to_hcp(hcps) # type:ignore
    return bytes(hcps)

def board_from_position_string(s : PositionStr)->cshogi.Board: # type:ignore
    """
    positionコマンドで指定する文字列
        startpos
        startpos moves ..
        SFEN文字列
        SFEN文字列 moves..
    をdecodeして、通常のSfen文字列(plyつき)に変換する。
    """
    if 'moves' in s:
        sfen , moves = s.split('moves')
        moves = moves.split()
    else:
        sfen, moves = s, []

    sfen = sfen.strip()
    if sfen == 'startpos':
        sfen = SFEN_START_PLY1

    board = cshogi.Board(sfen) # type:ignore
    for move in moves:
        board.push_usi(move)
    
    return board

class Hcpe3GameData:
    """
    1局分のHCPE3データ。

    HCPE3は開始局面HCPと手順列で1局を表すため、対局中はMoveInfo/MoveVisits相当の
    recordだけを保持し、終局後にHcpe3Writerがまとめて書き出す。
    """
    def __init__(self, start_hcp:bytes | None = None):
        self.start_hcp = start_hcp
        self.records : list[tuple[int, int, list[tuple[int, int]]]] = []
        self.result = HCPE3_DRAW
        self.position_num = 0

    def is_valid(self)->bool:
        return self.start_hcp is not None

    def set_result(self, result:int, reason:int = 0):
        self.result = result | reason

    def add_record(self, selected_move16:int, eval16:int, candidates:list[tuple[int, int]]):
        self.records.append((
            selected_move16 & 0xffff,
            clamp_int16(eval16),
            [(move16 & 0xffff, clamp_uint16(visit)) for move16, visit in candidates],
        ))
        if candidates:
            self.position_num += 1

# 1局の対局データ
class GameDataEncoder:
    """
    1対局分の棋譜データを格納する構造体
    """
    def __init__(self):
        # 棋譜データ本体
        self.data : bytearray = bytearray()
        # 書き出した局面数
        self.position_num = 0

    def get_bytes(self) -> bytearray:
        return self.data

    def set_startsfen(self, position_str:str):
        """
          対局開始局面をself.dataに追加する。
          また、self.boardには、この局面のcshogi.Boardが設定される。
        """

        # 盤面
        self.board = board_from_position_string(position_str)

        if self.board.sfen() == SFEN_START_PLY1:
            self.data.append(1) # startpos
            return

        self.data.append(0) # 任意局面。

        # byte列に
        hcps = np.empty(1, dtype=cshogi.HuffmanCodedPos) # type:ignore
        self.board.to_hcp(hcps) # type:ignore

        self.data.extend(bytes(hcps))

        game_ply = self.board.move_number
        self.write_uint16(game_ply)

    def write_uint8(self, b:int):
        """ 無符号8bit整数を追加する """
        self.data.append(b)

    def write_uint16(self, b:int):
        """ 無符号16bit整数を追加する。(指し手もこれで追加する) """
        self.data.extend(b.to_bytes(2, byteorder='little', signed=False))

    def write_int16(self, eval16:int):
        """ 符号つき16bit整数を追加する。"""
        self.data.extend(eval16.to_bytes(2, byteorder='little', signed=True))

    def write_eval(self, eval_int:int):
        """ 評価値の追加用 """

        # これはevalの書き出し用なので、clampしとくか…。
        if eval_int < -32000:
            eval_int = -32000
        elif eval_int > 32000:
            eval_int = 32000

        self.write_int16(eval_int)

        # 書き出した局面数をインクリメント
        self.position_num += 1

    def write_game_result(self, b:int):
        """ ゲーム結果を書き出す。0:引き分け, 1:先手勝ち, 2:後手勝ち """
        self.write_uint16(b + (b << 7))

class GameDataDecoder:
    """
    1対局分の棋譜データを読み取るクラス
    """
    def __init__(self, data:bytearray, pos:int=0):
        """
        棋譜のbytearrayと、読み取り開始位置を指定して初期化する。
        """
        self.data = data
        self.pos = pos

    def get_sfen(self)->str:
        state = self.read_uint8()
        if state == 1:
            # 平手の開始局面
            return SFEN_START_PLY1

        if state != 0:
            raise Exception("GameDataDecoder: get_sfen: 不明な開始局面形式です。")

        # hcp形式の任意局面
        b = self.read_bytes(32)
        board = cshogi.Board() # type:ignore
        board.set_hcp(np.frombuffer(b, dtype=cshogi.HuffmanCodedPos)) # type:ignore
        ply = self.read_uint16()
        board.move_number = ply
        return board.sfen() # type:ignore

    def get_pos(self)->int:
        """現在の読み取り位置を返す"""
        return self.pos

    def read_bytes(self, size:int) -> bytearray:
        """sizeバイト読み取って返す"""
        if len(self.data) < self.pos + size:
            raise Exception("GameDataDecoder: read_bytes: 読み取り範囲外です。")

        b = self.data[self.pos:self.pos+size]
        self.pos += size
        return b

    def read_uint8(self) -> int:
        """1バイト読み取ってuint8として返す"""
        b = self.read_bytes(1)
        return int.from_bytes(b, byteorder='little')

    def read_uint16(self)->int:
        """指し手(Move16)を2バイト読み取ってuint16として返す"""
        b = self.read_bytes(2)
        return int.from_bytes(b, byteorder='little', signed=False)
    
    def read_int16(self)->int:
        """評価値(符号つき16bit整数)を2バイト読み取ってint16として返す"""
        b = self.read_bytes(2)
        return int.from_bytes(b, byteorder='little', signed=True)

    # データの末尾までreadしたのか。
    def eof(self)->bool:
        return len(self.data) == self.pos


class KifWriter:
    """
    棋譜保存用クラス
    binaryで保存する。
    """
    def __init__(self, nodes:int):
        # 書き出すファイル名。自動生成。
        # nodes : ノード数。これをファイル名に付与する。
        self.kif_filename = f'kif/kif_{make_time_stamp()}_{nodes}.pack'
        mkdir(self.kif_filename)

        # 棋譜ファイルのhandle。8KBごとに書き出す。
        self.kif_file = open(self.kif_filename,'wb', buffering=8192)

        # 書き出した対局数
        self.game_count = 0

        # 書き出した局面数
        self.position_num = 0

        # ファイル書き出し時のlock
        self.lock = Lock()

    def get_kif_filename(self) -> str:
        """棋譜ファイル名を返す"""
        return self.kif_filename

    def write_game(self, game_data:GameDataEncoder):
        """
        1つの対局棋譜を書き出す。
        📝 GameDataEncoder.get_bytes()で得られたbytearrayを渡す。
        """
        with self.lock:
            self.kif_file.write(game_data.data)
            self.kif_file.flush()

            # 書き出した対局数
            self.game_count += 1
            self.position_num += game_data.position_num
            if self.game_count % 100 == 0:
                print_log(f"total games written: {self.game_count}, position_num = {self.position_num}")

    def close(self):
        """ファイルを閉じる"""
        self.kif_file.close()

class Hcpe3Writer:
    """
    HCPE3保存用クラス。

    複数の対局スレッドから呼ばれるので、1局分の書き出し全体をlockで保護する。
    """
    def __init__(self, nodes:int):
        self.hcpe3_filename = f'hcpe3/hcpe3_{make_time_stamp()}_{nodes}.hcpe3'
        mkdir(self.hcpe3_filename)
        self.hcpe3_file = open(self.hcpe3_filename, 'wb', buffering=8192)
        self.game_count = 0
        self.position_num = 0
        self.lock = Lock()

    def get_hcpe3_filename(self)->str:
        return self.hcpe3_filename

    def write_game(self, game_data:Hcpe3GameData):
        if not game_data.is_valid():
            return

        with self.lock:
            start_hcp = game_data.start_hcp
            if start_hcp is None:
                return
            if len(start_hcp) != 32:
                raise Exception(f"invalid HCP size: {len(start_hcp)}")

            self.hcpe3_file.write(start_hcp)
            self.hcpe3_file.write(len(game_data.records).to_bytes(2, byteorder='little', signed=False))
            self.hcpe3_file.write((game_data.result & 0xff).to_bytes(1, byteorder='little', signed=False))
            self.hcpe3_file.write((0).to_bytes(1, byteorder='little', signed=False)) # opponent: self-play

            for selected_move16, eval16, candidates in game_data.records:
                self.hcpe3_file.write(selected_move16.to_bytes(2, byteorder='little', signed=False))
                self.hcpe3_file.write(eval16.to_bytes(2, byteorder='little', signed=True))
                self.hcpe3_file.write(len(candidates).to_bytes(2, byteorder='little', signed=False))
                for move16, visit_num in candidates:
                    self.hcpe3_file.write(move16.to_bytes(2, byteorder='little', signed=False))
                    self.hcpe3_file.write(visit_num.to_bytes(2, byteorder='little', signed=False))

            self.hcpe3_file.flush()
            os.fsync(self.hcpe3_file.fileno())

            self.game_count += 1
            self.position_num += game_data.position_num
            if self.game_count % 100 == 0:
                print_log(f"total hcpe3 games written: {self.game_count}, position_num = {self.position_num}")

    def close(self):
        self.hcpe3_file.close()
