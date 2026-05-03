import sys
import os
import re
import traceback
import copy
from dataclasses import dataclass, field
from ParamLib import *

@dataclass
class Block:
    # block種別('set', 'context', 'add)
    type : str = ""

    # block名の後ろに続いていた文字列。
    params : list[str] = field(default_factory=list)

    # blockの中身
    content : list[str] = field(default_factory=list)

# .tuneファイルの1patchを表現する型
@dataclass
class TuneBlock:
    # setblockに書いてあったパラメーター。
    # `file`とか`tune`とか。
    setblock : dict[str,str] = field(default_factory=dict)

    # contextブロック
    # TuneBlockは1つのcontextブロックを持つ。
    # この内容をソースコードに対する置換対象文字列として使う。
    # ただし、数値に付与された`@`は、その数値をワイルドカード扱いにする。
    context_block : Block | None = None

    # addブロック(これは複数ありうる)
    add_blocks : list[Block] = field(default_factory=list)


def get_context_block(tune_block:TuneBlock)->Block:
    if tune_block.context_block is None:
        raise Exception(f"Error : context block not found, {tune_block}")
    return tune_block.context_block


def parse_tune_file(tune_file:str)->list[Block]:
    print(f"parse tune file, path = {tune_file}", end="")

    with open(tune_file, "r", encoding="utf-8") as f:
        filelines = f.readlines()

    # 返し値
    blocks : list[Block] = []

    # 次のブロックを取得する。
    # "#..."から次の"#..."の前の行まで
    lineNo = 0
    def get_next_block()->Block | None:
        nonlocal lineNo, filelines
        lines : list[str] = []
        block_type = ""
        block_params : list[str] = []
        while lineNo < len(filelines):
            line = filelines[lineNo]
            if line.startswith("#"):
                # すでにblock_typeが格納済みであれば、これは次のブロックの開始行だからwhileを抜ける。
                if block_type:
                    break
                # "//" 以降を無視
                line = line.split("//", 1)[0].strip()

                # 空白で分割（空要素を除外）
                parts = line.split()
                if len(parts) >= 2:
                    block_params = parts[1:]
                block_type = parts[0][1:]
            else:
                lines.append(line)
            lineNo += 1

        # ブロック名をすでに設定されてあるなら
        if block_type:
            return Block(block_type, block_params, lines)
        else:
            return None

    while True:
        block = get_next_block()
        # ブロックがなければ終了
        if not block:
            break
        blocks.append(block)

    print(f", {len(blocks)} Blocks.")

    # for block in blocks:
    #     print(block)

    return blocks


def read_tune_file(tune_file:str, blocks:list[Block] | None = None)->list[TuneBlock]:

    if blocks is None:
        blocks = parse_tune_file(tune_file)
    print(f"read tune file, path = {tune_file}", end="")

    # 返し値
    result : list[TuneBlock] = []

    # 現在の`#set`blockの保存内容
    setblock : dict[str,str] = {}

    current_block = TuneBlock()

    def append_check():
        # "#file"と"#context"とがあるとそこまでのTuneBlockを書き出す。
        nonlocal current_block
        # current_blockがすでに埋まっていれば、書き出す。
        if current_block.context_block is not None:
            # setblockの内容をコピー。これはTuneBlockを超えて設定を引き継ぐので…。
            current_block.setblock = dict(setblock)

            result.append(current_block)
            current_block = TuneBlock()

    for block in blocks:
        block_name = block.type
        if block_name.startswith("set"):
            # 新しいセクションかも知れないのでいまあるものを追記する。
            append_check()
            if len(block.params) < 2:
                raise Exception(f"Error : Insufficient parameters in set block, {block}")
            setblock[block.params[0]] = block.params[1]
        elif block_name.startswith("context"):
            # 新しいセクションなのでいまあるものを追記する。
            append_check()

            # if len(block.params) < 1:
            #     raise Exception(f"Error : Insufficient parameter in content block, {block}")
            # 📝 無名contentブロックは、置換用に使うようにした。

            current_block.context_block = block
        elif block_name.startswith("add"):
            current_block.add_blocks.append(block)

    append_check()
            
    print(f", {len(result)} TuneBlocks.")
    return result


def read_explicit_params(tune_file:str, blocks:list[Block] | None = None)->list[Entry]:
    """
        .tuneファイルに書かれた明示パラメーターを読み込む。
        既存のUSI optionをSPSA対象にしたいが、ソースコードは置換したくない場合に使う。

        書式:
          #param name type value min max step delta
    """

    if blocks is None:
        blocks = parse_tune_file(tune_file)

    result : list[Entry] = []
    for block in blocks:
        if block.type != "param":
            continue

        if len(block.params) < 7:
            raise Exception(f"Error : Insufficient parameters in param block, {block}")

        name, type_, value, min_, max_, step, delta = block.params[:7]
        if type_ not in ("int", "float"):
            raise Exception(f"Error : unsupported param type, type = {type_}, name = {name}")

        result.append(Entry(name, type_, float(value), float(min_), float(max_),
                            float(step), float(delta), "", False))

    return result


def parse_content_block(block:Block, prefix:str):
    """
        blockを与えて、`123@234`のような文字列を
        removedのほうに`@`の左側の数値を格納していき、
        params_nameのほうに`@`右側の英数字から作った変数名を格納していく。
        変数名にはprefixをくっつける。
    
        # 置換対象文字列から削除された(元あった)パラメーター
        removed_numbers : list[str] = []

        modified_block : パラメーター名で置き換えたものを返す。
    """

    lines        = block.content
    param_prefix = prefix

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
    
    for line in lines:
        # 数字(小数含む)を削除しつつ removed_numbers に格納
        modified = re.sub(r'-?\d+(?:\.\d+)?(?=@)', collect_and_remove, line)
        # "@"とその後の英数字を順番に置換
        modified = re.sub(r'@([A-Za-z0-9]*)', replace_at, modified)
        modified_block.append(modified)

    return modified_block, removed_numbers, params_name

NUMBER_WITH_AT_RE = re.compile(r'-?\d+(?:\.\d+)?@[A-Za-z0-9]*')
NUMBER_CAPTURE_RE = r'(-?\d+(?:\.\d+)?)'


def get_target_path(filename:str)->str:
    """ tuneファイル内のWindows形式パスを実行環境のパスに変換する。"""

    filename = filename.replace("\\", os.sep)
    return os.path.join(target_dir, filename)


def make_context_pattern(context:list[str])->str:
    """
    contextから空白差分を無視する正規表現を作る。

    `123@`や`123@name`のように、数値の直後に`@`がついている箇所は
    ソース照合時に数値ワイルドカードとして扱う。
    """

    context_text = "".join(context)
    tokens:list[str] = []

    def add_literal(text:str):
        text = re.sub(r'\s+', '', text)
        tokens.extend(re.escape(c) for c in text)

    last = 0
    for match in NUMBER_WITH_AT_RE.finditer(context_text):
        add_literal(context_text[last:match.start()])
        tokens.append(NUMBER_CAPTURE_RE)
        last = match.end()
    add_literal(context_text[last:])

    return r'\s*'.join(tokens)


def get_context_matches(filename:str, context:list[str]):
    """ ファイルのなかのcontextに合致した箇所を取得する。"""

    path = get_target_path(filename)
    # print(path)

    if not os.path.exists(path):
        raise Exception(f"file not found : {path}")

    with open(path, 'r', encoding='utf-8') as f:
        filetext = f.read()

    pattern = make_context_pattern(context)
    matches = list(re.finditer(pattern, filetext, flags=re.MULTILINE | re.DOTALL))

    return path, filetext, matches


def get_context_numbers(filename:str, context:list[str])->list[str]:
    """ context内の`@`付き数値に対応する、現在のソースコード上の数値を取得する。"""

    _, _, matches = get_context_matches(filename, context)

    if len(matches) != 1:
        print("target context : ")
        print(context)
        raise Exception(f"Error : matched count = {len(matches)}")

    return list(matches[0].groups())


def replace_context(filename:str, context:list[str], modified:list[str]):
    """ ファイルのなかのcontextに合致したところをmodifiedに置換する。"""

    path, filetext, matches = get_context_matches(filename, context)

    if len(matches) != 1:
        print("target context : ")
        print(context)
        raise Exception(f"Error : replaced count = {len(matches)}")

    # これに置き換える。
    replaced = "".join(modified)
    match = matches[0]
    new_text = filetext[:match.start()] + replaced + filetext[match.end():]

    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_text)

def add_content(filename:str, marker : str, lines: list[str]):
    """
    ソースファイルのmarkerが書いてある行の次の行にlinesを追加する。
    """
    path = get_target_path(filename)

    if not os.path.exists(path):
        raise Exception(f"file not found : {path}")

    # ファイルの内容を読み込む
    with open(path, 'r', encoding='utf-8') as f:
        content = f.readlines()

     # 新しい内容を格納するリスト
    new_content = []
    marker_found = False

    for line in content:
        new_content.append(line)
        if marker in line and not marker_found:
            # マーカーが見つかった次の行にlinesを挿入
            new_content.extend(lines)
            marker_found = True

    if not marker_found:
        raise Exception(f"Error : marker not found, marker = {marker} , file path = {filename}")

    # ファイルに書き戻す
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_content)


def print_block(block:list[str]):
    """
    blockの画面出力用
    """
    for line in block:
        print(line)


def apply_parameters(tune_file:str , params_file : str, target_dir:str):

    try:
        params = read_parameters(params_file)
    except:
        params : list[Entry] = []

    tune_blocks = read_tune_file(tune_file)

    # 変数名を列挙する。
    for tune_block in tune_blocks:
        content_block = get_context_block(tune_block)
        prefix        = content_block.params[0] if content_block.params else ""
        modified_block , _, params_name = parse_content_block(content_block , prefix)

        # print("modified block")
        # print(modified_block)

        # contextのコピー。
        context_lines = list(modified_block)

        if prefix:
            # contextをparamの実際の値で置き換えていく。
            for param_name in params_name:
                # このパラメーターの値
                value , type = next(((e.v, e.type) for e in params if e.name == param_name), (None,None))
                if value is None:
                    print_block(content_block.content)
                    raise Exception(f"Error! : {param_name} not found in {params_file}.")

                # print(f"replace : {param_name} -> {value}")

                # パラメーターはfloat表記で持っているので、"int"を要求しているならintに変換する必要がある。
                if type == "int":
                    value = int(value + 0.5) # このときに値を丸める。

                # in-place文字置換           
                context_lines[:] = [line.replace(param_name, str(value)) for line in context_lines]

        else:
            # 無名contextブロック
            # この場合、強制的に次の無名addブロックで置換する
            for add_block in tune_block.add_blocks:
                if len(add_block.params) == 0:
                    context_lines = add_block.content
                    break
                else:
                    raise Exception("置換対象となる無名addブロックが来ていない。")


        # 前者を後者で置換する。
        filename = tune_block.setblock['file']
        print(f"file : {filename}")

        # print("target context")
        # print_block(block.context_lines_no_param)
        # print("final context")
        # print_block(context_lines)

        context  = content_block.content
        replaced = context_lines
        replace_context(filename, context, replaced)

        print(f"Patch applied to {prefix} .. done.")

    print("All patches have been applied successfully.")



def tune_parameters(tune_file:str, params_file : str, target_dir:str):

    print("start tune_parameters()")

    try:
        params = read_parameters(params_file)
    except:
        params : list[Entry] = []

    # いったん、全部使わないパラメーターとして、使っているものだけをFalseにする。
    for param in params:
        param.not_used = True

    blocks = parse_tune_file(tune_file)

    # `#param`で明示された、既存USI option用のパラメーターを追加する。
    # これはソースコード上の`@`置換とは独立しており、tune対象エンジンへ
    # `setoption name ... value ...`を送るためだけに使う。
    for explicit_param in read_explicit_params(tune_file, blocks):
        result = next((p for p in params if p.name == explicit_param.name), None)
        if result is None:
            params.append(explicit_param)
        else:
            result.type     = explicit_param.type
            result.min      = explicit_param.min
            result.max      = explicit_param.max
            result.step     = explicit_param.step
            result.delta    = explicit_param.delta
            result.comment  = explicit_param.comment
            result.v        = min(result.max, max(result.min, result.v))
            result.not_used = False

    tune_blocks = read_tune_file(tune_file, blocks)

    def check_params(tune_block:TuneBlock):
        # linesのなかから、変数(`@`)を探して、なければparamに追加。
        block = get_context_block(tune_block)
        prefix = block.params[0] if block.params else ""
        _ , _, params_name = parse_content_block(block, prefix)

        if not prefix:
            return

        filename = tune_block.setblock['file']
        source_numbers = get_context_numbers(filename, block.content)

        if len(source_numbers) != len(params_name):
            raise Exception(f"Error : parameter count mismatch, file = {filename}, prefix = {prefix}, params = {len(params_name)}, source numbers = {len(source_numbers)}")

        for param_name, number in zip(params_name, source_numbers):
            try:
                # print(f"variable {param_name}")
                result = next((p for p in params if p.name == param_name), None)
                if result is None:
                    # 変数がなかったので、paramsに追加。
                    # print("..appended")
                    number = float(number)
                    if number > 0:
                        min_, max_ = 0.0 , number * 2
                    elif number == 0: # 0 になっとる。なんぞこれ。boolと違うか？0,1と扱う。
                        min_, max_ = 0, 1
                    else:
                        min_, max_ = number * 2 , 0.0

                    # stepが1未満だとint化すると同じ値になってしまうので、
                    # 少なくとも1以上でなければならない
                    step  = max((max_ - min_) / 20 , 1)

                    # deltaは固定でいいと思う。
                    # 一回のパラメーターを動かす量は step * delta なので stepのほうで調整がなされる。
                    delta = 0.0020

                    # 整数化したものと値が異なる ⇨ 小数部分がある ⇨ float
                    type = "int" if number == int(number) else "float"

                    params.append(Entry(param_name, type , number, min_, max_ , step, delta,"", False))            

                else:
                    # print(f"..found")
                    # 使っていた。
                    result.not_used = False
            except Exception as e:
                raise Exception(f"{e} , param_name = {param_name}")

    # 変数名を列挙する。
    for tune_block in tune_blocks:
        # content blockで使われている`@`を列挙する。
        check_params(tune_block)

    # これでパラメーターファイルは確定したので、これを書き出す。
    write_parameters(params_file, params)

    # このあと、sourceコードにpatchを当てにいく。

    #  add blockをparseして、ソースコードの追加場所を探し、そこに追加する。
    for tune_block in tune_blocks:
        filename      = tune_block.setblock['file']
        content_block = get_context_block(tune_block)
        prefix        = content_block.params[0] if content_block.params else ""
        modified_block , _, params_name = parse_content_block(content_block, prefix)

        # 置換対象文字列
        context = content_block.content

        # add_blocksのなかに無名blockがあるなら、contextは、それで置き換えられる。
        # さもなくば、↑のmodified_blockで置き換える。

        replaced = False
        for add_block in tune_block.add_blocks:
            block_name = add_block.params[0] if len(add_block.params) >= 1 else ""
            
            modified_block2 , _, _ = parse_content_block(add_block, prefix)

            if block_name:
                print(f"add block, prefix = {prefix} , name = {block_name}")
                # block_nameをマーカーとして、その次の行に追加する
                add_content(filename, block_name, modified_block2)
            else:
                # context block名
                prefix = content_block.params[0] if content_block.params else ""
                print(f"replace block, prefix = {prefix}")

                # contextが合致する箇所を探す。
                replace_context(filename, context, modified_block2)
                replaced = True

        if not replaced:
            print(f"modified block, prefix = {prefix}")
            replace_context(filename, context, modified_block)

        # あと、ここで得られた変数を追加する。
        #  "int myValue;"" みたいな文字列と、
        #  "TUNE(SetRange(-100, 100), myValue, SetDefaultRange);"みたいな文字列を構築する。
        tune_params_to_declare : list[str] = []
        tune_params_to_options : list[str] = []
        for param_name in params_name:
            # 同じ名前があるはず..
            p = next((p for p in params if p.name == param_name), None)
            if p is None:
                raise Exception() # この可能性はないはず..
            v = int(p.v) if p.type == 'int' else p.v
            tune_params_to_declare.append(f"{p.type} {p.name} = {v}; {'//' + p.comment if p.comment else ''}\n")
            tune_params_to_options.append(f"TUNE(SetRange({p.min}, {p.max}), {p.name}, SetDefaultRange);\n")

        # print(tune_params_to_declare)
        # print(tune_params_to_options)
        # これらをそれぞれ
        # `#set tune`と`#set declare`で指定されたところに追加する。

        if 'declaration' in tune_block.setblock:
            add_content(filename, tune_block.setblock['declaration'], tune_params_to_declare)
        if 'options' in tune_block.setblock:
            add_content(filename, tune_block.setblock['options']    , tune_params_to_options)

    print("end tune_parameters()")


if __name__ == "__main__":

    # 使い方
    # python tune.py [apply|tune] patch_file target_dir

    args = sys.argv
    target_dir = args[3] if len(args) >= 4 else "source"
    tune_file  = args[2] if len(args) >= 3 else "suisho10.tune"
    command    = args[1] if len(args) >= 2 else ""

    target_dir = args[3] if len(args) >= 4 else "source"
    tune_file  = args[2] if len(args) >= 3 else "param/checkshogi.tune"
    command    = args[1] if len(args) >= 2 else "apply"

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
        error_msg = traceback.format_exc()
        print(f"Exception : {e}")
        print(error_msg)
