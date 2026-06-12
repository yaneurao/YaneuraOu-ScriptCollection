# CommonLib

YaneuraOu-ScriptCollection 内の複数スクリプトから使う共通ライブラリ置き場です。各 `.py` は直接実行するより、他のスクリプトから import して使うことを想定しています。

## import方法

サブディレクトリのスクリプトから使う場合は、実行ファイルの位置から `CommonLib` を `sys.path` に追加してから import します。

```python
from pathlib import Path
import sys

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneuraOuBookLib import read_yaneuraou_book, write_yaneuraou_book
```

## YaneuraOuBookLib.py

やねうら王定跡DBを扱うライブラリです。テキスト形式の `.db` とバイナリ形式の `.ybb` を、ファイル名の拡張子で自動判定します。

主な型と関数:

| 名前 | 用途 |
| --- | --- |
| `YANEURAOU_BOOK_HEADER_V1` | やねうら王標準定跡DB `.db` のヘッダ文字列。 |
| `YBB_*` 定数 | `.ybb` の magic、flags、struct 定義。 |
| `BookMove` | `move`, `ponder`, `value`, `depth`, `move_count` を持つ定跡候補手。 |
| `read_yaneuraou_book(path, ignore_book_ply=False)` | `.db` / `.ybb` を読み、`dict[str, list[BookMove]]` を返します。 |
| `read_yaneuraou_book_blocks(path, ignore_book_ply=False)` | `.db` / `.ybb` を局面ブロック単位で逐次読みします。 |
| `write_yaneuraou_book(book, dst)` | `dict[str, list[BookMove]]` を `.db` / `.ybb` に書きます。 |
| `write_yaneuraou_book_block(out, sfen, moves)` | `.db` の1局面ブロックを書きます。巨大DB処理の一時run用です。 |
| `insert_book_move(moves, new_move)` | 同じ指し手があれば置換し、採択回数を加算します。 |
| `normalize_sfen(sfen)` | `cshogi` で SFEN を読み直して正規化します。 |
| `trim_number(sfen)` / `sfen_ply(sfen)` | SFEN 末尾の手数を処理します。 |
| `trim_sfen_ply(sfen)` | 先頭の `sfen` と末尾手数を分離します。手数がなければ `1` を返します。 |
| `is_ybb_path(path)` | 拡張子が `.ybb` か判定します。 |
| `resolve_ybb_input(path)` / `ybb_path_from_output(path)` | `.ybb` 拡張子省略時の入力・出力パスを解決します。 |
| `pack_sfen()` / `board_from_packed_sfen()` | `cshogi.Board` と `.ybb` の `PackedSfen` を相互変換します。 |
| `usi_to_move16()` / `move16_to_usi()` | USI指し手文字列と `.ybb` に保存するやねうら王 `Move16` を相互変換します。 |

オンメモリで読む例:

```python
book = read_yaneuraou_book("book.db")
book_ybb = read_yaneuraou_book("book.ybb")
write_yaneuraou_book(book, "sorted.db")
write_yaneuraou_book(book, "sorted.ybb")
```

ブロック単位で読む例:

```python
for sfen, moves in read_yaneuraou_book_blocks("book.ybb"):
    print(sfen, len(moves))
```

`.ybb` は ponder と各手の採択回数を持たない形式です。`.ybb` を読み込んだ場合、`ponder` は `"none"`、`move_count` は `1` になります。`.ybb` に書く場合は `move`, `value`, `depth` を保存します。

## TeacherFormatLib.py

教師局面ファイルの共通フォーマット定義と補助関数です。

主な内容:

| 名前 | 用途 |
| --- | --- |
| `HCPE`, `PSV`, `HCPE3_HEADER` | `cshogi` / numpy で扱う固定長レコードの dtype。 |
| `HCPE_SIZE`, `PSV_SIZE` | 固定長レコードの byte size。 |
| `ConvertStats` | 変換したファイル数、対局数、局面数を集計する dataclass。 |
| `collect_inputs(input_path, input_ext, recursive)` | ファイルまたはフォルダから対象入力ファイルを集めます。 |
| `output_for_file(...)` | 入力ファイルに対応する出力ファイル名を作ります。 |
| `read_exact(f, size, context)` | 指定byte数を読み、不足時に `EOFError` を投げます。 |
| `validate_fixed_record_file(path, record_size, format_name)` | 固定長レコードファイルのサイズを検証し、レコード数を返します。 |

## TeacherConvertLib.py

教師局面ファイルのストリーミング変換関数です。入力は `Path`、出力は open 済みの `BinaryIO` を渡します。

主な関数:

| 名前 | 変換 |
| --- | --- |
| `convert_pack_to_hcpe_file(input_path, output, ...)` | やねうら王 pack 棋譜から HCPE。 |
| `convert_hcpe_to_psv_file(input_path, output, ...)` | HCPE から PSV。 |
| `convert_psv_to_hcpe_file(input_path, output, ...)` | PSV から HCPE。 |
| `convert_hcpe3_to_hcpe_file(input_path, output, ...)` | HCPE3 から HCPE。 |
| `convert_hcpe3_to_psv_file(input_path, output, ...)` | HCPE3 から PSV。 |

呼び出し例:

```python
from pathlib import Path
from TeacherConvertLib import convert_hcpe_to_psv_file

with open("output.psv", "wb") as out:
    stats = convert_hcpe_to_psv_file(Path("input.hcpe"), out)
print(stats.positions)
```

## YaneShogiLib.py

将棋スクリプト全般で使う補助ライブラリです。

主な内容:

| 名前 | 用途 |
| --- | --- |
| `trim_sfen()` / `trim_sfen_ply()` | SFENから末尾手数を取り除きます。 |
| `flipped_move()` / `flipped_sfen()` | 指し手や局面を先後反転します。 |
| `evalstr_to_int()` | USIの `score cp` / `score mate` を定跡用評価値へ変換します。 |
| `clamp_eval()` / `clamp_int16()` / `clamp_uint16()` | 評価値や整数値を保存形式の範囲へ丸めます。 |
| `visits_from_scores()` | MultiPV評価値から疑似訪問回数を作ります。 |
| `Engine` | USIエンジンを起動して `go` / `go_multipv` を呼ぶラッパーです。 |
| `Board` / `NonStandardBoard` | `cshogi.Board` 周辺の薄いラッパーです。 |
| `GameDataEncoder` / `GameDataDecoder` | やねうら王 pack 棋譜の読み書き補助です。 |
| `KifWriter` / `Hcpe3Writer` | 棋譜や HCPE3 を連番ファイルへ書く補助クラスです。 |
