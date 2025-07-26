import sys
import os
import re
from dataclasses import dataclass

# パラメーターファイルで、そのパラメーターを使っていないことを示す文字列。
NOT_USED_STR = "[[NOT USED]]" 

@dataclass
class Entry:
    # パラメーター名
    name: str

    # パラメーターの型("int" | "str")
    param_type : str

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
            
        e = Entry(name = values[0], param_type = str(values[1]),
                    v = float(values[2]),
                    min = float(values[3]), max = float(values[4]),
                    step = float(values[5]), delta = float(values[6]),
                    comment = comment, not_used = not_used)

        l.append(e)

    print(f"read parameter file, {len(l)} parameters.")
    return l

def write_parameters(params_file : str , entries : list[Entry]):
    """
        パラメーターファイルに書き込む。
        パラメーターファイルにはEntry構造体の定義順でデータがカンマ区切りで並んでいるものとする。
    """

    with open(params_file, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(f"{e.name}, {e.param_type}, {e.v}, {e.min}, {e.max}, {e.step}, {e.delta}{' //' + e.comment if e.comment else ""}{NOT_USED_STR if e.not_used else ""}\n")

    print(f"write parameter file, {len(entries)} parameters.")


def apply_parameters(tune_file:str , params_file : str, target_dir:str):
    pass

def tune_parameters(tune_file:str, params_file : str, target_dir:str):
    # "suisho10.tune"   ← チューニング指示ファイル
    # "suisho10.params" ← パラメーターファイル

    try:
        params = read_parameters(params_file)
    except:
        params : list[Entry] = []
    


if __name__ == "__main__":

    # 使い方
    # python tune.py [apply|tune] patch_file target_dir

    args = sys.argv
    target_dir = args[3] if len(args) >= 4 else "source"
    tune_file  = args[2] if len(args) >= 3 else "suisho10.tune"
    command    = args[1] if len(args) >= 2 else ""

    params_file, _ = os.path.splitext(tune_file)
    params_file += ".params"

    print(f"command     : {command}")
    print(f"tune_file   : {tune_file}")
    print(f"params_file : {params_file}")
    print(f"target_dir  : {target_dir}")

    # entries = read_parameters("suisho10.params")
    # write_parameters("suisho10-new.params", entries)

    try:
        if command == "apply":
            apply_parameters(tune_file, params_file, target_dir)
        elif command == "tune":
            tune_parameters(tune_file, params_file, target_dir)
        else:
            print("Usage : python tune.py [apply|tune] tune_file target_dir")
            # tune_fileとtarget_dirのデフォルト値は、"suisho10.tune", "source"
    except Exception as e:
        print(str(e))
