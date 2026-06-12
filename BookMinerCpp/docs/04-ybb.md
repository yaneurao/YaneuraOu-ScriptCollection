# 4. `.ybb` と定跡ファイル

BookMinerCpp は、通常バックアップとして やねうら王 バイナリ定跡DB (`.ybb`) を使います。

Python 版 BookMiner は、通常バックアップをやねうら王標準定跡フォーマットの `.db` として保存します。
一方、BookMinerCpp は高速な読み書きと省メモリ化のため、通常バックアップを `.ybb` にします。

## `.db` と `.ybb`

`.db` はテキスト形式です。
局面は `sfen ...` 行で書かれ、その下に指し手と評価値が並びます。

やねうら王 バイナリ定跡DB `.ybb` はバイナリ形式です。
局面 key は `PackedSfen` の32 byteで、指し手は `Move16` の2 byte、評価値は `int16_t` の2 byteです。

BookMinerCpp の通常運用では、ユーザーが `.ybb` を手で編集する必要はありません。

## ファイル構成

ファイル名の例:

```text
user_book.ybb
```

BookMinerCpp のバックアップでは次の名前になります。

```text
book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb
```

ファイル先頭に局面 index 領域を置き、その直後に moves 領域を置きます。
やねうら王本体が on-the-fly probe するときは、index を二分探索して必要な指し手列だけを同じ `.ybb` の moves 領域から読みます。

## index 領域

index 領域の形式は次です。

```text
magic[16] = "YANE-BINBOOK-V1\0"
record_count uint64
flags uint64
records[record_count]:
  packed_sfen[32]
  moves_offset uint64
  ply uint16
  move_count uint16
```

header は 32 byte です。

```text
16 byte magic
 8 byte record_count
 8 byte flags
```

index record は 44 byte です。

```text
32 byte packed_sfen
 8 byte moves_offset
 2 byte ply
 2 byte move_count
```

`packed_sfen[32]` の辞書順で strict sort します。
index 領域のサイズは `32 + record_count * 44` byte です。

## moves 領域

moves 領域は、header の `flags` によって 4 byte または 6 byte record の連続になります。

```text
flags bit0 = 0:
move16 uint16
eval   int16

flags bit0 = 1:
move16 uint16
eval   int16
depth  uint16
```

BookMinerCpp が通常バックアップとして書き出す `.ybb` は `flags bit0 = 0` です。
BookMinerCpp の作業DBでは `depth` を使わないため、不要な `depth` は保存しません。

`move16` は cshogi の内部 `move16` ではなく、やねうら王本体の `Move16` です。
cshogi で扱う場合は PSV形式の move16 がこれと同じbit配置なので、書き出し時は `cshogi.move16_to_psv()`、読み戻し時は `cshogi.move16_from_psv()` を使います。
cshogi の内部 `move16` をそのまま保存してはいけません。

各局面の指し手列は、moves 領域先頭から `moves_offset` byte の位置を起点に `move_count` 件ぶん読みます。

## endianness

数値は little endian です。

現在の主な想定環境は Windows / Linux / macOS の little endian CPU です。
magic に version や endian field は持たせていません。

## 評価値

BookMinerCpp 内部では評価値を `int16_t` で保持します。
mate score は Python 版と同じ考え方で `VALUE_MATE = 32000` 系へ寄せ、通常の大きすぎる評価値は clamp します。

`.ybb` は BookMinerCpp の正規バックアップ形式なので、保存済み `.ybb` は正規化済みの評価値を持つ前提です。

## `.db` との変換

変換スクリプトは次です。

```text
../makebook/convert_db_to_ybb.py
../makebook/convert_ybb_to_db.py
```

`.db` から `.ybb`:

```bash
python3 ../makebook/convert_db_to_ybb.py input.db output
```

出力は `.ybb` です。

```text
output.ybb
```

第2引数には `.ybb` path または `.ybb` 拡張子なしの basename を指定します。
`-index` / `-moves` suffix は指定できません。

`.ybb` から `.db`:

```bash
python3 ../makebook/convert_ybb_to_db.py input output.db
```

第1引数には `.ybb` path または `.ybb` 拡張子なしの basename を指定します。
`-index` / `-moves` suffix は指定できません。

これらのスクリプトは `cshogi.Board.to_psfen()` / `set_psfen()` を使って packed sfen を変換します。
最大の利点は、巨大定跡でも少ないメモリで `.db` / `.ybb` を相互変換できることです。
一時runを作って外部sortするため、全局面をオンメモリに載せません。
必要メモリは主に chunk サイズに依存し、入力ファイル全体のサイズに比例して増える設計ではありません。

## やねうら王で使う

やねうら王本体にも `.ybb` reader を追加しています。
通常の定跡ファイルとして使う場合は、`BookFile` に `.ybb` を指定します。

```text
setoption name BookFile value user_book.ybb
setoption name BookOnTheFly value true
setoption name FlippedBook value true
setoption name IgnoreBookPly value true
```

`BookOnTheFly=true` の場合、やねうら王は `.ybb` の index 領域を二分探索し、必要な moves だけを読みます。

`BookFile` に従来の `.db` 名を指定したとき、その `.db` が存在しなければ、同じ basename の `.ybb` を探します。
例えば `user_book1.db` がなく、`user_book1.ybb` がある場合、`BookFile=user_book1.db` の指定で `.ybb` が読み込まれます。

`FlippedBook=true` の場合、通常局面で hit しなければ packed sfen を直接 flip して再検索します。
このとき SFEN文字列や `Position::set()` は経由しません。

## peta shock 化の入力

やねうら王の `makebook peta_shock` は、`.ybb` を入力として受け取れるようにしています。

```text
makebook peta_shock backup/book_miner-....ybb backup/peta_book-....db
```

通常の `BookFile` と同じく、入力に `.db` 名を指定したときにその `.db` が存在しなければ、同じ basename の `.ybb` を入力として使います。

出力は従来の `.db` です。
BookMinerCpp の `p` コマンドはこの経路を自動で呼び出します。
