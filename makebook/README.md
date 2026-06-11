# makebook-script

YaneuraOu の旧 `makebook` コマンドのうち、速度要求が比較的低い定跡DB変換・整形処理を Python スクリプトとして外部化したものです。

やねうら王本体には、速度とメモリ効率が重要な `makebook peta_shock` だけを残します。

## 必要なもの

- Python 3
- `cshogi`
- `numpy`

`numpy` は `convert_from_apery.py` で Apery定跡を読むため、および `.ybb` 変換時に `cshogi.PackedSfen` を扱うために使います。

## スクリプト一覧

| スクリプト | 旧 makebook コマンド | 用途 |
| --- | --- | --- |
| [`from_sfen.py`](from_sfen.py) | `makebook from_sfen` | SFEN棋譜列から、やねうら王定跡DBを生成します。 |
| [`merge.py`](merge.py) | `makebook merge` | 2つのやねうら王定跡DBを統合します。 |
| [`merge_largebook.py`](merge_largebook.py) | `makebook merge` | 巨大な2つのやねうら王定跡DBを、一時ファイルを使って統合します。 |
| [`sort.py`](sort.py) | `makebook sort` | やねうら王定跡DBを正規化してソートします。 |
| [`sort_largebook.py`](sort_largebook.py) | `makebook sort` | 巨大なやねうら王定跡DBを、一時ファイルを使って正規化・ソートします。 |
| [`convert_db_to_ybb.py`](convert_db_to_ybb.py) | - | やねうら王定跡DB `.db` を やねうら王 バイナリ定跡DB `.ybb` へ、少ないメモリで変換します。 |
| [`convert_ybb_to_db.py`](convert_ybb_to_db.py) | - | やねうら王 バイナリ定跡DB `.ybb` を やねうら王定跡DB `.db` へ、少ないメモリで変換します。 |
| [`convert_to_apery.py`](convert_to_apery.py) | `makebook convert_to_apery` | やねうら王定跡DBを Apery定跡へ変換します。 |
| [`convert_from_apery.py`](convert_from_apery.py) | `makebook convert_from_apery` | Apery定跡をやねうら王定跡DBへ変換します。 |

[`YaneuraOuBookLib.py`](YaneuraOuBookLib.py) は共通ライブラリです。直接実行するスクリプトではありません。

## やねうら王定跡DB

このディレクトリで「やねうら王定跡DB」と呼ぶものは、以下のヘッダを持つ `.db` ファイルです。

```text
#YANEURAOU-DB2016 1.00
```

本文は局面ごとに `sfen` 行があり、その下に候補手を並べます。

```text
sfen <sfen>
<move> <ponder> <value> <depth> <move_count>
```

## やねうら王 バイナリ定跡DB `.ybb`

やねうら王 バイナリ定跡DB `.ybb` は、BookMinerCpp と、やねうら王本体の on-the-fly 定跡 probe 用のバイナリ定跡形式です。
2ファイルで1つの定跡を表します。

```text
user_book-index.ybb
user_book-moves.ybb
```

`-index.ybb` には `PackedSfen` 順に sort された固定長 index を置き、`-moves.ybb` には各局面の指し手列を置きます。
やねうら王本体は index を二分探索して、必要な局面の指し手列だけを moves file から読みます。

`.db` / `.ybb` の相互変換スクリプトの最大の長所は、入力ファイル全体をメモリに載せず、巨大な定跡でも少ないメモリで変換できることです。
一定件数または一定byte数ごとに一時runを書き出し、最後に k-way merge します。
run が多すぎる場合は `--max-open-runs` 個ずつ段階的に merge するため、数百・数千runになっても同時にopenするファイル数を抑えます。

そのため、数GB、数十GB、さらに大きな定跡ファイルでも、必要メモリは「現在処理中の chunk + merge 中の各runの現在レコード」程度に抑えられます。

注意: `.db` / `.ybb` の相互変換スクリプトは、`--tmp-dir` を指定しない場合、実行時のカレントディレクトリに `tmp/` を作り、そこに外部sort用の一時runを書き出します。入力が巨大な場合、この `tmp/` も巨大になります。空き容量の大きいSSDなどを使いたい場合は、必ず `--tmp-dir /path/to/tmp` を指定してください。

## from_sfen.py

SFEN棋譜列から、やねうら王定跡DBを生成します。

入力ファイルの各行には、次のどちらかの形式を書けます。

```text
startpos moves 7g7f 3c3d ...
sfen <board> <turn> <hands> <ply> moves 7g7f 3c3d ...
```

基本形:

```bash
python3 from_sfen.py input.sfen output.db --moves 24
```

先手番用と後手番用の入力を分ける場合:

```bash
python3 from_sfen.py bw black.sfen white.sfen output.db --moves 24
```

片側を読まない場合は `no_file` を指定できます。

```bash
python3 from_sfen.py bw black.sfen no_file output.db --moves 24
```

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--moves N` | `16` | 各棋譜行から何手目まで読むか。ponder 用に内部では `N + 1` 手まで見ます。 |

生成される候補手は、旧 `makebook from_sfen` と同じく以下の値を持ちます。

| 項目 | 値 |
| --- | --- |
| `move` | 現局面での次の指し手 |
| `ponder` | `move` の次の相手番の指し手 |
| `value` | `0` |
| `depth` | `32` |
| `move_count` | `1` |

## merge.py

2つのやねうら王定跡DBを統合します。

```bash
python3 merge.py book1.db book2.db merged.db
```

同じ SFEN が両方のDBにある場合は、旧 `makebook merge` と同じ基準で片方の候補手リストを採用します。

1. 候補手が空でない側を採用する。
2. 先頭候補手の `depth` が深い側を採用する。
3. `depth` が同じなら候補手数が多い側を採用する。
4. それも同じなら第1引数側を採用する。

片方にしかない SFEN はそのまま出力します。

オプション:

| オプション | 意味 |
| --- | --- |
| `--ignore-book-ply` | 入力DBを読むときに SFEN 末尾の手数を無視します。 |

`merge.py` は入力DBをオンメモリで読みます。巨大なDBを処理する場合は `merge_largebook.py` を使ってください。

## merge_largebook.py

巨大な2つのやねうら王定跡DBを統合します。

入力DBがソート済みであることは前提にしません。内部で `sort_largebook.py` を2回呼び出して、一時的なソート済みDBを作ってから、SFENブロック単位で2-way mergeします。

```bash
python3 merge_largebook.py book1.db book2.db merged.db
```

一時ファイルの作成先を指定する場合:

```bash
python3 merge_largebook.py book1.db book2.db merged.db --tmp-dir /mnt/ssd/tmp
```

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--tmp-dir DIR` | `./tmp` | 一時ファイルを作るディレクトリ。省略時はカレントディレクトリの `tmp/` を使います。 |
| `--chunk-positions N` | `500000` | 内部で `sort_largebook.py` に渡す1 runあたりの局面数。 |
| `--ignore-book-ply` | なし | 入力DBを読むときに SFEN 末尾の手数を無視します。 |
| `--keep-temp` | なし | 処理後に一時ファイルを削除せず残します。デバッグ用です。 |

`merge_largebook.py` は全DBをメモリに載せません。通常時に保持するのは、入力2本の現在の SFEN ブロックと、内部 sort の chunk 分だけです。

## sort.py

やねうら王定跡DBを読み直し、SFEN正規化、局面順ソート、候補手順ソートを行って書き出します。

```bash
python3 sort.py input.db sorted.db
```

主な処理:

- ヘッダ `#YANEURAOU-DB2016 1.00` を書きます。
- SFEN を `cshogi` で読み直して正規化します。
- SFEN 文字列順に局面をソートします。
- 手数違いの同一局面がある場合、手数が最小の局面だけを書きます。
- 候補手は `move_count` 降順、`value` 降順で並べます。

オプション:

| オプション | 意味 |
| --- | --- |
| `--ignore-book-ply` | 入力DBを読むときに SFEN 末尾の手数を無視します。 |

`sort.py` は入力DBをオンメモリで読みます。巨大なDBを処理する場合は `sort_largebook.py` を使ってください。

## sort_largebook.py

巨大なやねうら王定跡DBを、一時ファイルを使って正規化・ソートします。

```bash
python3 sort_largebook.py input.db sorted.db
```

一時ファイルの作成先を指定する場合:

```bash
python3 sort_largebook.py input.db sorted.db --tmp-dir /mnt/ssd/tmp
```

処理の流れ:

1. 入力DBを SFEN ブロック単位で読みます。
2. `--chunk-positions` 件ごとに、SFENを正規化してソート済みの一時runを書きます。
3. すべてのrunを k-way merge して、最終的なDBを書きます。
4. 手数違いの同一局面は、最終merge時に手数が最小の局面だけを書きます。

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--tmp-dir DIR` | `./tmp` | 一時ファイルを作るディレクトリ。省略時はカレントディレクトリの `tmp/` を使います。 |
| `--chunk-positions N` | `500000` | 1つの一時runに含める局面数。 |
| `--ignore-book-ply` | なし | 入力DBを読むときに SFEN 末尾の手数を無視します。 |
| `--keep-temp` | なし | 処理後に一時ファイルを削除せず残します。デバッグ用です。 |

## convert_db_to_ybb.py

やねうら王定跡DB `.db` を やねうら王 バイナリ定跡DB `.ybb` へ変換します。

このスクリプトは巨大ファイル向けです。
`.db` 全体をオンメモリに読み込まず、一定量ずつ一時runへ分割して外部sortするため、ファイルが非常に大きくても少ないメモリで変換できます。

```bash
python3 convert_db_to_ybb.py input.db output
```

出力は2ファイルです。

```text
output-index.ybb
output-moves.ybb
```

第2引数には、`.ybb` 拡張子や `-index` / `-moves` を付けない basename だけを指定します。

正しい例:

```bash
python3 convert_db_to_ybb.py input.db user_book
```

この場合、次の2ファイルが生成されます。

```text
user_book-index.ybb
user_book-moves.ybb
```

誤った例:

```bash
python3 convert_db_to_ybb.py input.db user_book.ybb
python3 convert_db_to_ybb.py input.db user_book-index.ybb
python3 convert_db_to_ybb.py input.db user_book-moves.ybb
```

これらは、basename と index/moves file の区別が曖昧になるためエラーにします。

処理の流れ:

1. `.db` を SFEN ブロック単位で読みます。
2. `cshogi.Board.to_psfen()` で局面を `PackedSfen` へ変換します。
3. 指し手を `Move16`、評価値を `int16_t` として一時runへ書きます。
4. 一時runを `PackedSfen` の32 byte辞書順で k-way merge して `.ybb` を書きます。

メモリ使用量は、主に `--chunk-positions` と `--chunk-bytes` で決まります。
入力 `.db` の総サイズに比例してメモリを要求する作りではありません。

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--tmp-dir DIR` | `./tmp` | 一時ファイルを作るディレクトリ。省略時は実行時のカレントディレクトリに `tmp/` を作ります。巨大DBではこの `tmp/` も巨大になるため、空き容量の大きい場所を指定してください。 |
| `--chunk-positions N` | `500000` | 1つの一時runに含める局面数の上限。 |
| `--chunk-bytes N` | `536870912` | 1つの一時runに含める概算byte数の上限。 |
| `--max-open-runs N` | `64` | k-way mergeで同時にopenするrun数の上限。 |
| `--keep-temp` | なし | 処理後に一時ファイルを削除せず残します。デバッグ用です。 |

`.ybb` は `PackedSfen` を key にするため、同一局面が複数回現れた場合はエラーにします。

## convert_ybb_to_db.py

やねうら王 バイナリ定跡DB `.ybb` を やねうら王定跡DB `.db` へ変換します。

このスクリプトも巨大ファイル向けです。
`.ybb` 全体をオンメモリに展開せず、`PackedSfen` から復元した SFEN ブロックを一定量ずつ一時runへ書き、最後に SFEN 文字列順で外部sortします。

```bash
python3 convert_ybb_to_db.py input output.db
```

第1引数には、`.ybb` 拡張子や `-index` / `-moves` を付けない basename だけを指定します。
対応する `input-index.ybb` と `input-moves.ybb` は同じフォルダから自動的に読みます。

正しい例:

```bash
python3 convert_ybb_to_db.py user_book output.db
```

この場合、次の2ファイルを入力として読みます。

```text
user_book-index.ybb
user_book-moves.ybb
```

誤った例:

```bash
python3 convert_ybb_to_db.py user_book.ybb output.db
python3 convert_ybb_to_db.py user_book-index.ybb output.db
python3 convert_ybb_to_db.py user_book-moves.ybb output.db
```

これらは、basename と index/moves file の区別が曖昧になるためエラーにします。

処理の流れ:

1. `.ybb` の index を順に読みます。
2. `cshogi.Board.set_psfen()` で `PackedSfen` から SFEN 文字列を復元します。
3. 指し手列を `.db` のテキストブロックへ変換します。
4. 一時runを SFEN 文字列順で k-way merge して `.db` を書きます。

メモリ使用量は、主に `--chunk-positions` と `--chunk-bytes` で決まります。
入力 `.ybb` の総サイズに比例してメモリを要求する作りではありません。

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--tmp-dir DIR` | `./tmp` | 一時ファイルを作るディレクトリ。省略時は実行時のカレントディレクトリに `tmp/` を作ります。巨大DBではこの `tmp/` も巨大になるため、空き容量の大きい場所を指定してください。 |
| `--chunk-positions N` | `500000` | 1つの一時runに含める局面数の上限。 |
| `--chunk-bytes N` | `536870912` | 1つの一時runに含める概算byte数の上限。 |
| `--max-open-runs N` | `64` | k-way mergeで同時にopenするrun数の上限。 |
| `--keep-temp` | なし | 処理後に一時ファイルを削除せず残します。デバッグ用です。 |

## convert_to_apery.py

やねうら王定跡DBを Apery定跡へ変換します。

```bash
python3 convert_to_apery.py input.db output.bin
```

入力:

- やねうら王定跡DB `.db`

出力:

- Apery定跡 `.bin`

変換時は、各局面の `book_key` を計算し、Apery定跡のレコード形式で出力します。候補手は `move_count` 降順、`value` 降順に並べます。`move_count` が `65535` を超える場合は `65535` に丸めます。

## convert_from_apery.py

Apery定跡をやねうら王定跡DBへ変換します。

```bash
python3 convert_from_apery.py input.bin output.db
```

入力:

- Apery定跡 `.bin`

出力:

- やねうら王定跡DB `.db`

`Apery定跡` は ponder を持たないため、変換時に次局面の定跡 bestmove を ponder として補完します。局面探索は初期局面から行います。

オプション:

| オプション | 既定値 | 意味 |
| --- | ---: | --- |
| `--unreg-depth N` | `1` | 定跡未登録局面を何手先まで探索するか。旧やねうら王実装の既定値に合わせています。 |

## 互換性確認

これらのスクリプトは、テスト用の定跡DBで旧やねうら王 `makebook` 実装と byte 単位の一致を確認しています。

- `from_sfen.py`
- `merge.py`
- `sort.py`
- `convert_to_apery.py`

`convert_from_apery.py` は、合法手生成順の差を避けるため、局面探索の合法手を USI 文字列順にソートしています。同じ順序にした旧やねうら王実装とは byte 単位で一致することを確認しています。
