import os
import traceback
from dataclasses import dataclass, field
from ShogiCommonLib import *

# SPSAするための対局スクリプト

# ============================================================
#                             定数
# ============================================================

# このスクリプトのバージョン
SCRIPT_VERSION               = "V0.01"

# ============================================================
#                         Game Match
# ============================================================

# 全対局スレッドが共通で(同じものを参照で)持っている構造体
@dataclass
class SharedState:
    # 棋譜保存用
    kif_manager : KifManager = field(default_factory=KifManager)


@dataclass
class EngineSettings:
    '''探索スレッド固有の設定を集めた構造体'''

    # エンジンのpath
    engine_path : str

    # エンジンの表示名
    engine_name : str

    # ノード数込みの表示名
    engine_name_with_nodes : str

    # エンジンの探索node数
    engine_nodes : int

    # エンジンが開始できる状態になったのか？
    readyok : bool

    # スレッドid
    thread_id : int

# 対局スレッド
class ShogiMatch:
    def __init__(self, t1 : EngineSettings, t2 : EngineSettings, shared : SharedState):
        self.t : list[EngineSettings]= [t1, t2]
        self.shared = shared

        engine1 = Engine(t1.engine_path, t1.thread_id)
        engine2 = Engine(t2.engine_path, t2.thread_id)
        self.engines = [engine1, engine2]

# ============================================================
#                             main
# ============================================================

def user_input():
    """
    ユーザーからの入力受付。
    """

    # これは全対局スレッドが同じものを指す。
    shared = SharedState()

    while True:
        try:
            print("[Q]uit [S]psa [H]elp> ", end='')
            inp = input().split()
            if not inp:
                continue
            i = inp[0].lower()

            if i == 'h':
                print("Help : ")
                print("  Q : Quit")
                print("  S : Spsa")

            elif i == 's':
                print("spsa")
                pass

            elif i == 'q':
                print("quit")
                break

        except Exception as e:
            print(f"Exception :{type(e).__name__}{e}\n{traceback.format_exc()}")


def main():
    user_input()

if __name__ == '__main__':
    main()

