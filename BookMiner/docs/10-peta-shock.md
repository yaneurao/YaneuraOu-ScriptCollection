# 10. peta shock 化

この章では、BookMiner の運用で出てくる `peta_shock`、`peta_read`、`peta_next`、`peta_refutation` が何をしているのか、なぜ必要なのかを説明します。

## peta shock 化とは

peta shock 化は、やねうら王の `makebook peta_shock` コマンドで通常定跡 DB を変換する処理です。

BookMiner が探索して書き出す `book_miner-....db` は、各局面をエンジンで調べた結果を集めた通常定跡 DB です。leaf 側には探索結果がありますが、root 側の指し手の評価値が、その先の定跡木全体を最善に進めた結果をまだ十分に反映していないことがあります。

peta shock 化は、定跡木を後ろから辿り、leaf の評価値を min-max で親局面へ伝播させます。変換後の `peta_book-....db` では、内部ノードの指し手評価値が、その指し手で進んだ先の最善応手を踏まえた値に更新されます。

![peta shock 化による min-max 伝播](assets/peta-shock-minimax.svg)

## なぜ必要なのか

BookMiner は定跡を一度に完成させるのではなく、次の周回を繰り返して定跡木を伸ばします。

```text
1. 通常bookを peta shock 化して peta_book を作る
2. peta_book を peta_next または peta_refutation で辿り、次に掘る局面を book/think_sfens.txt に書き出す
3. book/think_sfens.txt を enqueue して探索する
4. 探索結果で通常bookが増える
5. もう一度 peta shock 化する
```

peta shock 化せずに通常bookだけを見ると、途中局面の評価値が古いままになり、どの枝を次に伸ばすべきかを判断しにくくなります。peta shock 化すると、末端までの結果が root 側へ戻ってくるため、`peta_next` が「定跡木全体を見たうえで次に掘る候補」を選びやすくなります。

一方で、peta shock 化によって、もともと2番手以下だった指し手が best に入れ替わることがあります。BookMiner ではこれを「反駁」と呼びます。反駁された指し手が depth 0 のままだと、その評価値はまだ十分に延長されていない可能性があり、root 側へ強いノイズとして戻ることがあります。この候補を重点的に延長するために `peta_refutation` を使います。

対局用に使う定跡も、基本的には peta shock 化後の `peta_book-....db` を使います。

## 通常bookとpeta_book

BookMiner の運用では、主に次の2種類の DB が出てきます。

| ファイル | 役割 |
|---|---|
| `book/backup/book_miner-....db` | BookMiner が探索結果を保存する通常定跡 DB。次に探索するときの元データです。 |
| `book/backup/peta_book-....db` | 通常定跡 DB を peta shock 化した DB。`peta_next`、`peta_refutation`、対局用の基本入力です。 |

`peta_book-....db` は派生物です。通常bookを更新したあとに古い `peta_book-....db` を使い続けると、新しく掘った評価値が反映されません。探索後は、再度 peta shock 化して新しい `peta_book-....db` を作ります。

## BookMinerの各コマンド

BookMiner の GUI ボタンと CLI コマンドは次の対応です。

| GUI | CLI | 内容 |
|---|---|---|
| `peta_shock` | `p` | 現在の通常bookを書き出し、そのファイルを peta shock 化して、生成された `peta_book-....db` を読み込みます。 |
| `peta_read` | `r` | すでに存在する `peta_book-....db` を読み込みます。peta shock 化自体は行いません。 |
| `peta_next` | `n eval_diff [max_step]` | 読み込み済みの peta_book を辿り、次に掘る候補を `book/think_sfens.txt` に書き出します。 |
| `peta refutation` | `f eval_refutation_margin [eval_limit]` | 反駁された depth 0 best のうち、旧bestとの差が大きい候補を `book/think_sfens.txt` に書き出します。`eval_limit` 指定時は enqueue 前に retire が確定している候補を除外します。 |
| `enqueue` | `e eval_limit` のあと `t` | `book/think_sfens.txt` を探索 queue に積みます。 |

通常は `peta_shock` → `peta_next` → `enqueue` を繰り返します。反駁された depth 0 best を重点的に延長したい場合は、`peta_next` の代わりに `peta refutation` を使います。メモリや時間の都合で別マシンで peta shock 化する場合は、外部で作った `peta_book-....db` を `book/backup/` に置き、`peta_read` → `peta_next` または `peta refutation` → `enqueue` と進めます。

![peta_shock と peta_next / peta_refutation](assets/peta-shock-next.svg)

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
makebook peta_shock backup/book_miner-YYYYMMDDHHMMSS_N.db backup/peta_book-YYYYMMDDHHMMSS_N.db.tmp
quit
```

変換に成功すると、BookMiner は `*.db.tmp` を正式な `peta_book-....db` に置き換えます。変換途中のファイルを完成済みの peta_book として扱わないためです。

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

## peta_refutation

`peta_refutation` は、peta shock 後に best になっている指し手のうち、depth が 0 のものを調べます。

その指し手が peta shock 前の通常bookでは2番手以下であり、かつ旧bestとの差が十分に大きい場合、要注意の反駁候補として `book/think_sfens.txt` に書き出します。

判定式は次の通りです。

```text
旧best評価値 - 反駁候補手の旧評価値 >= eval_refutation_margin
```

例えば peta shock 前に旧bestが `200`、反駁候補手が `100` だった場合、差は `100` です。`eval_refutation_margin` が `100` 以下なら抽出対象になります。`f` コマンドで値を省略した場合のデフォルトも `100` です。

`f 100 400` のように第2引数へ `eval_limit` を指定すると、反駁候補手の peta shock 前の評価値の絶対値が `400` を超える候補は書き出しません。これらは `enqueue` しても DB 外へ出る枝として retire するため、最初から `book/think_sfens.txt` に積まないほうが効率的です。GUI の `peta refutation` ボタンは、enqueue 欄に入力されている `eval_limit` を自動で渡します。CLI で `eval_limit` を省略した場合は、この事前除外を行いません。

`peta_refutation` は root から BFS で辿るのではなく、読み込み済みの `peta_book` の全nodeを走査します。すべてのnodeに到達可能であるという前提で、各nodeの best の depth だけを直接確認します。`max_book_ply` による除外は行いません。

抽出された行は、その反駁候補手を指した後の `sfen ... moves ...` です。`enqueue` すると、反駁候補手の先を追加探索できます。

## eval_diff と eval_limit

`peta_next` の `eval_diff`、`peta_refutation` の `eval_refutation_margin`、`enqueue` の `eval_limit` は別の値です。

| 値 | 使う場所 | 意味 |
|---|---|---|
| `eval_diff` | `peta_next` / `n` | peta_book の中で、root の best move からどれくらい評価値が離れた枝まで辿るか。 |
| `eval_refutation_margin` | `peta_refutation` / `f` | peta shock 前の旧bestと反駁候補手の評価値差がどれくらい以上なら抽出するか。 |
| `eval_limit` | `enqueue` / `e` + `t`、GUIの `peta refutation` | `book/think_sfens.txt` を再生するとき、定跡木の外へ出る枝を評価値で止めるか。GUI の `peta refutation` では、書き出し前の事前除外にも使います。 |

既存定跡から広く掘り始める初回は、`eval_diff 99999` と `eval_limit 99999` のように大きな値を使うと、評価値による枝刈りをほぼ無効化できます。通常運用では、目的に応じてこれらを小さくし、形勢が大きく傾いた枝を広げすぎないようにします。

## MATERIAL版を使う理由

peta shock 化には、探索用の強いエンジンではなく MATERIAL 版のやねうら王を使います。peta shock 化は定跡 DB の変換処理であり、評価関数ファイルを使って局面を深く探索する処理ではありません。

MATERIAL 版は評価関数ファイルを必要とせず、メモリ使用量が小さいため、大きな定跡 DB を変換する用途に向いています。

## 運用上の注意

- `peta_read` は変換を実行しません。外部で作った `peta_book-....db` を読み込むだけです。
- `peta_next` と `peta_refutation` はメモリ上に読み込まれている peta_book を辿ります。DB ファイルを毎回読み直すわけではありません。
- 通常bookを探索で増やしたあとは、古い peta_book ではなく、新しく peta shock 化した peta_book を使います。
- `makebook peta_shock` に渡す通常定跡 DB は `sfen` 順に sort されている必要があります。BookMiner が `p` で書き出した `book_miner-....db` は sort 済みです。
