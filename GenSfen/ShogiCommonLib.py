import os
import random
import datetime
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

# 無限大に相当する評価値(mate 1は、VALUE_INF-1)
VALUE_INF                    =  1000000
# その指し手の評価値が定まっていない時の定数(定跡として選択されないようにするために-INFみたいな値にしておく。-VALUE_INFは負けの指し手でそれよりはマシだろうから、9999にしておく。)
VALUE_NONE                   =   -99999

# 平手の開始局面
STARTPOS_SFEN               = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"

# ============================================================
#                    helper functions
# ============================================================

def mkdir(path:str):
    '''pathまでのフォルダを(なければすべて)作成する'''
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)


# ログを書き出すかのフラグ
write_log : bool = False
# ログファイルのhandle
log_file : Any = None # _io.TextIOWrapper

def print_log(*args:Any,end:str='\n'):
    ''' このスクリプト内で用いるprint関数。 '''
    print(*args,end=end)
    if write_log:
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
#                      cshogi wrapper
# ============================================================

import cshogi

class Board:
    '''
    cshogiがCythonで書かれていてPylacnceが機能しないのでwrapperを書く。
    cshogi.Boardとだいたい等価。
    '''
    def __init__(self,position_str:str=''):
        self.board = cshogi.Board(position_str)
    
    def to_svg(self)->str:
        '''局面をSVG化した文字列を返す。'''
        return self.board.to_svg()
    
    def set_position(self,position_str:str)->str:
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

def move_to_usi(m:int)->str:
    '''legal_moves()で返ってきた32bit整数をUSIプロトコルの指し手文字列に変換する。'''
    return cshogi.move_to_usi(m)

# 先手番を表す定数
BLACK                     = cshogi.BLACK

# 後手番を表す定数
WHITE                     = cshogi.WHITE

# ============================================================

# 1局の対局データ
class GameDataEncoder:
    """
    1対局分の棋譜データを格納する構造体
    """
    def __init__(self):
        # 棋譜データ本体
        self.data : bytearray = bytearray()

    def get_bytes(self) -> bytearray:
        return self.data

    def set_startsfen(self, sfen:str):
        """ 対局開始局面を追加する。 """

        # 盤面
        self.board = cshogi.Board(sfen) # type:ignore

        board_sfen = self.board.sfen()
        if self.board.sfen() == STARTPOS_SFEN:
            self.data.append(1) # startpos
            return

        self.data.append(0) # 任意局面。

        # byte列に
        hcps = np.empty(1, dtype=cshogi.HuffmanCodedPos) # type:ignore
        self.board.to_hcp(hcps) # type:ignore

        self.data.extend(bytes(hcps))

    def write_uint8(self, b:int):
        """ 無符号8bit整数を追加する """
        self.data.append(b)

    def write_uint16(self, b:int):
        """ 無符号16bit整数を追加する。(指し手もこれで追加する) """
        self.data.extend(b.to_bytes(2, byteorder='little', signed=False))

    def write_int16(self, eval16:int):
        """ 符号つき16bit整数を追加する。(評価値もこれで追加する) """
        self.data.extend(eval16.to_bytes(2, byteorder='little', signed=True))

    def write_game_result(self, b:int):
        """ ゲーム結果を書き出す。0:引き分け, 1:先手勝ち, 2:後手勝ち """
        self.data.append(b)
        self.data.append(b)

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
            return STARTPOS_SFEN

        if state != 0:
            raise Exception("GameDataDecoder: get_sfen: 不明な開始局面形式です。")

        # hcp形式の任意局面
        b = self.read_bytes(32)
        board = cshogi.Board() # type:ignore
        board.set_hcp(np.frombuffer(b, dtype=cshogi.HuffmanCodedPos)) # type:ignore
        ply = self.read_uint16()
        board.move_number(ply)
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

class KifWriter:
    """
    棋譜保存用クラス
    binaryで保存する。
    """
    def __init__(self):
        # 書き出すファイル名。自動生成。
        self.kif_filename = f'kif/kif_{make_time_stamp()}.pack'
        mkdir(self.kif_filename)

        # 棋譜ファイルのhandle。8KBごとに書き出す。
        self.kif_file = open(self.kif_filename,'wb', buffering=8192)

        # ファイル書き出し時のlock
        self.lock = Lock()

    def get_kif_filename(self) -> str:
        """棋譜ファイル名を返す"""
        return self.kif_filename

    def write_game(self, game_data:bytearray):
        """
        1つの対局棋譜を書き出す。
        📝 GameDataEncoder.get_bytes()で得られたbytearrayを渡す。
        """
        with self.lock:
            self.kif_file.write(game_data)

    def close(self):
        """ファイルを閉じる"""
        self.kif_file.close()

# KifReaderを書こうと思ったが、可変長フォーマットなのでparseするまで終わりかどうかが確定しない。
# ちょっと使い勝手が悪そうであった。
# なので、KifReaderは書かずに、PackedKifToHcpeというクラスを書いておく。

def pack_file_to_hcpe(pack_file_path:str, hcpe_file_path:str) -> None:
    """
    Pack形式のファイルをhcpe形式のファイルに変換する。
    """
    with open(pack_file_path, 'rb') as r:
        # 丸読みするの、あまり良くないけど、このコード、丸読みしないのは難しい。
        data = r.read()

    decoder = GameDataDecoder(bytearray(data))

    # 対局数
    game_index = 0
    # 局面数
    game_positions = 0

    # HuffmanCodedPosAndEval = np.dtype([
    #     ('hcp', dtypeHcp),
    #     ('eval', dtypeEval),
    #     ('bestMove16', dtypeMove16),
    #     ('gameResult', dtypeGameResult),
    #     ('dummy', np.uint8),
    #     ])

    # GAME_RESULTS = [
    # DRAW, BLACK_WIN, WHITE_WIN,
    # ] = range(3)
    
    with open(hcpe_file_path, 'wb') as w:

        while True:
            try:
                sfen = decoder.get_sfen()
                # print(f"Game {game_index} startpos: {sfen}")
                game_index += 1

                board = cshogi.Board(sfen) # type:ignore

                # 対局1局分をparseする。
                game_kif = []
                game_result = 0

                while True:

                    move = decoder.read_uint16()

                    # 対局終了？
                    if move == 0x0000 or move == 0x0101 or move == 0x0202:
                        # 勝者 = draw , black_win , white_win
                        game_result = move & 0x00ff
                        # 終局理由
                        reason = decoder.read_uint8()
                        # print(f"  End of game with result code: {move:04x} , reason: {reason}")

                        # 負けが確定している局面は書き出さなくていいか…。
                        break

                    eval16 = decoder.read_int16()

                    # あとで取り出す。
                    game_kif.append( (move, eval16) )

                # 1局分の記譜をファイルに書き出す。
                for move, eval16 in game_kif:

                    usi_move = cshogi.move_to_usi(move) # type:ignore
                    # print(f"  Move: {move:04x} = {usi_move}, Eval: {eval16}, game result = {game_result}")

                    # 局面
                    hcps = np.empty(1, dtype=cshogi.HuffmanCodedPos) # type:ignore
                    board.to_hcp(hcps) # type:ignore
                    w.write(bytes(hcps))

                    # 評価値(手番側から見たもの)
                    w.write(eval16.to_bytes(2, byteorder='little', signed=True))

                    # 指し手
                    w.write(move.to_bytes(2, byteorder='little', signed=False)) 

                    # 勝った側
                    w.write(game_result.to_bytes(1, byteorder='little', signed=False))

                    # ダミー1バイト
                    w.write((0).to_bytes(1, byteorder='little', signed=False))

                    # 指し手で局面を進める
                    board.push_move16(move)

                    game_positions += 1
                    if game_positions % 10000 == 0:
                        print_log(f"  total positions: {game_positions}")

            except Exception as e:
                print(f"Finished reading games. Total games: {game_index}, total positions: {game_positions}")
                break

