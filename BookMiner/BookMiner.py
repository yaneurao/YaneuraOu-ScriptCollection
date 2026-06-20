import argparse
import datetime
import os
import time
import subprocess
import traceback
import queue
import cshogi
import sys
import re
from pathlib import Path

from dataclasses import dataclass
from typing import TypeAlias, Any, Callable, Generic, TypeVar
from threading import Lock, Thread
from itertools import zip_longest

try:
    import json5
except ImportError as exc:
    raise SystemExit("json5 package is required. Install it with: pip install json5") from exc

COMMON_LIB_DIR = Path(__file__).resolve().parent.parent / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneShogiLib import trim_sfen, make_time_stamp, flipped_sfen, flipped_move , trim_sfen_ply, PositionStr, enable_print_log, print_log

print = print_log
enable_print_log()

# ============================================================
#                     定数
# ============================================================

# やねうら王の定跡ファイルを書き出すフォルダ
BOOK_DIR       = "book"
BOOK_BACKUP_DIR= os.path.join(BOOK_DIR, "backup")
             
# このスクリプトの管理定跡ファイル名
BOOK_DB_NAME   = "book_miner"

# エンジン設定が書いてあるjson5ファイルのpath
ENGINE_SETTINGS_JSON_PATH    = "settings/engine_settings.json5"

# BookMiner本体設定が書いてあるjson5ファイルのpath
BOOK_MINER_SETTINGS_JSON_PATH = "settings/book_miner_settings.json5"

# peta_nextコマンドの開始局面集合。settings/book_miner_settings.json5 で上書きされる。
PETA_NEXT_START_SFENS_PATH = os.path.join(BOOK_DIR, "peta_start_sfens.txt")

# 開始局面のsfen文字列
SFEN_START      = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b -"
SFEN_START_PLY1 = "lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1" # 手数つき

# やねうら王定跡DBのheader
YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"

# ペタショック化された定跡ファイルのprefix
PETA_BOOK_DB_NAME = "peta_book"
PETA_SHOCK_ENGINE_NAME = "YO-MATERIAL.exe"
PETA_SHOCK_PROGRESS_INTERVAL = 10
BOOK_READ_PROGRESS_INTERVAL = 10000
BOOK_WRITE_PROGRESS_INTERVAL = 10000
TASK_QUEUE_PROGRESS_INTERVAL = 10.0
MINING_PROGRESS_INTERVAL = 60.0

# think_sfens.txt のファイル名
THINK_SFENS_NAME = "think_sfens.txt"

# 自動保存の間隔 [s]。settings/book_miner_settings.json5 で上書きされる。
AUTO_SAVE_INTERVAL = 3 * 60 * 60 # 3時間おき

# 定跡の最大手数。settings/book_miner_settings.json5 で上書きされる。
MAX_BOOK_PLY = 200

# VALUE, PLY の -∞
VALUE_MIN      = -(2**31)
PLY_MIN        = -(2**31)

# mate scoreはやねうら王体系に合わせる。score cpの異常値だけ±30000へ丸める。
VALUE_MATE                   =   32000
VALUE_EVAL_CLAMP             =   30000

# 旧BookMiner/やねうら王定跡に残っている MATE_VALUE=100000 体系のmate score。
OLD_BOOK_VALUE_MATE          =  100000
OLD_BOOK_MATE_THRESHOLD      =   99000

# `t`コマンドで思考させるときに垂直に何手分(固定で)掘るか。
THINK_COMMAND_PLY = 6 # 3

TASK_RESULT_DONE = "done"
TASK_RESULT_DEFERRED = "deferred"
MAX_TASK_DEFER_COUNT = 1000
TASK_DEFER_SLEEP_SECONDS = 0.01

# ============================================================
#                     型定義
# ============================================================

"""
[定跡フォーマット]

Sfen
MoveInfo * : 
    指し手 * :
        eval(評価値)
"""

# 局面文字列。"sfen "は省略してある。また末尾に手数はつかないものとする。(読み込み時にplyを分離)
Sfen    : TypeAlias = str

# 指し手文字列。usi_string。
MoveStr  : TypeAlias = str

# 評価値。不明ならNone。
Eval    : TypeAlias = int | None

# 指し手とその評価値
@dataclass
class MoveInfo:
    # 指し手
    move : MoveStr
    # 評価値
    eval : Eval

# 局面情報
@dataclass
class PositionInfo:
    # 指し手の集合
    moveinfos : list[MoveInfo]

    # 手数(初期局面をply=1とする)
    ply : int

    # その他、何か情報があれば…。

# 定跡本体
class Book:
    def __init__(self):

        # 定跡本体
        self.body : dict[Sfen,PositionInfo] = {}

        # 各スレッドが探索中である局面のsfen文字列
        self.searching_sfens : set[Sfen] = set()

        # このメンバーを操作するときのlock object
        self.lock : Lock = Lock()


@dataclass
class Task:
    # 定跡を掘る探索開始sfen
    sfen : Sfen

    # 上記のsfenの手数
    ply : int

    # 掘る範囲の評価値
    eval_limit : int

    # `t`コマンドで指定された position 文字列。Noneなら従来のsfen開始タスク。
    position_cmd : PositionStr | None = None

    # `t`コマンドで積まれたタスクの進捗表示用。
    job_id : int = 0

    # 他workerが同じ局面を探索中だったためにqueue末尾へ戻した回数。
    defer_count : int = 0


@dataclass
class TaskQueueJobProgress:
    # このjobで投入された対局棋譜数。
    total : int

    # このjobでworkerが受け取った対局棋譜数。
    taken : int = 0

    # このjobの完了ログを出力済みか。
    done_reported : bool = False


@dataclass
class BookMinerSettings:
    # 自動保存の間隔 [s]
    auto_save_interval_seconds : int = AUTO_SAVE_INTERVAL

    # この手数に到達したら、それ以上掘らない。
    max_book_ply : int = MAX_BOOK_PLY

    # peta_nextで辿り始める開始局面集合ファイル。
    peta_next_start_sfens_path : str = PETA_NEXT_START_SFENS_PATH

# ============================================================

T = TypeVar("T")

class ListRingQueue(Generic[T]):
    """固定長リングバッファ（listベース、FIFO）
    - put: 満杯なら sleep(1) で待機
    - get: 空なら IndexError
    """

    def __init__(self, maxsize: int):
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self._buf: list[T | None] = [None] * maxsize
        self._max = maxsize
        self._head = 0  # 次に取り出す位置
        self._tail = 0  # 次に書き込む位置
        self._count = 0
        self._lock = Lock()

    def put(self, item: T) -> None:
        """満杯なら sleep(1) で空きを待ってから追加"""
        while True:
            with self._lock:
                if self._count < self._max:
                    self._buf[self._tail] = item
                    self._tail = (self._tail + 1) % self._max
                    self._count += 1
                    return
            time.sleep(1)  # 満杯時は待機(バッファはふんだんにあるので1秒ぐらい待っても突然空にはならない)

    def _grow_unlocked(self) -> None:
        new_max = self._max * 2
        new_buf: list[T | None] = [None] * new_max
        for i in range(self._count):
            new_buf[i] = self._buf[(self._head + i) % self._max]
        self._buf = new_buf
        self._max = new_max
        self._head = 0
        self._tail = self._count

    def put_deferred(self, item: T) -> None:
        """worker内からの再投入用。満杯でもworkerを詰まらせないよう必要なら拡張する。"""
        with self._lock:
            if self._count >= self._max:
                self._grow_unlocked()
            self._buf[self._tail] = item
            self._tail = (self._tail + 1) % self._max
            self._count += 1

    def get(self) -> T:
        """空なら sleep(1) で待機してから取得"""
        while True:
            with self._lock:
                if self._count > 0:
                    item = self._buf[self._head]
                    self._buf[self._head] = None  # GCしやすく
                    self._head = (self._head + 1) % self._max
                    self._count -= 1
                    return item  # type: ignore
            time.sleep(1)  # 空のときは待機。そんなに空の状態が続くことはないはずなので…。

    def qsize(self) -> int:
        with self._lock:
            return self._count

    def full(self) -> bool:
        with self._lock:
            return self._count >= self._max

    def empty(self) -> bool:
        with self._lock:
            return self._count == 0

    def clear(self) -> None:
        with self._lock:
            self._buf = [None] * self._max
            self._head = self._tail = self._count = 0

# CLI表示用の10分間探索呼び出し回数
CALL_COUNT : int = 0
LAST_REPORT = time.time()

# ============================================================
#                     load/save
# ============================================================

def collect_yaneuraou_book_sfens(book:Book, ply_limit:int|None)->list[Sfen]:
    with book.lock:
        if ply_limit is None:
            sfens = list(book.body.keys())
        else:
            sfens = [
                sfen
                for sfen, position_info in book.body.items()
                if position_info.ply <= ply_limit
            ]

    sfens.sort()
    return sfens


def temp_book_path(path:str)->str:
    directory, filename = os.path.split(path)
    stem, extension = os.path.splitext(filename)
    if extension.lower() == ".db":
        temp_name = f"tmp-{stem}{extension}"
        return os.path.join(directory, temp_name) if directory else temp_name
    return f"{path}.tmp"


def book_progress_total_text(total:int|None)->str:
    return str(total) if total is not None else "?"


def print_book_write_start(path:str, total:int):
    print(f"[BookWriteStart] 0/{total} path={path}")


def print_book_write_progress(count:int, total:int):
    print(f"[BookWriteProgress] {count}/{total}")


def print_book_write_done(path:str, count:int, total:int):
    print(f"[BookWriteDone] {count}/{total} path={path}")


def write_yaneuraou_book_records(book:Book, path:str, ply_limit:int|None, sfens:list[Sfen]|None = None)->int:
    if sfens is None:
        sfens = collect_yaneuraou_book_sfens(book, ply_limit)

    dirpath = os.path.dirname(path)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)

    temp_path = temp_book_path(path)
    total = len(sfens)
    print_book_write_start(path, total)
    try:
        with open(temp_path, 'w', encoding='utf-8') as w:
            w.write(YANEURAOU_BOOK_HEADER_V1 + '\n')
            w.write(f"# NOE:{total}\n")
            for count, sfen in enumerate(sfens, 1):
                with book.lock:
                    position_info = book.body[sfen]
                    ply = position_info.ply
                    moveinfos = [
                        MoveInfo(move_info.move, move_info.eval)
                        for move_info in position_info.moveinfos
                    ]
                    if not moveinfos or any(move_info.eval is None for move_info in moveinfos):
                        raise Exception(f"unconsidered position in book: {sfen}")

                w.write(f'sfen {sfen} {ply}\n')

                moveinfos.sort(key=lambda x: x.eval, reverse=True) # type:ignore

                if len(moveinfos) >= 2 and moveinfos[0].eval == moveinfos[1].eval:
                    besteval = moveinfos[0].eval
                    for i in range(1, len(moveinfos)):
                        if besteval == moveinfos[i].eval:
                            moveinfos[i].eval -= 1 # type:ignore
                        else:
                            break

                for move_info in moveinfos:
                    w.write(f'{move_info.move} none {move_info.eval} 0\n')

                if count % BOOK_WRITE_PROGRESS_INTERVAL == 0:
                    print_book_write_progress(count, total)

        os.replace(temp_path, path)
        print_book_write_done(path, total, total)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise

    return len(sfens)


def save_book(book:Book, filepath:str):
    """
    メモリ上の定跡をやねうら王定跡フォーマットで保存する。
    book.body は思考済み局面だけを保持している前提。
    """

    print(f"start save_book , path = {filepath}")
    count = write_yaneuraou_book_records(book, filepath, None)
    print(f"..save_book has done, {count} positions.")
    return count


def save_book_backup(book:Book, save_dir:str, ply_limit:int|None = None)->str:
    sfens = collect_yaneuraou_book_sfens(book, ply_limit)
    ply_suffix = "" if ply_limit is None else f"_ply{ply_limit}"
    path = os.path.join(
        save_dir,
        f"{BOOK_DB_NAME}-{make_time_stamp()}_{len(sfens)}{ply_suffix}.db"
    )
    print(f"start save_book_backup , path = {path}")
    write_yaneuraou_book_records(book, path, ply_limit, sfens)
    print(f"..save_book_backup has done, {len(sfens)} positions.")
    return path


def load_book(book:Book, filepath:str, *, fast:bool = False):
    """
    やねうら王定跡フォーマットから、メモリに読み込み。
    bookのbodyはclear()されたあと読み込まれる。
    """

    print(f"start load_book , path = {filepath}, fast = {fast}")

    with book.lock:
        book.body.clear()

    if fast:
        read_bookminer_backup(book, filepath)
    else:
        read_yaneuraou_book(book, filepath)
    print(f"done..{len(book.body)} positions.")
    return book


def load_book_miner_settings(path:str = BOOK_MINER_SETTINGS_JSON_PATH)->BookMinerSettings:
    settings = BookMinerSettings()

    if not os.path.exists(path):
        print(f"Warning : BookMiner settings file not found. use default settings. path = {path}")
        return settings

    print(f"read BookMiner settings , path = {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw_settings = json5.load(f)

    if not isinstance(raw_settings, dict):
        raise Exception(f"invalid BookMiner settings file. root must be object. path = {path}")

    def read_positive_int(name:str, current_value:int)->int:
        value = raw_settings.get(name, current_value)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise Exception(f"invalid BookMiner setting. {name} must be positive integer. value = {value}")
        return value

    def read_non_empty_str(name:str, current_value:str)->str:
        value = raw_settings.get(name, current_value)
        if not isinstance(value, str) or not value.strip():
            raise Exception(f"invalid BookMiner setting. {name} must be non-empty string. value = {value}")
        return value.strip()

    settings.auto_save_interval_seconds = read_positive_int(
        "auto_save_interval_seconds",
        settings.auto_save_interval_seconds,
    )
    settings.max_book_ply = read_positive_int(
        "max_book_ply",
        settings.max_book_ply,
    )
    settings.peta_next_start_sfens_path = read_non_empty_str(
        "peta_next_start_sfens_path",
        settings.peta_next_start_sfens_path,
    )

    print(
        "BookMiner settings : "
        f"auto_save_interval_seconds = {settings.auto_save_interval_seconds}, "
        f"max_book_ply = {settings.max_book_ply}, "
        f"peta_next_start_sfens_path = {settings.peta_next_start_sfens_path}"
    )
    return settings


def dump_position(sfen:Sfen, position_info:PositionInfo, move: MoveStr | None = None):
    """
    定跡の局面を1つdumpする。
    """

    moveinfos = position_info.moveinfos
    print(f"{sfen} {position_info.ply}")

    if move:
        # 指し手が指定されているなら、この指し手が登録されているかだけ出力
        for moveinfo in moveinfos:
            if moveinfo.move == move:
                print(f"  {moveinfo.move},{moveinfo.eval}")
                break

    else:
        for moveinfo in moveinfos:
            print(f"  {moveinfo.move},{moveinfo.eval}")


def dump_book(book:Book):
    """
    定跡をdumpする。
    """
    with book.lock:
        items = list(book.body.items())
    for sfen, position_info in items:
        dump_position(sfen, position_info)

# ============================================================
#                     USI Engine
# ============================================================

def parse_position_string(s:PositionStr)->tuple[Sfen, list[MoveStr]]:
    """
    positionコマンドで指定する文字列を、開始sfen(手数つき)とmovesに分解する。
    """
    s = s.strip()
    if s.startswith('position '):
        s = s[len('position '):].strip()

    sfen, sep, moves_str = s.partition(' moves ')
    moves = moves_str.split() if sep else []

    sfen = sfen.strip()
    if sfen == 'startpos':
        sfen = SFEN_START_PLY1
    elif sfen.startswith('sfen '):
        sfen = sfen[len('sfen '):].strip()

    return sfen, moves


def checked_push_usi(board:cshogi.Board, move:MoveStr, *, context:str = ""):
    """
    cshogi.Board.push_usi() は非合法手で例外を投げず 0 を返す場合がある。
    think_sfens.txt 由来の手順はここで明示的に合法手チェックしてから進める。
    """
    try:
        move32 = board.move_from_usi(move)
    except Exception as exc:
        detail = f"invalid usi move: {move}"
        if context:
            detail += f" / {context}"
        detail += f" / {board.sfen()}"
        raise ValueError(detail) from exc

    if not move32 or not board.is_legal(move32):
        detail = f"illegal usi move: {move}"
        if context:
            detail += f" / {context}"
        detail += f" / {board.sfen()}"
        raise ValueError(detail)

    board.push(move32)


def append_position_move(position_cmd:PositionStr, move:MoveStr)->PositionStr:
    position_cmd = position_cmd.strip()
    if ' moves ' in position_cmd:
        return f"{position_cmd} {move}"
    return f"{position_cmd} moves {move}"


def decode_position_string(s : PositionStr)->Sfen:
    """
    positionコマンドで指定する文字列
        startpos
        startpos moves ..
        SFEN文字列
        SFEN文字列 moves..
    をdecodeして、通常のSfen文字列(plyつき)に変換する。
    """
    sfen, moves = parse_position_string(s)

    board = cshogi.Board(sfen) # type:ignore
    for move in moves:
        checked_push_usi(board, move, context=s)
    
    return board.sfen()


def legal_move_count_for_position(s:PositionStr)->int:
    """
    positionコマンドで指定できる局面を展開し、その局面の合法手数を返す。
    """
    sfen, moves = parse_position_string(s)

    board = cshogi.Board(sfen) # type:ignore
    for move in moves:
        checked_push_usi(board, move, context=s)

    return len(board.legal_moves) # type:ignore


def index_of(a:list[Any] | str, x:Any):
    ''' 配列 a から xを探し、何番目の要素であるかを返す。見つからなければ-1が返る。'''
    if x in a:
        return a.index(x)
    return -1

def clamp_eval(x:int)->int:
    return min(VALUE_EVAL_CLAMP, max(-VALUE_EVAL_CLAMP, x))


def usi_mate_to_yaneuraou_eval(mate_ply_str:str)->Eval:
    if mate_ply_str == '+' or mate_ply_str == '-':
        return VALUE_MATE - 1 if mate_ply_str == '+' else -VALUE_MATE + 1

    mate_ply = int(mate_ply_str)
    if mate_ply > 0:
        return VALUE_MATE - mate_ply
    if mate_ply < 0:
        return -VALUE_MATE - mate_ply

    return VALUE_MATE


def evalstr_to_int(s1:str,s2:str)->Eval:
    ''' cp 100 なら 100。mate 1 なら 31999 を返す。'''
    if s1 == 'cp':
        return clamp_eval(int(s2))
    if s1 == "mate":
        return usi_mate_to_yaneuraou_eval(s2)
    raise Exception(f"Error! : parse error {s1},{s2}")


@dataclass
class GlobalSettings:
    '''探索関係の共通設定を集めた構造体'''

    # エンジン設定(settings/engine_settings.json5をdeserializeしたもの)
    engine_settings : Any

    # エンジンからの出力をデバッグのための標準出力に出してみるモード
    debug_engine : bool

    # 思考する時の最初のMultiPVの数。
    multipv : int

    # MultiPV Nで思考し、bestとN番目の指し手の評価値の差がこの範囲になければ、Nを広げて再探索する。
    multipv_delta : int

    # 全スレッドを停止させるかのフラグ
    quit : bool

    # GUI経由で起動されているか。
    from_gui : bool = False

@dataclass
class ThreadSettings:
    '''探索スレッド固有の設定を集めた構造体'''

    # スレッドID
    thread_id : int

    # エンジンのpath
    engine_path : str

    # エンジンの探索node数
    engine_nodes : int

    # エンジン側からreadyokを受け取ったか。(これが来るまで次のエンジンの起動をしない)
    readyok : bool


class Engine:
    '''エンジン操作クラス'''
    def __init__(self, global_settings:GlobalSettings, thread_settings:ThreadSettings):
        '''
        global_settings : 全体設定
        thread_settings : スレッド設定
        '''
        self.global_settings = global_settings
        self.thread_settings = thread_settings

        # 探索中のsfen
        self.search_sfen = ""

        path : str = thread_settings.engine_path

        # 思考エンジンのprocessの起動。
        # sshしたいなら、pathに"ssh 2698a suisho6"のようなsshコマンドを書いておけば良い。
        if path.startswith("ssh"):
            # この場合、コマンドはlistで渡してやらないといけないらしい。
            self.engine = subprocess.Popen(path.split(), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                encoding="UTF-8",
                                                errors="replace")

        else:
            # 相対pathで呼び出すと評価関数を読み込めない問題があるっぽい..(やねうら王V9.00系で発覚)
            # cwdを変更しているから、cwd相対になったときにnn.binがそこにはなくてまずいのか…。そうか…。
            path = os.path.abspath(os.path.normpath(path))

            if not os.path.isfile(path):
                self.raise_exception(f"Engine not Found")

            # 思考エンジンの実行ファイルが存在するフォルダをworking directoryとして
            # 指定しておかないと評価関数ファイルなど、実行ファイル相対で配置するファイルが
            # 思考エンジンから読み込めなくてエラーになるのでworking directorを指定する。
            working_directory = os.path.dirname(path)

            self.engine = subprocess.Popen(path, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                cwd=working_directory,
                                                encoding="UTF-8",
                                                errors="replace")


    def isready(self):
        # 思考エンジンに対してisreadyコマンドを送信して、
        # エンジン側からreadyokが返ってくるのを待つ。
        self.send_usi("isready")

        # 別スレッドで実行して待機する
        def wait_readyok():
            self.wait_usi("readyok") # readyokを待つ
            self.thread_settings.readyok = True

        Thread(target=wait_readyok, daemon=True).start()

        # Debug用に実際に探索させてみる。
        # print_log(self.go("startpos"))

    # 新しいゲーム(置換表等をクリアして欲しいので…)
    def send_newgame(self):
        self.send_usi("usinewgame")
        self.send_usi("isready")
        self.wait_usi("readyok") # readyokを待つ

    def send_usi(self, command:str):
        ''' 思考エンジンに対してUSIコマンドを送信する。 '''

        # デバッグモードならエンジンへの入出力をすべて標準出力へ。
        if self.global_settings.debug_engine:
            print(f'[{self.thread_settings.thread_id}]<{command}')

        self.engine.stdin.write(command+"\n") # type:ignore
        self.engine.stdin.flush()             # type:ignore

        # print_log(f"{self.thread_settings.thread_id} < {command}")

    def receive_usi(self)->str:
        ''' 思考エンジンから1行もらう。改行は取り除いて返す。'''
        mes = self.engine.stdout.readline().strip() # type:ignore

        # デバッグモードならエンジンへの入出力をすべて標準出力へ。
        if self.global_settings.debug_engine:
            print(f'[{self.thread_settings.thread_id}]>{mes}')

        # エンジンのprocessが死んでたら例外を出す。
        if self.engine.poll() is not None:
            self.raise_exception(f"Engine is terminated.")

        # print_log(f"{self.thread_settings.thread_id} > {mes}")

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

    def go(self, sfen:Sfen, node_ratio:float )->list[MoveInfo]:
        '''
        思考エンジンに探索させる。
        sfen : 局面(USIのpositionコマンドで指定できる形式)
        node_ratio : 今回探索するノード数に掛け算する係数

        返し値 : 読み筋(PV)の初手のlistとその評価値が返る。
                例) [('7g7f',120),('6i7h',39)]
                mate 1は、99999と数値化されて返る。

            例)
            info nps 241 time 41494 nodes 10014 hashfull 0 multipv 1 depth 18 score cp 39 pv 7g7f 8c8d 6i7h 8d8e 8h7g 3c3d 7i6h 4a3b 3i3h 7a6b 2g2f 4c4d 2f2e 2b3c 3g3f 3a4b 3h3g 4b4c
            info nps 241 time 41494 nodes 10014 hashfull 0 multipv 2 depth 17 score cp 39 pv 6i7h 4a3b 7g7f 8c8d 2g2f 8d8e 8h7g 3c3d 7i6h 4c4d 4g4f 3a4b 3i4h 7a6b 4h4g 4b4c 5i6i                
                        

        ⇨　
            multipv対策として

            info depth 15 seldepth 24 score cp 90 multipv 1 nodes 798752 nps 4726343 hashfull 2 time 169 pv 6i7h 3c3d 7g7f 8c8d 2g2f 8d8e 2f2e 8e8f 8g8f 8b8f P*8g 8f8b 2e2d 2c2d 2h2d 2b8h+ 7i8h 3a2b 2d2h 1c1d 3i3h 4a3b 8h7g P*2c 9g9f
            info depth 15 seldepth 23 score cp 75 multipv 2 nodes 798752 nps 4726343 hashfull 2 time 169 pv 2g2f 8c8d 6i7h 8d8e 2f2e 4a3b 3i3h 9c9d 9g9f 1c1d 2e2d 2c2d 2h2d P*2c 2d2h 8e8f
            info depth 15 seldepth 25 score cp 58 multipv 3 nodes 798752 nps 4726343 hashfull 2 time 169 pv 7g7f 3c3d 6i7h 8c8d 2g2f 8d8e 2f2e 4a3b 2e2d 2c2d 2h2d 8e8f 8g8f 8b8f 8h2b+ 3a2b B*7g 8f8b P*8c 8b6b 2d3d 6c6d 7g2b+ 3b2b 3d3a+
            info depth 15 seldepth 21 score cp 57 multipv 4 nodes 798752 nps 4726343 hashfull 2 time 169 pv 1g1f 8c8d 2g2f 8d8e 2f2e 4a3b 6i7h 1c1d 9g9f 5a5b 3i3h 7a7b 2e2d 2c2d 2h2d P*2c 

            この形式に対応する。
        '''
        multipv_step = max(1, self.global_settings.multipv)
        multipv_limit = max(1, legal_move_count_for_position(sfen))

        # "MultiPV"の値を決定する。
        multipv = min(multipv_step, multipv_limit)
        self.send_usi(f"multipv {multipv}")

        # 1番目とN番目の指し手の評価値の差がこの範囲に収まらないなら再探索。
        multipv_delta = self.global_settings.multipv_delta

        # "position"コマンドを思考エンジンに送信する。
        self.search_sfen = sfen
        self.send_usi(f"position {sfen}")

        # 探索ノード数
        nodes = int(self.thread_settings.engine_nodes * node_ratio)
        half_nodes = nodes // 2

        # "go"コマンドを思考エンジンに送信する。
        self.send_usi(f"go nodes {nodes}")

        # "bestmove"は必ず返ってくるはずなのでそれを待つ。
        # 読み筋(PV)の初手と最終的な評価値とbestmoveをparseして返す。

        moves : dict[int,MoveInfo] = {}

        while True:
            ret = self.receive_usi()
            rets = ret.split()
            # これはUSIプロトコルでエンジン側から送られてくる文字列の"bestmove"
            if "bestmove" in ret:
                # この直前にPVが返ってきているはず…。
                node : list[MoveInfo] = []
                # multipvの指し手を1番目から列挙
                for i in range(1,1000):
                    if not i in moves:
                        break
                    node += moves[i],
                
                # 再探索条件を満たしているなら、再度思考コマンドを送って探索を継続
                # 候補手がmultipvの個数だけあって、1番目と末尾の指して手の評価値の差がδ以内であるなら、multipvの範囲を少しずつ増やす。
                # nodesは初期値の半分にする。
                if len(node) == multipv and abs(node[0].eval - node[-1].eval) <= multipv_delta: # type:ignore
                    if multipv >= multipv_limit:
                        return node

                    # multipvの範囲を広げて再度"go"コマンドを思考エンジンに送信する。
                    multipv = min(multipv + multipv_step, multipv_limit)
                    nodes = half_nodes
                    self.send_usi(f"multipv {multipv}")
                    self.send_usi(f"go nodes {nodes}")
                    continue

                return node

            else:
                # 読み筋に対して、そのpvの初手を蓄積していく。
                # また最終的な評価値も保存しておく。
                
                # info nodes XX score cp YY pv ZZ ... の形なので、"cp"と"pv"を起点として、その直後の文字を取得する。
                if not rets or rets[0]!='info':
                    # infoと違う。何か関係ないメッセージっぽい。
                    continue
                
                mpv_index = index_of(rets,'multipv')
                # ⇑ one replyだと'multipv'の文字は見つからない。その場合は -1 が返るので注意。
                mpv = 1 if mpv_index == -1 else int(rets[mpv_index + 1])

                idx1 = index_of(rets,'score')
                idx2 = index_of(rets,'pv')
                if idx1 != -1 and idx2 != -1:
                    eval = evalstr_to_int(rets[idx1+1],rets[idx1+2])

                    # 'pv'の次のtokenが存在することを仮定している。
                    # 実戦だとこの指し手が'resign'とか'win'の可能性もあるが、評価値が先に振り切るので定跡掘る時には考えない。
                    move = rets[idx2+1]

                    if move == 'win' or move == 'resign':
                        continue
                    
                    # 指し手と評価値を登録しておく。
                    moves[mpv] = MoveInfo(move, eval)


    def raise_exception(self, error_message:str):
        ''' 例外を発生させる。エンジンの詳細を出力する。'''
        t = self.thread_settings
        raise Exception(f"{error_message} , engine_thread_id = {t.thread_id} , search_sfen : {self.search_sfen}")


# ============================================================
#                     USI Engine Manager
# ============================================================

class EngineManager:

    def __init__(self, book_miner_settings:BookMinerSettings, from_gui:bool = False):

        print("initialize the engines..")
        print("[StartupStage] stage=engine_init message=エンジン起動中")
        self.book_miner_settings = book_miner_settings

        # エンジン設定の読み込み    
        with open(ENGINE_SETTINGS_JSON_PATH,"r",encoding="utf-8") as f:
            engine_settings : list[Any] = json5.load(f)
        total_engines = sum(int(engine_setting["multi"]) for engine_setting in engine_settings)
        print(f"[EngineInitStart] 0/{total_engines}")

        global_settings = GlobalSettings(
            engine_settings       = engine_settings,
            multipv               = 4,
            multipv_delta         = 100,
            quit                  = False,
            debug_engine          = False,
            from_gui              = from_gui,
        )
        self.global_settings = global_settings
        self.task_progress_lock = Lock()
        self.task_progress_total = 0
        self.task_progress_taken = 0
        self.task_progress_last_report = 0.0
        self.task_progress_jobs : dict[int, TaskQueueJobProgress] = {}
        self.mining_progress_lock = Lock()
        self.mining_progress_last_report = 0.0

        engines : list[Engine] = []
        last_started_count = 0

        def ready_engine_count()->int:
            return sum(1 for engine in engines if engine.thread_settings.readyok)

        def report_engine_launch_progress():
            nonlocal last_started_count
            started_count = len(engines)
            if started_count == last_started_count:
                return
            last_started_count = started_count
            print(
                f"[EngineInitProgress] {started_count}/{total_engines} "
                f"ready={ready_engine_count()}"
            )

        id = 0
        for engine_setting in engine_settings:

            for _ in range(engine_setting['multi']):

                thread_settings = ThreadSettings(
                    thread_id              = id,
                    engine_path            = engine_setting["path"],
                    engine_nodes           = engine_setting['nodes'],
                    readyok                = False
                )
                id += 1

                print(f"  engine {id} , start .. path = {thread_settings.engine_path}")
                engine = Engine(global_settings, thread_settings)
                engine.isready()
                engines.append(engine)

                # ここでsleepしとかないと、ssh接続が切断されうる。
                time.sleep(0.3)
                report_engine_launch_progress()

        # 全エンジンがreadyokになるのを待つ
        last_ready_count = -1
        while True:
            ready_count = ready_engine_count()
            if ready_count != last_ready_count:
                if ready_count == total_engines:
                    print(f"[EngineInitDone] {ready_count}/{total_engines}")
                else:
                    print(f"[EngineReadyProgress] {ready_count}/{total_engines}")
                last_ready_count = ready_count
            if ready_count == total_engines:
                break
            time.sleep(1)

        print("all engines are ready.")

        self.engines = engines

    def reached_max_book_ply(self, ply:int)->bool:
        return ply >= self.book_miner_settings.max_book_ply

    def print_reached_max_book_ply(self, sfen:Sfen, ply:int):
        print(
            "max_book_ply reached. "
            f"ply = {ply}, max_book_ply = {self.book_miner_settings.max_book_ply}, "
            f"sfen = {sfen}"
        )

    def engine_test(self):
        """
        エンジンのテストコード
        """
        engine = self.engines[0]
        engine.send_newgame()
        moveinfos = engine.go(SFEN_START, 1.0)
        position_info = PositionInfo( moveinfos , 1)
        dump_position(SFEN_START, position_info)


    def think_sfen_once(self, book:Book, engine:Engine, sfen:Sfen, ply:int, last_thinking_ply:int, visited:set[Sfen]):
        """
        1局面について、未思考なら思考してbookにマージする。
        """
        global CALL_COUNT, LAST_REPORT

        current_sfen = sfen
        current_sfen_f = flipped_sfen(current_sfen)

        if self.reached_max_book_ply(ply):
            self.print_reached_max_book_ply(current_sfen, ply)
            return None, current_sfen, last_thinking_ply, TASK_RESULT_DONE

        with book.lock:
            if current_sfen in visited or current_sfen_f in visited:
                return None, current_sfen, last_thinking_ply, TASK_RESULT_DONE

            if current_sfen in book.searching_sfens or current_sfen_f in book.searching_sfens:
                # 他のスレッドが探索中なので、このtaskはqueue末尾へ戻して後で再試行する。
                return None, current_sfen, last_thinking_ply, TASK_RESULT_DEFERRED

            if current_sfen in book.body:
                position_info = book.body[current_sfen]
            elif current_sfen_f in book.body:
                position_info = book.body[current_sfen_f]
                current_sfen = current_sfen_f
            else:
                position_info = None

            book.searching_sfens.add(current_sfen)
            visited.add(current_sfen)

        try:
            if not position_info or not has_considered(position_info):
                # 2つ目以降の局面では、1手前で探索しているので7掛けで良い。
                # ⇨ 途中で合流した場合、1手前で探索してるとは限らない。last_thinking_plyを用いるように変更する。
                node_ratio = 0.7 if last_thinking_ply + 1 == ply else 1.0

                # この局面について探索するのでこれをログに出力しておく。
                print(f"[{engine.thread_settings.thread_id}] {current_sfen} {ply} , {node_ratio}")

                position_info_new = engine.go(current_sfen, node_ratio)
                last_thinking_ply = ply # この局面で思考したので更新する。
                book_position_count = None

                with book.lock:
                    if position_info:
                        # 新規局面ではないので、マージ。
                        for moveinfo_new in position_info_new:
                            for moveinfo in position_info.moveinfos:
                                if moveinfo_new.move == moveinfo.move:
                                    moveinfo.eval = moveinfo_new.eval
                                    break
                            else:
                                position_info.moveinfos.append(moveinfo_new)
                    else:
                        # 新規局面なので定跡にそのまま追加
                        position_info = PositionInfo(position_info_new, ply)
                        book.body[current_sfen] = position_info

                    # できればbestな順で掘りたいので、evalで降順に並び替える。
                    # valueがないところは、VALUE_MIN扱い。
                    position_info.moveinfos.sort(key=lambda x: x.eval if x.eval is not None else VALUE_MIN, reverse=True)
                    book_position_count = len(book.body)

                    if not self.global_settings.from_gui:
                        # CLIでは従来通り、探索呼び出し回数を10分ごとに出力する。
                        CALL_COUNT += 1
                        now = time.time()
                        if now - LAST_REPORT >= 600:
                            print(f"過去10分の呼び出し回数: {CALL_COUNT}")
                            CALL_COUNT = 0
                            LAST_REPORT = now

                if self.global_settings.from_gui and book_position_count is not None:
                    self.report_mining_progress(book_position_count)

            return position_info, current_sfen, last_thinking_ply, TASK_RESULT_DONE

        finally:
            with book.lock:
                book.searching_sfens.discard(current_sfen)

    def get_book_position_info(self, book:Book, sfen:Sfen)->tuple[PositionInfo | None, bool]:
        """
        book上の局面情報を返す。flip側でhitしたときは第2戻り値をTrueにする。
        """
        sfen_f = flipped_sfen(sfen)

        with book.lock:
            if sfen in book.body:
                return book.body[sfen], False
            if sfen_f in book.body:
                return book.body[sfen_f], True

        return None, False

    def get_book_move_eval(self, book:Book, sfen:Sfen, move:MoveStr)->Eval:
        """
        book上でsfenからmoveを指したときの評価値を返す。
        moveがbookに無い、または評価値が無い場合はNoneを返す。
        """
        position_info, flipped_bookhit = self.get_book_position_info(book, sfen)
        if position_info is None:
            return None

        book_move = flipped_move(move) if flipped_bookhit else move
        for moveinfo in position_info.moveinfos:
            if moveinfo.move == book_move:
                return moveinfo.eval

        return None


    def start_thinking(self, book:Book, engine:Engine, task:Task):
        """
        スレッドを生成して呼び出される。
        engineを用いて、sfenの局面から開始して思考していく。
        """
        sfen        = task.sfen
        ply         = task.ply
        eval_limit  = task.eval_limit

        engine.send_newgame()

        # 開始時の手数を保存しておく。開始局面の次以降は探索nodeを減らす。
        last_thinking_ply = PLY_MIN

        # `t`コマンドで垂直に掘る時の残り手数
        rest_ply = THINK_COMMAND_PLY

        # 現在探索中の局面
        current_sfen = sfen

        # 千日手を防ぐために自分が訪問した局面だけ持っておく。
        visited : set[Sfen] = set()

        while True:

            if rest_ply == 0:
                break

            # 現局面を必要なら思考する。
            position_info, current_sfen, last_thinking_ply, status = self.think_sfen_once(book, engine, current_sfen, ply, last_thinking_ply, visited)
            if status == TASK_RESULT_DEFERRED:
                return TASK_RESULT_DEFERRED
            if position_info is None:
                return TASK_RESULT_DONE

            # 次の局面を辿る。
            # ここから先は棋譜で指定された経路ではなく、BookMinerがbest lineを伸ばす。
            # そのため、bestがeval_limitを超えていたら、ここで延長を止める。
            besteval , _ = get_best(position_info)

            if besteval is None:
                # 思考したはずなのにbestevalがない。詰みの局面か？
                return TASK_RESULT_DONE

            if abs(besteval) > eval_limit:
                return TASK_RESULT_DONE

            board = cshogi.Board(current_sfen)
            for moveinfo in position_info.moveinfos:

                eval = moveinfo.eval
                if eval is None or abs(eval) > eval_limit:
                    continue

                move = moveinfo.move

                checked_push_usi(board, move, context=current_sfen)
                next_sfen = trim_sfen(board.sfen())

                current_sfen = next_sfen
                rest_ply -= 1
                break # best_moveがeval_limitの範囲である限り辿っていくだけ
            else:
                # 条件に当てはまる指し手が見つからなかったので、これ以上掘り進めない。
                return TASK_RESULT_DONE
            
            ply += 1

        return TASK_RESULT_DONE


    def start_thinking_position(self, book:Book, engine:Engine, task:Task):
        """
        `startpos moves ...` 形式の1行を、棋譜の指し手通りに辿って掘る。
        DB上の定跡木から外へ出る枝ではeval_limitを見て、条件を満たす場合だけ先へ進む。
        棋譜末端まで到達できたら、そこからTHINK_COMMAND_PLYだけbest lineを掘る。
        """
        if task.position_cmd is None:
            return TASK_RESULT_DONE

        eval_limit = task.eval_limit
        sfen, moves = parse_position_string(task.position_cmd)
        board = cshogi.Board(sfen) # type:ignore

        engine.send_newgame()

        visited : set[Sfen] = set()
        last_thinking_ply = PLY_MIN

        for move in moves:
            current_sfen, ply = trim_sfen_ply(board.sfen())

            if self.reached_max_book_ply(ply):
                self.print_reached_max_book_ply(current_sfen, ply)
                return TASK_RESULT_DONE

            # 現局面が未思考なら、棋譜上の局面としてbookに取り込む。
            position_info, _ = self.get_book_position_info(book, current_sfen)
            if position_info is None or not has_considered(position_info):
                position_info, _, last_thinking_ply, status = self.think_sfen_once(book, engine, current_sfen, ply, last_thinking_ply, visited)
                if status == TASK_RESULT_DEFERRED:
                    return TASK_RESULT_DEFERRED
                if position_info is None:
                    return TASK_RESULT_DONE

            lookahead_board = cshogi.Board(board.sfen()) # type:ignore
            checked_push_usi(lookahead_board, move, context=task.position_cmd)
            next_sfen = trim_sfen(lookahead_board.sfen())
            next_position_info, _ = self.get_book_position_info(book, next_sfen)

            # 次局面がbook上の思考済みノードでないなら、この手は定跡木から外へ出る枝。
            # その枝の評価値がeval_limitを超えている場合は、棋譜末端までは辿らずに止める。
            if next_position_info is None or not has_considered(next_position_info):
                move_eval = self.get_book_move_eval(book, current_sfen, move)
                if isinstance(move_eval, int) and abs(move_eval) > eval_limit:
                    return TASK_RESULT_DONE

            checked_push_usi(board, move, context=task.position_cmd)

        leaf_sfen, leaf_ply = trim_sfen_ply(board.sfen())
        return self.start_thinking(book, engine, Task(leaf_sfen, leaf_ply, eval_limit))

    """
    def parallel_think(self, book:Book, think_sfens:list[Sfen]):
        # N並列で思考対象局面を縦型探索していく。
        
        task_queue : queue.Queue[Sfen | None] = queue.Queue()
        for sfen in think_sfens:
            task_queue.put(sfen)

        def thread_func(book:Book, id:int):
            while True:
                task = task_queue.get()
                if task is None:
                    break
                self.start_thinking(book, id, task)
                task_queue.task_done()

        # 生成するスレッド数
        N = len(self.engines)

        # スレッドのリスト
        threads : list[Thread] = []
        for i in range(N):
            t = Thread(target=thread_func, args=(book, i))
            t.start()
            threads.append(t)

        task_queue.join()

        # スレッドに終了を伝える（None を N 個投入）
        for _ in range(N):
            task_queue.put(None)

        for t in threads:
            t.join()

        print("All tasks completed.")
    """

    # ============================================================
    #               Task Worker
    # ============================================================

    # 1. start_task_workers()でworker()を開始させる
    # 2. put_task()でTaskを積む
    # 3. join_task()でworkerの終了を待つ

    def start_task_workers(self, book:Book):
        # このqueueにtaskを積むとそれが処理されていく。
        self.task_queue : ListRingQueue = ListRingQueue(len(self.engines))

        threads : list[Thread] = []
        for engine in self.engines:
            thread = Thread(target=self.worker, args = (book, engine), daemon=True)
            thread.start()
            threads.append(thread)
        self.threads = threads

    def worker(self, book:Book, engine:Engine):
        # start_task_workers()で開始されたworker

        while True:
            task : Task | None = None
            try:
                task = self.task_queue.get()

                # 局面を掘っていく。
                if task.position_cmd is not None:
                    result = self.start_thinking_position(book, engine, task)
                else:
                    result = self.start_thinking(book, engine, task)

                if result == TASK_RESULT_DEFERRED:
                    self.defer_task(task)
                    continue

                self.report_task_queue_progress(task)

            except Exception as e:
                print(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")
                if task is not None:
                    self.report_task_queue_progress(task)


    def put_task(self, task:Task):
        # (taskがなければ)taskを積む
        self.task_queue.put(task)  # 満杯ならここでブロック

    def defer_task(self, task:Task):
        task.defer_count += 1
        if task.defer_count > MAX_TASK_DEFER_COUNT:
            print(
                f"[TaskQueueDeferLimit] job={task.job_id} "
                f"defer_count={task.defer_count} position={task.position_cmd or task.sfen}"
            )
            self.report_task_queue_progress(task)
            return

        if task.defer_count == 1 or task.defer_count % 100 == 0:
            print(
                f"[TaskQueueDeferred] job={task.job_id} "
                f"defer_count={task.defer_count} position={task.position_cmd or task.sfen}"
            )
        time.sleep(TASK_DEFER_SLEEP_SECONDS)
        self.task_queue.put_deferred(task)

    def join_task(self):
        # 全task queueのjoin待ち
        pass
        # これ実装できない。

    def start_task_queue_progress(self, job_id:int, added_count:int, path:str, eval_limit:int):
        with self.task_progress_lock:
            self.task_progress_total += added_count
            self.task_progress_jobs[job_id] = TaskQueueJobProgress(
                total=added_count,
                done_reported=added_count == 0,
            )
            total = self.task_progress_total
            taken = self.task_progress_taken
            remaining = max(total - taken, 0)
            self.task_progress_last_report = time.time()

        print(
            f"[TaskQueueStart] {taken}/{total} "
            f"job={job_id} job_progress=0/{added_count} job_remaining={added_count} "
            f"added={added_count} remaining={remaining} path={path} eval_limit={eval_limit}"
        )
        if added_count == 0:
            print(
                f"[TaskQueueJobDone] {taken}/{total} "
                f"job={job_id} job_progress=0/0 job_remaining=0 remaining={remaining}"
            )
        if remaining == 0:
            print(
                f"[TaskQueueDone] {taken}/{total} "
                f"job={job_id} job_progress=0/{added_count} job_remaining=0 remaining=0"
            )

    def report_task_queue_progress(self, task:Task):
        if task.job_id <= 0:
            return

        now = time.time()
        with self.task_progress_lock:
            self.task_progress_taken += 1
            taken = self.task_progress_taken
            total = self.task_progress_total
            job_progress = self.task_progress_jobs.get(task.job_id)
            if job_progress is None:
                job_progress = TaskQueueJobProgress(total=0)
                self.task_progress_jobs[task.job_id] = job_progress
            job_progress.taken += 1
            job_taken = job_progress.taken
            job_total = job_progress.total
            remaining = max(total - taken, 0)
            job_remaining = max(job_total - job_taken, 0)
            should_report_job_done = (
                job_total > 0
                and job_taken >= job_total
                and not job_progress.done_reported
            )
            if should_report_job_done:
                job_progress.done_reported = True
            should_report = remaining == 0 or should_report_job_done
            if not should_report:
                should_report = now - self.task_progress_last_report >= TASK_QUEUE_PROGRESS_INTERVAL
            if not should_report:
                return
            self.task_progress_last_report = now

        tag = "TaskQueueDone" if remaining == 0 else "TaskQueueProgress"
        print(
            f"[{tag}] {taken}/{total} "
            f"job={task.job_id} job_progress={job_taken}/{job_total} "
            f"job_remaining={job_remaining} remaining={remaining}"
        )
        if should_report_job_done:
            print(
                f"[TaskQueueJobDone] {taken}/{total} "
                f"job={task.job_id} job_progress={job_taken}/{job_total} "
                f"job_remaining=0 remaining={remaining}"
            )

    def report_mining_progress(self, position_count:int, force:bool = False):
        now = time.time()
        with self.mining_progress_lock:
            if not force and now - self.mining_progress_last_report < MINING_PROGRESS_INTERVAL:
                return
            self.mining_progress_last_report = now

        print(f"[MiningProgress] positions={position_count}")

# ============================================================
#                     helper functions
# ============================================================

# tコマンドのtask番号。(連番で増えていく)
job_counter : int = 0

def get_job_counter()->int:
    global job_counter
    job_counter += 1
    return job_counter


def get_best(infos:PositionInfo)->tuple[Eval, MoveStr]:
    """
    最善手のevalを返す。evalがすべてNoneであればNoneを返す。
    """
    bestEval : int = VALUE_MIN
    bestMove : MoveStr = "none"
    for info in infos.moveinfos:
        if info.eval is not None and bestEval < info.eval:
            bestEval = info.eval
            bestMove = info.move

    return (bestEval, bestMove) if bestEval != VALUE_MIN else (None , "none")


def has_considered(infos:PositionInfo)->bool:
    """
    思考済みの局面であるかを判定して返す。
    """
    return get_best(infos)[0] is not None

def write_to_yaneuraou_book(book:Book, save_dir:str, ply_limit:int|None = None)->str:
    """
    やねうら王 定跡形式で書き出す。
    """
    print(f"write yaneuraou book , save_dir = {save_dir} , ply_limit = {ply_limit if ply_limit is not None else 'all'} , sorting..")

    # C++でsortしたほうがいいわ。ここでsortするの時間かかる。
    # print("sorting..")
    # sorted_book = dict(sorted(book.items()))  # キーの昇順
    # print("..finish.")
    # ⇨ sort済みにして、NOEつけて書き出すことも考えられるか…。まあいいや。
	#					// 2行目には
	#					// # NOE:258
    # のようにレコード数を書き出す。

    path = save_book_backup(book, save_dir, ply_limit)
    print(f"write path = {path}")
    print(f"..w command write has done. path = {path}")
    return path


def bfs_for_ply(book:Book):
    """
    bfsして手数を記入していく。
    """
    print("bfs for ply")

    visited : set[Sfen] = set()
    next_queue : list[Sfen] = [SFEN_START]

    ply = 1
    # 書き出した局面数
    c = 0

    while next_queue:
        # 訪問済みの局面を除外して重複を除去しておく。
        next_queue = list(set([sfen for sfen in next_queue if sfen not in visited and flipped_sfen(sfen) not in visited]))

        print(f"bfs ply = {ply} , num = {len(next_queue)}")
        c += len(next_queue)

        sfen_queue = next_queue
        next_queue = []

        for sfen in sfen_queue:
            # この局面はbookに存在するはずで…。(初期局面のことは知らんが…)
            visited.add(sfen)

            position_info = book.body[sfen]
            # 手数を記録する。
            position_info.ply = ply

            # 指し手を辿る。
            board = cshogi.Board(sfen)

            # すべての合法手を辿る。
            for move in board.legal_moves:

                board.push(move)

                # 1手進めた局面が定跡本体に存在しないなら駄目っぽ。
                next_sfen = trim_sfen(board.sfen())

                if next_sfen in book.body and next_sfen not in visited:
                    next_queue.append(next_sfen)
                else:
                    next_sfen_f = flipped_sfen(next_sfen)
                    if next_sfen_f in book.body and next_sfen_f not in visited:
                        next_queue.append(next_sfen_f)
                board.pop()

        ply += 1

    print(f"..bsf for ply done. {c} positions.")


def parse_book_move_line(line:str, normalize_eval:bool = True)->MoveInfo:
    if ',' in line:
        move, eval_str, *_ = line.split(',')
    else:
        move, _, eval_str, *_ = line.split()

    if eval_str == 'None':
        eval = None
    else:
        eval_raw = int(eval_str)
        eval = normalize_book_eval(eval_raw) if normalize_eval else eval_raw
    return MoveInfo(move, eval)


def normalize_book_eval(eval:int)->int:
    abs_eval = abs(eval)
    sign = 1 if eval >= 0 else -1

    if abs_eval > OLD_BOOK_MATE_THRESHOLD:
        mate_distance = OLD_BOOK_VALUE_MATE - abs_eval
        return sign * (VALUE_MATE - mate_distance)

    if abs_eval > VALUE_EVAL_CLAMP:
        return sign * VALUE_MATE

    return eval


def parse_noe_line(line:str)->int|None:
    """やねうら王定跡ヘッダーの # NOE:<positions> を読む。"""
    if not line.startswith('#'):
        return None
    body = line[1:].strip()
    if not body.startswith('NOE:'):
        return None
    try:
        return int(body.split(':', 1)[1].strip())
    except ValueError:
        return None


def print_book_read_start(path:str, total:int|None):
    print(f"[BookReadStart] 0/{book_progress_total_text(total)} path={path}")


def print_book_read_progress(count:int, total:int|None):
    print(f"[BookReadProgress] {count}/{book_progress_total_text(total)}")


def print_book_read_done(path:str, count:int, total:int|None):
    print(f"[BookReadDone] {count}/{book_progress_total_text(total)} path={path}")


def read_yaneuraou_book_file(
    path:str,
    on_position:Callable[[Sfen, int, list[MoveInfo]], None],
    *,
    normalize_eval:bool = True,
    copy_moveinfos:bool = True,
)->int:
    """
    やねうら王定跡フォーマットを読み込み、1局面ごとに on_position() を呼ぶ。
    """
    sfen : Sfen|None = None
    moveinfos : list[MoveInfo] = []
    ply = 0
    count = 0
    total_positions : int|None = None

    print_book_read_start(path, total_positions)

    def append_to_book():
        nonlocal sfen, moveinfos, ply
        if sfen is None:
            return
        if copy_moveinfos:
            considered_moveinfos = [
                MoveInfo(moveinfo.move, moveinfo.eval)
                for moveinfo in moveinfos
                if moveinfo.eval is not None
            ]
        else:
            considered_moveinfos = moveinfos
        if considered_moveinfos:
            on_position(sfen, ply, considered_moveinfos)
        sfen = None
        moveinfos = []
        ply = 0

    first = True
    for raw_line in open(path, 'r', encoding='utf-8'):
        line = raw_line.rstrip()
        if not line:
            continue

        line = line.lstrip('\ufeff')
        if YANEURAOU_BOOK_HEADER_V1 in line:
            first = False
            continue

        if line.startswith('#'):
            parsed_total_positions = parse_noe_line(line)
            if parsed_total_positions is not None and parsed_total_positions != total_positions:
                total_positions = parsed_total_positions
                print_book_read_start(path, total_positions)
            first = False
            continue

        if first:
            if not line.startswith('sfen '):
                print("warning : illegal YaneuraOu Book Header")
            first = False

        if line.startswith('sfen '):
            append_to_book()
            count += 1
            if count % BOOK_READ_PROGRESS_INTERVAL == 0:
                print_book_read_progress(count, total_positions)
            sfen, ply = trim_sfen_ply(line)
        else:
            moveinfos.append(parse_book_move_line(line, normalize_eval))

    append_to_book()
    print_book_read_done(path, count, total_positions)
    return count


def merge_moveinfos(position_info:PositionInfo, moveinfos:list[MoveInfo]):
    for moveinfo_new in moveinfos:
        for move_info in position_info.moveinfos:
            if move_info.move == moveinfo_new.move:
                if moveinfo_new.eval is not None:
                    move_info.eval = moveinfo_new.eval
                break
        else:
            position_info.moveinfos.append(MoveInfo(moveinfo_new.move, moveinfo_new.eval))


def read_yaneuraou_book(book:Book, path:str):
    """
    やねうら王 定跡形式を読み込む。
    """
    print(f"read yaneuraou book , path = {path}")

    def append_position(sfen:Sfen, ply:int, moveinfos:list[MoveInfo]):
        if sfen in book.body:
            # 定跡本体に見つかったので、指し手のみ追加登録する。
            merge_moveinfos(book.body[sfen], moveinfos)
        else:
            # flipped sfenのほうも調べる。
            sfen_f = flipped_sfen(sfen)
            if sfen_f in book.body:
                moveinfos_f = [MoveInfo(flipped_move(moveinfo.move), moveinfo.eval) for moveinfo in moveinfos]
                merge_moveinfos(book.body[sfen_f], moveinfos_f)
            else:
                # 定跡本体に見つからなかったので、新規局面として登録する。
                book.body[sfen] = PositionInfo([MoveInfo(moveinfo.move, moveinfo.eval) for moveinfo in moveinfos], ply)

    with book.lock:
        read_yaneuraou_book_file(path, append_position)

    print(f"..read yaneuraou book done..len(book) = {len(book.body)}.")


def read_bookminer_backup(book:Book, path:str):
    """
    BookMiner自身が book/backup/ に書き出した通常定跡DBを高速に読み込む。
    正規形と仮定し、flip merge と評価値の旧形式補正は行わない。
    """
    read_regular_book_fast(book, path, "BookMiner backup book")


def read_regular_book_fast(book:Book, path:str, label:str):
    """
    正規形と仮定できる通常定跡DBを高速に読み込む。
    flip merge と評価値の旧形式補正は行わない。
    """
    print(f"read {label} , path = {path}")

    def append_position(sfen:Sfen, ply:int, moveinfos:list[MoveInfo]):
        book.body[sfen] = PositionInfo(moveinfos, ply)

    with book.lock:
        read_yaneuraou_book_file(
            path,
            append_position,
            normalize_eval=False,
            copy_moveinfos=False,
        )

    print(f"..read {label} done..len(book) = {len(book.body)}.")


# `peta_read`コマンドで読み込まれた定跡ファイル
peta_book = Book()


def collect_book_backup_paths()->list[str]:
    paths : list[str] = []
    if not os.path.isdir(BOOK_BACKUP_DIR):
        return paths

    for filename in os.listdir(BOOK_BACKUP_DIR):
        if not filename.startswith(f"{BOOK_DB_NAME}-"):
            continue
        if not filename.endswith(".db"):
            continue
        if "_ply" in filename:
            continue
        path = os.path.join(BOOK_BACKUP_DIR, filename)
        if os.path.isfile(path):
            paths.append(path)

    paths.sort(key=lambda path: os.path.basename(path))
    return paths


def legacy_book_backup_path()->str:
    return os.path.join(BOOK_BACKUP_DIR, f"{BOOK_DB_NAME}.db")


def get_latest_book_backup_or_none()->str|None:
    """
    book/backup/ にある最新の通常バックアップを返す。
    ply制限つきの `*_plyN.db` は、部分書き出しなので自動選択から除外する。
    `book/backup/book_miner.db` は、通常バックアップが1つも無いときだけ読む。
    """
    paths = collect_book_backup_paths()
    if paths:
        return paths[-1]

    legacy_path = legacy_book_backup_path()
    if os.path.isfile(legacy_path):
        return legacy_path

    return None


def get_latest_book_backup()->str:
    path = get_latest_book_backup_or_none()
    if path is None:
        raise Exception(f"book backup file not found : {BOOK_BACKUP_DIR}/{BOOK_DB_NAME}-*.db")
    return path


def collect_peta_book_paths()->list[str]:
    paths : list[str] = []
    if not os.path.isdir(BOOK_BACKUP_DIR):
        return paths

    for filename in os.listdir(BOOK_BACKUP_DIR):
        if not filename.startswith(f"{PETA_BOOK_DB_NAME}-"):
            continue
        if not filename.endswith(".db"):
            continue
        path = os.path.join(BOOK_BACKUP_DIR, filename)
        if os.path.isfile(path):
            paths.append(path)

    paths.sort(key=lambda path: os.path.basename(path))
    return paths


def get_latest_peta_book()->str:
    paths = collect_peta_book_paths()
    if not paths:
        raise Exception(f"peta book file not found : {BOOK_BACKUP_DIR}/{PETA_BOOK_DB_NAME}-*.db")
    return paths[-1]


def is_supported_peta_book_path(path:str)->bool:
    return os.path.splitext(path)[1].lower() == ".db"


def ensure_supported_peta_book_path(path:str, label:str):
    if not is_supported_peta_book_path(path):
        raise Exception(f"{label} must be .db : {path}")


def load_latest_book_backup(book:Book):
    path = get_latest_book_backup_or_none()
    if path is None:
        print(f"book backup file not found. start with empty book. dir = {BOOK_BACKUP_DIR}")
        return

    load_book(book, path, fast=True)


def resolve_peta_source_book_path(path:str|None)->str:
    """
    peta_shockの変換元となる通常のやねうら王定跡DBを返す。
    path省略時は、最新の通常バックアップを用いる。
    """
    if path is None:
        latest = get_latest_book_backup()
        ensure_supported_peta_book_path(latest, "peta source book")
        return latest

    candidates = [
        path,
        os.path.join(BOOK_DIR, path),
    ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            ensure_supported_peta_book_path(candidate, "peta source book")
            return candidate

    raise Exception(f"peta source book not found : {path}")


def resolve_peta_book_path(path:str|None)->str:
    """
    peta_readで読み込むpeta_shock済み定跡DBを返す。
    path省略時は、book/backup/ の最新 peta_book を用いる。
    """
    if path is None:
        latest = get_latest_peta_book()
        ensure_supported_peta_book_path(latest, "peta book")
        return latest

    candidates = [
        path,
        os.path.join(BOOK_DIR, path),
    ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            ensure_supported_peta_book_path(candidate, "peta book")
            return candidate

    raise Exception(f"peta book not found : {path}")


def parse_regular_book_backup_name(path:str)->tuple[str,int]|None:
    """
    book_miner-YYYYMMDDHHMMSS_N.db から timestamp と局面数を取り出す。
    peta book はこの2つを引き継いだ名前にする。
    """
    filename = os.path.basename(path)
    pattern = rf"^{re.escape(BOOK_DB_NAME)}-(\d{{14}})_(\d+)\.db$"
    match = re.fullmatch(pattern, filename)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def peta_book_backup_path_from_source(source_book_path:str)->str:
    parsed = parse_regular_book_backup_name(source_book_path)
    if parsed is None:
        return os.path.join(BOOK_BACKUP_DIR, f"{PETA_BOOK_DB_NAME}-{make_time_stamp()}.db")

    timestamp, position_count = parsed
    return os.path.join(BOOK_BACKUP_DIR, f"{PETA_BOOK_DB_NAME}-{timestamp}_{position_count}.db")


def to_book_dir_relative_path(path:str)->str:
    """
    makebook peta_shock は BookDir からの相対pathを受け取るので、
    Python側の `book/` 配下の実ファイルを BookDir 相対pathへ変換する。
    """
    book_dir_abs = os.path.abspath(BOOK_DIR)
    path_abs = os.path.abspath(path)
    rel = os.path.relpath(path_abs, book_dir_abs)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise Exception(f"peta source book must be under {BOOK_DIR} : {path}")
    return rel.replace(os.sep, '/')


def is_yaneuraou_progress_bar_line(line:str)->bool:
    line = line.strip()
    if not line.startswith("0% [") or not line.endswith("] 100%"):
        return False

    bar = line[len("0% ["):-len("] 100%")]
    return bool(bar) and all(ch == "." for ch in bar)


def run_peta_shock_makebook(source_book_path:str)->str:
    """
    YO-MATERIAL.exe を子プロセスとして起動し、
    makebook peta_shock で source_book_path を book/backup/peta_book-*.db に変換する。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    engine_path = os.path.join(script_dir, PETA_SHOCK_ENGINE_NAME)
    if not os.path.isfile(engine_path):
        raise Exception(f"peta shock engine not found : {engine_path}")

    source_book_rel = to_book_dir_relative_path(source_book_path)
    peta_path = peta_book_backup_path_from_source(source_book_path)
    peta_temp_path = temp_book_path(peta_path)
    peta_temp_rel = to_book_dir_relative_path(peta_temp_path)
    os.makedirs(BOOK_BACKUP_DIR, exist_ok=True)

    if os.path.exists(peta_temp_path):
        os.remove(peta_temp_path)

    makebook_command = f"makebook peta_shock {source_book_rel} {peta_temp_rel}"
    commands = [
        "setoption name BookDir value book",
        "setoption name BookFile value no_book",
        "setoption name FlippedBook value true",
        "setoption name USI_Hash value 1",
        makebook_command,
        "quit",
    ]

    print(f"start peta_shock makebook")
    print(f"engine path = {engine_path}")
    print(f"source book = {source_book_path}")
    print(f"peta book   = {peta_path}")
    print(f"command     = {makebook_command}")

    process = subprocess.Popen(
        engine_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=script_dir,
        encoding="UTF-8",
        errors="replace",
        bufsize=1,
    )

    output_queue = queue.Queue()

    def read_output():
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    Thread(target=read_output, daemon=True).start()

    assert process.stdin is not None
    for command in commands:
        process.stdin.write(command + "\n")
    process.stdin.flush()
    process.stdin.close()

    start_time = time.time()
    last_progress_time = start_time
    output_done = False

    while True:
        try:
            line = output_queue.get(timeout=1)
            if line is None:
                output_done = True
            else:
                line = line.rstrip()
                if line and not is_yaneuraou_progress_bar_line(line):
                    print(f"[peta_shock] {line}")
        except queue.Empty:
            pass

        now = time.time()
        if now - last_progress_time >= PETA_SHOCK_PROGRESS_INTERVAL:
            elapsed = int(now - start_time)
            print(f"[peta_shock] running... elapsed {elapsed}s")
            last_progress_time = now

        if process.poll() is not None and output_done and output_queue.empty():
            break

    return_code = process.wait()
    if return_code != 0:
        raise Exception(f"peta_shock makebook failed. return code = {return_code}")

    if not os.path.isfile(peta_temp_path) or os.path.getsize(peta_temp_path) == 0:
        raise Exception(f"peta_shock makebook failed. output file was not created : {peta_temp_path}")

    os.replace(peta_temp_path, peta_path)
    print(f"..peta_shock makebook has done, path = {peta_path}")
    return peta_path


def read_peta_book(peta_book_path:str|None = None):
    """
    最新または指定されたpeta_shock済み定跡DBを peta_book に読み込む。
    """
    peta_path = resolve_peta_book_path(peta_book_path)

    print(f"read peta shocked book , path = {peta_path}")
    global peta_book
    peta_book = Book()
    read_regular_book_fast(peta_book, peta_path, "peta shocked book")
    print("reading the peta_book has done.")


def make_and_read_peta_book(source_book_path:str|None = None):
    """
    最新または指定された通常定跡DBをpeta_shock化し、生成されたpeta_bookを読み込む。
    """
    source_book_path = resolve_peta_source_book_path(source_book_path)
    peta_path = run_peta_shock_makebook(source_book_path)
    read_peta_book(peta_path)


def write_and_read_peta_book(book:Book):
    """
    現在の定跡DBを書き出し、その書き出したファイルをpeta_shock化して読み込む。
    周回作業で現在のDBをpeta_bookへ反映するための一括コマンド。
    """
    print("start p command : write backup, peta_shock, and read peta book.")
    source_book_path = write_to_yaneuraou_book(book, BOOK_BACKUP_DIR)
    print(f"p command source book = {source_book_path}")
    make_and_read_peta_book(source_book_path)
    print("..p command has done.")
    print("[PetaCommandDone]")


def peta_next(peta_eval_diff:int, max_step:int, max_book_ply:int, start_sfens_path:str):
    """
    r/pコマンドでメモリに読み込まれたpeta_book(peta_shock化された定跡)を
    読み込み、掘れていない局面を`book/think_sfens.txt`に書き出します。

    rootのbestmoveのscoreをroot_bestとする。
    root_best ± eval_diffの範囲の枝を展開していき、末端の局面を書き出す。

    展開していくとき、先手番の定跡について考えるときは、先手の局面ならbestmoveのみ、後手の局面ならroot_best - eval_diff 以上の指し手を延長していき、未展開のleaf nodeを`book/think_sfens-black.txt`に書き出します。

    後手番の定跡について考えるときも、同様に、後手の局面ならbestmoveのみ、先手の局面なら、root_best - eval_diff以上の指し手を延長していきます。`book/think_sfens-white.txt`に書き出します。

    開始局面集合は、settings/book_miner_settings.json5 の peta_next_start_sfens_path で指定できます。
    このファイルがなければ平手の開始局面から辿ります。
    このファイルは、startpos moves .. のようなPositionコマンドで指定するposition stringに対応しています。
    """

    global peta_book

    print(
        f"peta_next, peta_eval_diff = {peta_eval_diff}, "
        f"max_step = {max_step}, max_book_ply = {max_book_ply}, "
        f"start_sfens_path = {start_sfens_path}"
    )

    # 先手の定跡を考えるのか？
    # turn == 1 なら 先手、0なら後手
    for turn in [1, 0]:

        think_sfens : dict[PositionStr,None] = {}

        # 手番文字列
        turn_str = ['white','black'][turn]

        print(f"--- peta_next {turn_str} ---")

        # 訪問済み局面
        visited : set[Sfen] = set()

        # bfs

        # 今回辿る局面集合。(position文字列, plyつきsfen)
        root_positions : list[tuple[PositionStr, Sfen]] = []

        # ファイルで指定されているなら、そこから。
        if os.path.exists(start_sfens_path):
            print(f"read start sfens , path = {start_sfens_path}")
            for line in open(start_sfens_path, 'r'):
                position_cmd = line.strip()
                if not position_cmd or position_cmd.startswith('#'):
                    continue
                sfen_with_ply = decode_position_string(position_cmd)
                print(f"start sfen = {sfen_with_ply}")
                root_positions.append((position_cmd, sfen_with_ply))
        else:
            root_positions.append(('startpos', SFEN_START_PLY1))

        # PositionStr -> Sfen with ply,RootBest,EvalDiffが並んでいる。
        current_positions : dict[PositionStr, tuple[Sfen,int,int]] = {}

        # root_positionsのroot_bestを調べてcurrent_positionsに突っ込む。
        for position_cmd, sfen_with_ply in root_positions:
            sfen, ply = trim_sfen_ply(sfen_with_ply)

            if ply >= max_book_ply:
                continue

            sfen_f = flipped_sfen(sfen)
            if sfen in peta_book.body:
                position_info = peta_book.body[sfen]
            elif sfen_f in peta_book.body:
                position_info = peta_book.body[sfen_f]
            else:
                think_sfens[position_cmd] = None
                continue

            # root_bestは手番側から見たスコアなのでflip hitでも気にしなくて良い。
            root_best, _ = get_best(position_info)
            if root_best is None:
                think_sfens[position_cmd] = None
                continue

            current_positions[position_cmd] = (sfen_with_ply, root_best, peta_eval_diff)
            print(f"root sfen : {sfen_with_ply} , root_best = {root_best}")

        # 何回目のwhileループか。
        step = 1

        while current_positions:

            if step > max_step:
                break

            # 次回のwhileで辿る局面集合
            # PositionStr -> Sfen,RootBest,EvalDiffが並んでいる。
            next_positions : dict[PositionStr, tuple[Sfen, int, int]] = {}

            for position_cmd, (sfen_with_ply, root_best_eval, peta_eval_diff0) in current_positions.items():
                sfen, ply = trim_sfen_ply(sfen_with_ply)

                if ply >= max_book_ply:
                    continue

                sfen_f = flipped_sfen(sfen)

                # 訪問済みならskip
                if sfen in visited or sfen_f in visited:
                    continue

                visited.add(sfen)

                # 定跡DB上のsfenは、flipしたものがhitしているのか？
                flipped_bookhit : bool
                
                if sfen in peta_book.body:
                    position_info = peta_book.body[sfen]
                    flipped_bookhit = False
                elif sfen_f in peta_book.body:
                    position_info = peta_book.body[sfen_f]
                    flipped_bookhit = True
                else:
                    # 元の定跡ツリーに存在しない局面なので定跡ツリーが出たということで
                    # ここを思考対象局面に追加してやる。
                    think_sfens[position_cmd] = None
                    continue

                moveinfos = position_info.moveinfos
                if len(moveinfos)==0:
                    # なぜ指し手が登録されていない局面が定跡に書き出されているのであろうか…。
                    continue

                # scoreの降順のはずなので..
                besteval = moveinfos[0].eval
                if not isinstance(besteval, int):
                    continue

                # この局面の手番が turn と一致するなら(手番側)、bestmoveだけを辿る。
                # さもなくば(非手番側)、root_best_eval - peta_eval_diff2 の指し手までなら辿る。
                eval_low = besteval if ply % 2 == turn else root_best_eval - peta_eval_diff0

                for moveinfo in moveinfos:
                    # 評価値を持っている枝でなければskip
                    # peta shock化したので、すべての枝は評価値を持っているはずなのだが。
                    if not isinstance(moveinfo.eval, int):
                        continue

                    if eval_low <= moveinfo.eval:
                        # この枝は辿る。
                        # この指し手で進めた局面を次周調べる。

                        # peta_bookは先手局面しか登録されていないので、book_sfenは先手局面となっている。
                        # それだと好ましくないので、sfenのほうを用いる。
                        board = cshogi.Board(sfen_with_ply)
                        move = flipped_move(moveinfo.move) if flipped_bookhit else moveinfo.move
                        checked_push_usi(board, move, context=position_cmd)
                        next_sfen = board.sfen()
                        _, next_ply = trim_sfen_ply(next_sfen)
                        if next_ply >= max_book_ply:
                            continue

                        next_position_cmd = append_position_move(position_cmd, move)

                        # 次の局面では、root_best_evalは反転する。(手番側から見たevalで管理しているため)
                        next_positions[next_position_cmd] = (next_sfen, - root_best_eval, peta_eval_diff0)

            print(f"step = {step} , len(next_positions) = {len(next_positions)}, think_sfens = {len(think_sfens)}")

            current_positions = next_positions
            step += 1


        path = os.path.join(BOOK_DIR, f"think_sfens-{turn_str}.txt")
        with open(path, 'w') as w:
            print(f"write book path = {path}, len(think_sfens) = {len(think_sfens)}.")
            for position_cmd in think_sfens:
                w.write(position_cmd + '\n')


    # 先後のthink_sfensを合体させたファイル。
    # 2つのファイルの内容を交互に書き出す。

    bw_path = os.path.join(BOOK_DIR, f"think_sfens.txt")
    b_path  = os.path.join(BOOK_DIR, f"think_sfens-black.txt")
    w_path  = os.path.join(BOOK_DIR, f"think_sfens-white.txt")
    bw_count = 0
    with open(bw_path, 'w') as fbw,\
        open(b_path, 'r') as fb, \
        open(w_path, 'r') as fw:
        for line_a, line_b in zip_longest(fb, fw, fillvalue=''):
            if line_a:
                fbw.write(line_a)
                bw_count += 1
            if line_b:
                fbw.write(line_b)
                bw_count += 1

    print("peta_next done.")
    print(f"[PetaNextDone] path={bw_path} count={bw_count}")


def scheduled_time_text(timestamp:float)->str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d_%H:%M:%S")


def put_position_commands(book:Book, path:str, engine_manager:EngineManager, eval_limit:int):
    job_counter_local = get_job_counter()

    print(f"({job_counter_local}) put position commands , path = {path} , eval_limit = {eval_limit}")
    if not os.path.exists(path):
        print(f"({job_counter_local}) put position commands Error : file not found, path = {path}")
        return

    with open(path, 'r') as r:
        raw_lines = [
            (line_number, line.strip())
            for line_number, line in enumerate(r, 1)
            if line.strip() and not line.lstrip().startswith('#')
        ]

    lines : list[PositionStr] = []
    skipped = 0
    for line_number, line in raw_lines:
        try:
            decode_position_string(line)
        except Exception as exc:
            skipped += 1
            print(f"({job_counter_local}) skip illegal position command line {line_number}: {line} : {exc}")
            continue
        lines.append(line)

    total = len(lines)
    print(f'({job_counter_local}) read {total} position commands.')
    if skipped:
        print(f'({job_counter_local}) skipped {skipped} illegal position commands.')
    engine_manager.start_task_queue_progress(job_counter_local, total, path, eval_limit)

    for line in lines:
        engine_manager.put_task(Task(SFEN_START, 1, eval_limit, line, job_counter_local))

    print(f"({job_counter_local}) put position commands , done.")


def merge_flipped_positions(book:Book):
    """
    flipした局面を削除する。
    後手の局面が削除される。
    """

    try:

        print("merge_flipped_positions start.")

        with book.lock:

            c = 0
            keys : list[Sfen] = list(book.body.keys())

            for sfen in keys:

                # 削除済みkeyであるならskipする。
                if sfen not in book.body:
                    continue

                sfen_f = flipped_sfen(sfen)
                if sfen_f in book.body:

                    # 反転させた局面が定跡本体に存在する。このレコードをmergeする必要がある。
                    position   = book.body[sfen]

                    print(f"merge : {sfen} {position.ply}")

                    # このタイミングでpop()することでflipさせたsfenのほうは定跡から削除しておく。
                    position_f = book.body.pop(sfen_f)

                    for moveinfo_f in position_f.moveinfos:
                        move = flipped_move(moveinfo_f.move)
                        eval = moveinfo_f.eval
                        # これをmergeする。
                        for moveinfo in position.moveinfos:
                            if moveinfo.move == move:
                                # 見つかった
                                if moveinfo.eval is None:
                                    # evalは、Noneのときだけ上書き
                                    moveinfo.eval = eval
                                break
                        else:
                            # 見つからなかったのでレコード丸ごと追加。
                            position.moveinfos.append(MoveInfo(move, eval))

                    c += 1

        print(f"merge flipped positions done, merged {c} positions")

    except Exception as e:
        print(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")


def dump_sfen(book:Book, sfen:Sfen, move : MoveStr|None):
    if sfen == 'startpos':
        sfen = SFEN_START

    board = cshogi.Board(sfen)
    sfen = trim_sfen(board.sfen())

    if sfen in book.body:
        position_info = book.body[sfen]
    else:
        sfen_f = flipped_sfen(sfen)
        if sfen_f in book.body:
            # print("found a flipped sfen in the book")
            position_info = book.body[sfen_f]
            if move:
                move = flipped_move(move)
        else:
            position_info = None

    if position_info:
        dump_position(sfen, position_info, move)
        print(f"get_best = {get_best(position_info)}")
    else:
        print("sfen not found in the book")


def inquire_position(book:Book, position_cmd:str):
    """
    positionコマンドを与えて、その経路すべてを調査する。
    """
    print("--- start inqure ---")

    cmd = position_cmd.split('moves')
    sfen = cmd[0].strip()
    if sfen == 'startpos':
        sfen = SFEN_START
    board = cshogi.Board(sfen)
    sfen = trim_sfen(board.sfen())

    if len(cmd) >= 2:
        moves = cmd[1].split()
    else:
        moves = []

    dump_sfen(book, sfen, moves[0] if moves else None)

    # 指し手で進めていく。
    for move , next_move in zip(moves , moves[1:] + [None]):
        print(f"move = {move}")
        checked_push_usi(board, move, context=position_cmd) # type:ignore
        sfen = trim_sfen(board.sfen())
        dump_sfen(book, sfen, next_move)

    print("--- end inqure ---")


# ============================================================
#                             main
# ============================================================

def user_input(from_gui:bool = False):
    """
    ユーザーからの入力受付。
    """
    book : Book = Book()
    book_miner_settings = load_book_miner_settings()
    print("[StartupStage] stage=book_read message=定跡DBを読み込み中")
    load_latest_book_backup(book)
    print("[StartupStage] stage=book_read_done message=定跡DB読み込み完了")

    engine_manager = EngineManager(book_miner_settings, from_gui=from_gui)
    if from_gui:
        with book.lock:
            engine_manager.report_mining_progress(len(book.body), force=True)

    # 局面について思考するtask workerの開始
    print("[StartupStage] stage=task_worker message=探索worker起動中")
    engine_manager.start_task_workers(book)
    print("[StartupStage] stage=task_worker_done message=探索worker起動完了")

    def save_book_main():
        # lockは呼び出し元で行っているものとする。
        nonlocal book
        save_book_backup(book, BOOK_BACKUP_DIR)

    def backup_worker():
        print("start backup worker..")
        while True:
            next_backup_time = scheduled_time_text(time.time() + book_miner_settings.auto_save_interval_seconds)
            print(
                f"[BackupNext] next={next_backup_time} "
                f"interval={book_miner_settings.auto_save_interval_seconds}"
            )
            time.sleep(book_miner_settings.auto_save_interval_seconds)
            print("[BackupStart]")
            save_book_backup(book, BOOK_BACKUP_DIR)
            print("[BackupDone]")

    # backup用のタスクを開始。
    next_backup_time = scheduled_time_text(time.time() + book_miner_settings.auto_save_interval_seconds)
    print("[StartupStage] stage=backup_service message=自動保存サービス起動中")
    Thread(target=backup_worker, daemon=True).start()
    print(
        f"[BackupServiceStarted] next={next_backup_time} "
        f"interval={book_miner_settings.auto_save_interval_seconds}"
    )

    time.sleep(2)
    print("[CommandReady] message=コマンド受付を開始しました。")

    # 定跡を掘る範囲
    eval_limit = 400

    # enable_print_log()

    while True:
        try:
            if not from_gui:
                print("[Q]uit [T]hink [H]elp> ", end='')
            inp = input().split()
            if not inp:
                continue
            i = inp[0].lower()
            if i == 'h':
                print("Help : ")
                print("  Q : quit")
                print("  ! : quit without saving")
                print("  W : write book backup        , w (ply_limit)")
                print("  T : think positions          , t (think_sfens path)")
                print("  I : inquire                  , i [sfen]")
                print("  M : merge flipped positions")
                print("  E : EvalLimit , e [eval_limit]")
                print("  B : bfs for ply")
                print("  R    : read peta shocked book , r (peta book path)")
                print("  P    : write backup, make and read peta shocked book")
                print("  N    : peta_shock next , n peta_eval_diff (max_step)")
                print("  H : Help")

                # --- 削除したコマンド

                # print("  peta_check : check peta shocked book")
                # print("  peta_flood    : peta shocked book on floodgate , peta_flood [folder_path]")

            elif i == 'q':
                print("quit")
                save_book_main()
                break

            elif i == '!':
                print("quit without saving")
                break

            elif i == 'w':
                # 手数を指定して、その手数まで書き出す。
                # (peta_shock_nextのため)
                if len(inp) < 2:
                    ply_limit = None
                else:
                    ply_limit = int(inp[1])

                # 定跡を丸ごと書き出す。
                write_to_yaneuraou_book(book, BOOK_BACKUP_DIR, ply_limit)

            elif i == 't':
                if len(inp) < 2:
                    path = os.path.join(BOOK_DIR, THINK_SFENS_NAME)
                else:
                    path = inp[1]

                Thread(target=lambda: put_position_commands(book, path, engine_manager, eval_limit), daemon=True).start()

            elif i == 'i':
                if len(inp) < 2:
                    sfen = 'startpos'
                else:
                    sfen = trim_sfen(' '.join(inp[1:]))

                inquire_position(book, sfen)

            elif i == 'e':
                # eval_limitの指定。`e eval_limit eval_limit_low`のように指定できる。
                if len(inp) < 2:
                    print("Error : EvalLimit e")
                else:
                    eval_limit = int(inp[1])
                    print(f"eval_limit = {eval_limit}")

            elif i == 'm':
                merge_flipped_positions(book)
            
            elif i == 'b':
                Thread(target=lambda : bfs_for_ply(book), daemon=True).start()

            elif i == 'r':
                # peta_read
                peta_book_path = inp[1] if len(inp) >= 2 else None
                read_peta_book(peta_book_path)
                print("[PetaReadDone]")

            elif i == 'p':
                # write and peta_read
                write_and_read_peta_book(book)
            
            elif i == 'n':
                # peta_next
                if len(inp) < 2:
                    print("Usage : n peta_eval_diff (max_step)")
                else:
                    peta_eval_diff = int(inp[1])
                    max_step = 9999 if len(inp) < 3 else int(inp[2])
                    peta_next(
                        peta_eval_diff,
                        max_step,
                        book_miner_settings.max_book_ply,
                        book_miner_settings.peta_next_start_sfens_path,
                    )

        except Exception as e:
            print(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")


def parse_args()->argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from_gui",
        action="store_true",
        help="suppress interactive prompts for BookMiner-gui.py",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    user_input(from_gui=args.from_gui)

if __name__ == '__main__':
    main()
