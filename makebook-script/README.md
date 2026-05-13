# makebook-script

YaneuraOu の旧 `makebook` コマンドのうち、速度要求が比較的低い定跡DB変換・整形処理を Python スクリプトとして外部化したものです。

やねうら王本体には、速度とメモリ効率が重要な `makebook peta_shock` だけを残します。

## 必要なもの

- Python 3
- `cshogi`
- `numpy`

`numpy` は `convert_from_apery.py` で Apery定跡を読むために使います。

## スクリプト一覧

| スクリプト | 旧 makebook コマンド | 用途 |
| --- | --- | --- |
| [`from_sfen.py`](from_sfen.py) | `makebook from_sfen` | SFEN棋譜列から、やねうら王定跡DBを生成します。 |
| [`merge.py`](merge.py) | `makebook merge` | 2つのやねうら王定跡DBを統合します。 |
| [`sort.py`](sort.py) | `makebook sort` | やねうら王定跡DBを正規化してソートします。 |
| [`convert_to_apery.py`](convert_to_apery.py) | `makebook convert_to_apery` | やねうら王定跡DBを Apery定跡へ変換します。 |
| [`convert_from_apery.py`](convert_from_apery.py) | `makebook convert_from_apery` | Apery定跡をやねうら王定跡DBへ変換します。 |

[`yaneuraou_book.py`](yaneuraou_book.py) は共通処理です。直接実行するスクリプトではありません。

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
