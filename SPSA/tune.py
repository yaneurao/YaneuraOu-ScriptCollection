import sys
import os
import re
from dataclasses import dataclass, field

# パラメーターファイルで、そのパラメーターを使っていないことを示す文字列。
NOT_USED_STR = "[[NOT USED]]" 

# パラメーターファイルの1行を表現する型
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

# .tuneファイルの1patchを表現する型
@dataclass
class TuneBlock:
    # ファイル名
    filename : str = ""

    # パラメーターのprefix。これは`#context`のあとに書かれている。
    context_name : str = ""

    # パラメーターの名前。これはパラメーターのsuffix。
    # 💡 `@1`なら`_1`のように置換される。
    params : list[str] = field(default_factory=list)

    # 置換対象文字列
    context_lines : list[str] = field(default_factory=list)

    # これに置換する
    replace_lines : list[str] = field(default_factory=list)

    # これを追加する
    add_lines : list[str] = field(default_factory=list)



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

def parse_tune_file(tune_file:str)->list[TuneBlock]:

    print(f"parse tune file , path = {tune_file}")

    with open(tune_file, "r", encoding="utf-8") as f:
        filelines = f.readlines()

    r : list[TuneBlock] = []

    filename = ""
    current_block = TuneBlock()

    def append_check():
        # "#file"と"#context"とがあるとそこまでのTuneBlockを書き出す。
        nonlocal current_block
        # current_blockがすでに埋まっていれば、書き出す。
        if current_block.context_name:
            current_block.filename = filename
            r.append(current_block)
            current_block = TuneBlock(filename = filename)

    # 次のブロックを取得する。
    # "#..."から次の"#..."の前の行まで
    lineNo = 0
    def get_next_block()->list[str]:
        nonlocal lineNo, filelines
        l : list[str] = []
        while lineNo < len(filelines):
            line = filelines[lineNo]
            if line.startswith("#"):
                # すでにblock格納済みであれば、これは次のブロックの開始行だからwhileを抜ける。
                if l:
                    break
            l.append(line)
            lineNo += 1

        print(l)

        return l
    
    while True:
        lines = get_next_block()
        # ブロックがなければ終了
        if not lines:
            break

        # 1行以上あることは保証されている。
        line = lines[0]

        stripped = line.strip()
        if stripped.startswith("#file"):
            match = re.match(r"^#file\s+(.+)$", stripped)
            if match:
                # 新しいセクションなのでいまあるものを追記する。
                append_check()
                filename = match.group(1)
        elif stripped.startswith("#context"):
            match = re.match(r"^#context\s+(.+)$", stripped)
            if match:
                append_check()
                current_block.context_name = match.group(1)
                # 次の blockまで
                current_block.context_lines = lines[1:]
        elif stripped.startswith("#replace"):
            current_block.replace_lines = lines[1:]
        elif stripped.startswith("#add"):
            current_block.add_lines = lines[1:]
    append_check()
            
    print(f"tuning file .. {len(r)} blocks.")
    return r


def apply_parameters(tune_file:str , params_file : str, target_dir:str):

    try:
        params = read_parameters(params_file)
    except:
        params : list[Entry] = []

    blocks = parse_tune_file(tune_file)
    for block in blocks:
        print(block)

    pass

def parse_block(block:list[str], param_prefix:str):
    """
        blockを与えて、`123@234`のような文字列を
        removedのほうに`@`の左側の数値を格納していき、
        params_nameのほうに`@`右側の英数字から作った変数名を格納していく。
        変数名にはprefixをくっつける。
    
        # 置換対象文字列から削除された(元あった)パラメーター
        removed_numbers : list[str] = []

        modified_block : パラメーター名で置き換えたものを返す。
    """

    modified_block:list[str] = []
    removed_numbers:list[str] = []
    params_name:list[str] = []

    # 省略されたパラメーター番号(連番)
    param_no = 1

    # "123@234"のように左側の数値を集めてかつ削除する。
    def collect_and_remove(match):
        removed_numbers.append(match.group())
        return '' # 削除
    
    # "@"とその後の英数字を順番に置換。
    # "@"が省略されていれば順番に"@1","@2",...に置換。
    def replace_at(match):
        nonlocal param_no
        suffix = match.group(1) # "@"の後ろの文字。なければ空。
        if not suffix:
            suffix = str(param_no)
            param_no += 1
        param_name = f"{param_prefix}_{suffix}"
        params_name.append(param_name)
        return param_name
    
    for line in block:
        # 数字(小数含む)を削除しつつ removed_numbers に格納
        modified = re.sub(r'-?\d+(?:\.\d+)?(?=@)', collect_and_remove, line)
        # "@"とその後の英数字を順番に置換
        modified = re.sub(r'@([A-Za-z0-9]*)', replace_at, modified)
        modified_block.append(modified)

    return modified_block, removed_numbers, params_name

def tune_parameters(tune_file:str, params_file : str, target_dir:str):

    try:
        params = read_parameters(params_file)
    except:
        params : list[Entry] = []

    # いったん、全部使わないパラメーターとして、使っているものだけをFalseにする。
    for param in params:
        param.not_used = True

    blocks = parse_tune_file(tune_file)

    # 変数名を列挙する。
    for block in blocks:
        # content blockがあるならそれを対象に。
        modified_block , removed_numbers, params_name = parse_block(block.context_lines, block.context_name)

        print("--- context")
        print(modified_block)
        print(removed_numbers)
        print(params_name)

        if block.add_lines:
            # add blockがあるなら、それも対象に。
            modified_block , removed_numbers, params_name = parse_block(block.context_lines, block.context_name)

            print("--- add")
            print(modified_block)
            print(removed_numbers)
            print(params_name)



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

    # "suisho10.tune"   ← チューニング指示ファイル
    # "suisho10.params" ← パラメーターファイル

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
