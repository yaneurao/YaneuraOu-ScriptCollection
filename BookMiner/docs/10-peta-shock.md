# 10. peta shock 化

この章では、peta shock 化そのものだけを説明します。

peta shock 化した DB を使って `book/think_sfens.txt` を作る操作、つまり `peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent` については、次の章で説明します。

- [11. peta book を使って次に掘る局面を作る](11-peta-operations.md)

## peta shock 化とは

peta shock 化は、やねうら王の `makebook peta_shock` コマンドで通常定跡 DB を変換する処理です。

BookMiner が探索して保存する `book_miner-....db` は、各局面をエンジンで調べた結果を集めた通常定跡 DB です。leaf 側には探索結果がありますが、root 側の指し手評価値は、その先の定跡木全体を最善に進めた結果をまだ十分に反映していないことがあります。

peta shock 化は、定跡木を後ろから辿り、leaf 側の評価値を min-max で root 側へ伝播させます。変換後の `peta_book-....db` / `peta_book-....ybb` では、内部ノードの指し手評価値が、その指し手で進んだ先の最善応手を踏まえた値に更新されます。

![peta shock 化による min-max 伝播](assets/peta-shock-minimax.svg)

## なぜ必要なのか

BookMiner は、定跡を一度に完成させるのではなく、次の周回を繰り返して定跡木を伸ばします。

```text
1. 通常bookを掘る
2. 通常bookを peta shock 化して peta_book を作る
3. peta_book から、次に掘る局面を book/think_sfens.txt に書き出す
4. book/think_sfens.txt を enqueue して探索する
5. 探索結果で通常bookが増える
6. もう一度 peta shock 化する
```

peta shock 化せずに通常bookだけを見ると、途中局面の評価値が古いままになり、どの枝を次に伸ばすべきかを判断しにくくなります。peta shock 化すると、末端までの結果が root 側へ戻ってくるため、次に掘る候補を定跡木全体の結果に基づいて選びやすくなります。

対局用に使う定跡も、基本的には peta shock 化後の `peta_book-....db` または外部で作った `.ybb` を使います。

## 通常bookとpeta_book

BookMiner の運用では、主に次の2種類の DB が出てきます。

| ファイル | 役割 |
|---|---|
| `book/backup/book_miner-....db` | BookMiner.py が探索結果を保存する通常定跡 DB。次に探索するときの元データです。既存の `.ybb` も起動時に読み込めます。 |
| `book/backup/peta_book-....db` | 通常定跡 DB を peta shock 化した DB。次に掘る局面の列挙や、対局用定跡として使います。外部で作った `.ybb` も `peta_read` で読み込めます。 |

`peta_book-....db` / `peta_book-....ybb` は派生物です。通常bookを更新したあとに古い peta book を使い続けると、新しく掘った評価値が反映されません。探索後は、再度 peta shock 化して新しい peta book を作ります。

Python版 BookMiner.py が新しく書き出す通常bookは `.db` です。起動時に既存 `.ybb` を読み込んで、まだ通常bookを変更していない場合は、その `.ybb` を peta shock 化の入力として再利用できます。この場合、出力も `.ybb` になります。

## BookMiner で作る場合

BookMiner では `p` コマンド、GUI では `peta_shock` ボタンで peta shock 化します。

```text
p
```

`p` は、現在の通常bookを `book/backup/` に保存し、そのファイルを peta shock 化し、生成された peta book を読み込みます。

ただし、起動時に読み込んだ通常DB、または最後に `w` / 自動保存で書き出した通常DBからメモリ内容が変わっていない場合は、同じ内容の通常DBを再書き出しせず、その既存ファイルを変換元として再利用します。追加で掘っていないのに同じ `book_miner-....db` を増やさないためです。

出力例:

```text
book/backup/book_miner-20260607103251_14505901.db
book/backup/peta_book-20260607103251_14505901.db
```

外部で作った peta book を読むだけなら、`r` コマンド、GUI では `peta_read` を使います。

```text
r
```

`r` は peta shock 化を実行しません。すでに存在する `peta_book-....db` または `peta_book-....ybb` を読み込むだけです。

## 直接実行する場合

やねうら王側のコマンド形式は次の通りです。

```text
makebook peta_shock <readbook> <writebook> [shrink] [fast]
```

`readbook` と `writebook` は、エンジンオプション `BookDir` からの相対パスです。

| オプション | 意味 |
|---|---|
| `shrink` | 最善手と同じ評価値の指し手以外を削除して、出力される定跡ファイルを小さくします。次に掘る候補を広く列挙したい BookMiner 運用では通常使いません。 |
| `fast` | テンポラリファイルを書き出さずに高速化します。ただし、メモリ使用量は増えます。 |

BookMiner の `p` コマンドは、内部的には `YO-MATERIAL.exe` におおむね次のようなコマンドを送ります。

```text
setoption name BookDir value book
setoption name BookFile value no_book
setoption name FlippedBook value true
setoption name USI_Hash value 1
makebook peta_shock backup/book_miner-YYYYMMDDHHMMSS_N.db backup/tmp-peta_book-YYYYMMDDHHMMSS_N.db
quit
```

変換に成功すると、BookMiner は `tmp-peta_book-....db` を正式な `peta_book-....db` に置き換えます。変換途中のファイルを完成済みの peta book として扱わないためです。

`makebook peta_shock` は `.db -> .db` と `.ybb -> .ybb` のみ対応しています。変換元と変換先の拡張子は揃えます。

## value / depth の伝播

peta shock 化済み DB では、ある親局面 `P` の指し手 `m` で進めた子局面 `C` が同じ DB 内に存在する場合、原則として `m` の `(value, depth)` は、子局面 `C` の best move から次のように決まります。

```text
P の m.value = - C の best.value
P の m.depth = min(C の best.depth + 1, 9999)
```

評価値を反転するのは、手番が入れ替わるためです。子局面で相手から見て良い値は、親局面の手番側から見ると悪い値になります。

子局面 `C` が DB 内に存在しない枝は leaf です。この場合、その指し手の `(value, depth)` は入力 DB にあった探索結果を保持します。

## 同評価値・depth違いの `value - 1`

同じ親局面内で、best move と同じ評価値だが depth が異なる指し手がある場合、best 以外の指し手は書き出し時に `value` を1だけ下げることがあります。

これは仕様です。千日手絡みなどで、評価値は同じだが遠回りしている手順があると、depth の比較によって迂回手順を選び続けることがあります。そこで、同評価値・depth違いの非best手は `value - 1` して、同じ評価値の候補として選ばれ続けにくくしています。

実装上は、やねうら王の `source/book/makebook2025.cpp` で次の条件に該当する指し手を補正しています。

```text
best move と value が同じ
best move と depth が異なる
best move ではない
```

このため、peta shock 化済み DB を PV として辿ると、途中で評価値が1だけ変わったように見えることがあります。これは、上記条件に該当する限り不整合ではありません。

## MATERIAL版を使う理由

peta shock 化には、探索用の強いエンジンではなく MATERIAL 版のやねうら王を使います。peta shock 化は定跡 DB の変換処理であり、評価関数ファイルを使って局面を深く探索する処理ではありません。

MATERIAL 版は評価関数ファイルを必要とせず、メモリ使用量が小さいため、大きな定跡 DB を変換する用途に向いています。

BookMiner では、この用途のために `YO-MATERIAL.exe` を使います。`YO-MATERIAL.exe` は、自分で MATERIAL 版をビルドするか、やねうら王 News Letter で頒布されている最新の MATERIAL 版を `YO-MATERIAL.exe` という名前に変更して、`BookMiner/` に配置します。

## 運用上の注意

- `peta_read` は変換を実行しません。外部で作った `peta_book-....db` または `peta_book-....ybb` を読み込むだけです。
- 通常bookを探索で増やしたあとは、古い peta_book ではなく、新しく peta shock 化した peta_book を使います。
- `makebook peta_shock` に渡す通常定跡 DB は sort 済みである必要があります。BookMiner.py が `p` で書き出した `book_miner-....db` はそのまま使えます。
- peta shock 化した DB を使って次に掘る局面を作る操作は [11. peta book を使って次に掘る局面を作る](11-peta-operations.md) を参照してください。
