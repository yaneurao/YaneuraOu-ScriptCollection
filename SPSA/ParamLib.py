# パラメーターの読み書きモジュール

import os
import math
from dataclasses import dataclass

# ============================================================
#                      Read/Write Parameters
# ============================================================

# パラメーターファイルで、そのパラメーターを使っていないことを示す文字列。
NOT_USED_STR = "[[NOT USED]]" 


def round_parameter_int(value: float) -> int:
    """Round to nearest integer, with half values away from zero."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)

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
        raise FileNotFoundError(f"Error : {params_file} not found.")
        
    l : list[Entry] = []
    names = set()

    with open(params_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for lineNo, line in enumerate(lines, 1):
        line = line.rstrip()
        if not line:
            continue  # 空行は無視

        not_used = False
        if NOT_USED_STR in line:
            line = line.replace(NOT_USED_STR, "")
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

        key = e.name.casefold()
        if not e.name or key in names:
            raise ValueError(f"{params_file}({lineNo}): パラメーター名が空欄または重複: {e.name}")
        names.add(key)
        if not all(math.isfinite(v) for v in (e.v, e.min, e.max, e.step, e.delta)):
            raise ValueError(f"{params_file}({lineNo}): {e.name} の値は有限数で指定してください")
        if not e.not_used and (e.min >= e.max or not e.min <= e.v <= e.max or e.step <= 0 or e.delta < 0):
            raise ValueError(f"{params_file}({lineNo}): {e.name} の初期値・範囲・摂動幅・更新係数が不正です")
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
            f.write(f"{e.name}, {e.type}, {e.v}, {e.min}, {e.max}, {e.step}, {e.delta}{' //' + e.comment if e.comment else ''}{NOT_USED_STR if e.not_used else ''}\n")

    print(f"write parameter file, {len(entries)} parameters.")

