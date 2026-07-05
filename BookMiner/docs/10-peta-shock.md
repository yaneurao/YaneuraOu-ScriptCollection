# 10. peta shock 化

この章では、BookMiner の運用で出てくる `peta_shock`、`peta_read`、`peta next`、`peta_refutation`、`peta_depth_gap`、`peta_unsolved`、`peta_opponent` が何をしているのか、なぜ必要なのかを説明します。

## peta shock 化とは

peta shock 化は、やねうら王の `makebook peta_shock` コマンドで通常定跡 DB を変換する処理です。

BookMiner.py が探索して書き出す `book_miner-....db` は、各局面をエンジンで調べた結果を集めた通常定跡 DB です。leaf 側には探索結果がありますが、root 側の指し手の評価値が、その先の定跡木全体を最善に進めた結果をまだ十分に反映していないことがあります。既存の `.ybb` も読み込み対象として扱えます。

peta shock 化は、定跡木を後ろから辿り、leaf の評価値を min-max で親局面へ伝播させます。変換後の `peta_book-....db` では、内部ノードの指し手評価値が、その指し手で進んだ先の最善応手を踏まえた値に更新されます。

![peta shock 化による min-max 伝播](assets/peta-shock-minimax.svg)

## なぜ必要なのか

BookMiner は定跡を一度に完成させるのではなく、次の周回を繰り返して定跡木を伸ばします。

```text
1. 通常bookを peta shock 化して peta_book を作る
2. peta_book を peta next、peta_refutation、peta_depth_gap、peta_unsolved、peta_opponent のいずれかで辿り、次に掘る局面を book/think_sfens.txt に書き出す
3. book/think_sfens.txt を enqueue して探索する
4. 探索結果で通常bookが増える
5. もう一度 peta shock 化する
```

peta shock 化せずに通常bookだけを見ると、途中局面の評価値が古いままになり、どの枝を次に伸ばすべきかを判断しにくくなります。peta shock 化すると、末端までの結果が root 側へ戻ってくるため、`peta next` が「定跡木全体を見たうえで次に掘る候補」を選びやすくなります。

一方で、peta shock 化によって、もともと2番手以下だった指し手が best に入れ替わることがあります。BookMiner ではこれを「反駁」と呼びます。反駁された指し手が depth 0 のままだと、その評価値はまだ十分に延長されていない可能性があり、root 側へ強いノイズとして戻ることがあります。通常の leaf 延長の中で反駁された leaf だけを優先する場合は `peta_refutation` を使います。また、best に近い評価値だが depth が浅い候補手を延長するために `peta_depth_gap` を使います。負けた棋譜の変化周辺を重点的に延長したい場合は `peta_unsolved` を使います。過去に頒布した定跡をそのまま使ってくる相手を想定して対策候補を掘りたい場合は `peta_opponent` を使います。

対局用に使う定跡も、基本的には peta shock 化後の `peta_book-....db` または外部で作った `.ybb` を使います。

## 通常bookとpeta_book

BookMiner の運用では、主に次の2種類の DB が出てきます。

| ファイル | 役割 |
|---|---|
| `book/backup/book_miner-....db` | BookMiner.py が探索結果を保存する通常定跡 DB。次に探索するときの元データです。既存の `.ybb` も起動時に読み込めます。 |
| `book/backup/peta_book-....db` | 通常定跡 DB を peta shock 化した DB。`peta next`、`peta_refutation`、`peta_depth_gap`、`peta_unsolved`、`peta_opponent`、対局用の基本入力です。外部で作った `.ybb` も `peta_read` で読み込めます。 |

`peta_book-....db` / `peta_book-....ybb` は派生物です。通常bookを更新したあとに古い peta book を使い続けると、新しく掘った評価値が反映されません。探索後は、再度 peta shock 化して新しい peta book を作ります。Python版 BookMiner.py の新規書き出しは `.db` です。

## BookMinerの各コマンド

BookMiner の GUI ボタンと CLI コマンドは次の対応です。

| GUI | CLI | 内容 |
|---|---|---|
| `peta_shock` | `p` | 現在の通常bookを peta shock 化して、生成された `peta_book-....db` を読み込みます。通常bookが未変更なら既存DBを再利用し、変更済みなら書き出してから変換します。変換元が既存 `.ybb` の場合は `.ybb` のまま変換します。 |
| `peta_read` | `r` | すでに存在する `peta_book-....db` または `peta_book-....ybb` を読み込みます。peta shock 化自体は行いません。 |
| `peta next` | `pn eval_diff [max_step] [max_book_ply] [book_extend_ply] [eval_limit]` | 読み込み済みの peta_book を辿り、次に掘る候補を `book/think_sfens.txt` に書き出します。 |
| `peta refutation` | `pr eval_refutation_margin [eval_diff] [max_step] [max_book_ply] [book_extend_ply] [eval_limit]` | `peta next` の leaf のうち、元DBでは best ではなかった反駁leafだけを `book/think_sfens.txt` に書き出します。 |
| `peta depth gap` | `pdg eval_per_ply [eval_diff] [max_step] [max_book_ply] [book_extend_ply] [eval_limit]` | `peta next` と同じ範囲で、best以外の候補手がbestより浅く、depth差ぶん延長すると逆転しうる場合に、そのPV leafを `book/think_sfens.txt` に書き出します。 |
| `peta unsolved` | `pu [eval_drop_limit] [max_step] [max_book_ply] [book_extend_ply] [eval_limit]` | `book/think_unsolved_sfens.txt` の棋譜prefixから peta_book 上の best PV leaf を `book/think_sfens.txt` に書き出します。 |
| `peta opponent` | `po [eval_diff] [max_step] [game_ply_limit] [book_extend_ply] [eval_limit]` | `book/book_opponent/` に置いた相手定跡と現行 peta_book の best 進行を辿り、対策候補leafを `book/think_sfens.txt` に書き出します。 |
| `デフォルト値` | `sd eval_diff max_step game_ply_limit book_extend_ply eval_limit` | 手順2系コマンドと `enqueue` が `None` やメタ情報なし行で使う共通デフォルト値を設定します。 |
| `enqueue` | `e` | `book/think_sfens.txt` を探索 queue に積みます。探索条件は各行のメタ情報を使います。 |

通常は `peta_shock` → `peta next` → `enqueue` を繰り返します。通常の leaf 延長のうち反駁されたものだけを優先したい場合は `peta refutation`、best に近いが浅すぎる候補を延長したい場合は `peta depth gap`、負けた棋譜の周辺を重点的に掘る場合は `peta unsolved`、過去配布定跡への対策候補を掘る場合は `peta opponent` を使います。メモリや時間の都合で別マシンで peta shock 化する場合は、外部で作った `peta_book-....db` または `peta_book-....ybb` を `book/backup/` に置き、`peta_read` → `peta next` / `peta refutation` / `peta depth gap` / `peta unsolved` / `peta opponent` → `enqueue` と進めます。

`peta_shock` は、起動時に読み込んだ通常DB、または最後に `w` / 自動保存で書き出した通常DBからメモリ内容が変わっていなければ、その既存DBを変換元として再利用します。追加で掘っていないのに同じ内容の `book_miner-....db` を増やさないためです。起動時に `.ybb` を読み込んで未変更なら、その `.ybb` も変換元として再利用できます。

![peta_shock と peta next / peta_refutation / peta_depth_gap / peta_unsolved / peta_opponent](assets/peta-shock-next.svg)

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

変換に成功すると、BookMiner は `tmp-peta_book-....db` を正式な `peta_book-....db` に置き換えます。変換途中のファイルを完成済みの peta_book として扱わないためです。`makebook peta_shock` は `.db -> .db` と `.ybb -> .ybb` のみ対応しているため、変換元と変換先の拡張子は揃えます。

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

`peta_refutation` は、`peta next` と同じように root から peta_book を辿り、leaf の先へ伸ばす局面を探します。ただし、leaf として見つかった局面のうち、定跡から抜ける最後の1手が反駁された手だけを出力します。

GUIでは `peta refutation`、CLIでは `pr` コマンドです。

```text
pr 100 30 9999 200 None 400
```

引数は `eval_refutation_margin eval_diff max_step max_book_ply book_extend_ply eval_limit` の順です。GUIでは `peta next` と `peta refutation` で `max step` を別々に指定できます。手順2の行が空欄ならデフォルト値行の値が使われ、`None` と明示した場合は直前の `sd` で設定した値が使われます。

判定式は次の通りです。

```text
peta shock後の反駁候補手評価値 - peta shock後の旧best手評価値 >= eval_refutation_margin
```

`peta_refutation` は `peta next` の探索範囲に入った leaf だけを対象にします。通常の `peta next` では候補が多すぎるが、反駁された leaf を優先して延長したい場合に使います。

## peta_depth_gap

`peta_depth_gap` は、`peta next` と同じように root から peta_book を BFS で辿ります。その到達範囲内で、best以外の登録済み指し手が best より浅く、depth差ぶん延長すれば best を逆転しうる場合に抽出します。

GUIでは `peta depth gap`、CLIでは `pdg` コマンドです。

```text
pdg 0.1 30 9999 200 None 400
```

引数は `eval_per_ply eval_diff max_step max_book_ply book_extend_ply eval_limit` の順です。`eval_diff` と `max_step` は `peta next` と同じ意味です。共通引数に `None` を指定すると直前の `sd` で設定した値を使います。

判定式は次の通りです。

```text
候補手評価値 + (best.depth - 候補手.depth) * eval_per_ply >= best評価値
```

例えば peta shock 後に best が `eval=100 depth=10`、候補手が `eval=95 depth=1` だった場合、depth差は `9` です。`eval_per_ply=1` なら `95 + 9 = 104` となるため、その候補手をさらに掘る価値があるものとして抽出します。`eval_per_ply` には `0.5` のような小数も指定できます。

ただし、best の `depth` が `1000` 以上の局面は対象外です。peta shock 後の番兵値や過大な depth を、実際に読んだ手数として扱って大量抽出することを避けるためです。

`peta_depth_gap` は条件を満たした候補手を指したあと、peta_book 上の best PV を depth 0 または DB 外まで辿り、そのPV leafを `book/think_sfens.txt` に書き出します。GUI の `peta depth gap` ボタンは `pdg eval_per_ply eval_diff max_step max_book_ply book_extend_ply eval_limit` に対応します。

## peta_unsolved

`peta_unsolved` は、`book/think_unsolved_sfens.txt` に書いた棋譜の各prefix局面について、peta_book 上の best PV を leaf まで辿り、そのleaf局面を `book/think_sfens.txt` に書き出します。

GUIでは `peta unsolved`、CLIでは `pu` コマンドです。

```text
pu None None 200 None 400
```

引数は `eval_drop_limit max_step max_book_ply book_extend_ply eval_limit` の順です。共通引数に `None` を指定すると直前の `sd` で設定した値を使います。`eval_drop_limit` は棋譜rootの評価値からroot側視点でどれだけ悪化したprefixを除外するかで、`None` の場合は `99999` 扱いです。

`peta_unsolved` は `book/think_sfens.txt` を書き出すだけで、自動的には enqueue しません。負けた棋譜の変化周辺を確認してから、手動で `enqueue` します。

## peta_opponent

`peta_opponent` は、過去に頒布した定跡などを仮想敵として使い、その定跡をそのまま使ってくる相手への対策候補を作るための処理です。

相手定跡は次のフォルダに置きます。

```text
book/book_opponent/
```

Python版 BookMiner.py / BookMinerCpp ともに、相手定跡として `.db` と `.ybb` の両方を読みます。

GUIでは `peta opponent`、CLIでは `po` コマンドです。

```text
po 0 9999 200 20 400
```

引数は `eval_diff max_step game_ply_limit book_extend_ply eval_limit` の順です。`eval_diff` は各局面で best からどれくらい評価値が離れた候補まで辿るかです。通常は `0` で、best と同評価値の候補だけを辿ります。

`peta_opponent` は、現在読み込んでいる peta_book と相手定跡を、手番に応じて交互に辿ります。どちらかの定跡が切れた局面を見つけたら、そこから現在の peta_book の PV leaf まで進め、その leaf 局面を `book/think_sfens.txt` に書き出します。DFS ではなく BFS で辿るため、分岐と合流を繰り返す定跡でも手順組み合わせを過剰に膨らませにくい作りです。

各 peta 抽出コマンドの `book_extend_ply`、`eval_limit`、`max_book_ply` を数値で指定すると、書き出し行は次の形式になります。

```text
startpos moves 7g7f 3c3d, book_extend_ply=20, eval_limit=400, game_ply_limit=200
```

この行を `enqueue` した場合、行ごとのメタ情報が探索条件として使われます。`None` の場合はメタ情報を書かず、`sd` で設定したデフォルト値を使います。同じ局面が複数の手順2から出た場合、自動enqueueの集約ではより大きいメタ情報を持つ行を残します。

`max_step` は `book/think_sfens.txt` には書き出されません。これは `peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent` が leaf を探すときの範囲だけを絞る値です。

一方、`game_ply_limit` は leaf 抽出時にも使われ、さらに `game_ply_limit=...` として `book/think_sfens.txt` に書き出されます。そのため、その後に `enqueue` すると探索worker側の手数上限としても効きます。抽出対象を絞りたいだけで、enqueue後の掘り方を変えたくない場合は、`game_ply_limit` ではなく `max_step` を小さくしてください。

GUI の `デフォルト値` 行は、各 peta 操作や `enqueue` の直前に `sd ...` として BookMiner.py / BookMinerCpp へ送られます。KifManager の棋譜抽出で作った `think_sfens.txt` のように行末メタ情報がない場合も、この `sd` の値で `game_ply_limit`、`book_extend_ply`、`eval_limit` が決まります。

## eval_diff と eval_limit

`peta next` / `peta_refutation` / `peta_depth_gap` / `peta_opponent` の `eval_diff`、`peta_unsolved` の `eval_drop_limit`、`peta_refutation` の `eval_refutation_margin`、`peta_depth_gap` の `eval_per_ply`、手順2各行の `eval_limit`、`game_ply_limit`、`book_extend_ply` は別の値です。

| 値 | 使う場所 | 意味 |
|---|---|---|
| `eval_diff` | `peta next` / `pn`、`peta_refutation` / `pr`、`peta_depth_gap` / `pdg`、`peta_opponent` / `po` | peta_book の中で、root の best move からどれくらい評価値が離れた枝まで辿るか。`peta_opponent` では各局面で best に近い候補をどこまで辿るか。 |
| `eval_drop_limit` | `peta_unsolved` / `pu` | 棋譜rootの評価値からroot側視点でどれくらい悪化したprefixを除外するか。 |
| `eval_refutation_margin` | `peta_refutation` / `pr` | peta shock後の反駁候補手と旧best手の評価値差がどれくらい以上なら抽出するか。 |
| `eval_per_ply` | `peta_depth_gap` / `pdg` | bestとのdepth差1手あたり、候補手の評価値がどれくらい改善しうると仮定するか。 |
| `max_step` | 手順2の各 peta 抽出コマンド | peta_book の中で leaf を探す範囲を制限する値。`think_sfens.txt` には書き出されず、enqueue後の探索条件にはならない。 |
| `eval_limit` | 手順2の各 peta 抽出コマンドが書き出す行メタ情報、`enqueue` / `e` | `book/think_sfens.txt` を再生するとき、定跡木の外へ出る枝を評価値で止めるか。 |
| `game_ply_limit` | 手順2の各 peta 抽出コマンドが書き出す行メタ情報、`enqueue` / `e` | この手数に到達したらそれ以上掘らない上限。 |
| `book_extend_ply` | 手順2の各 peta 抽出コマンドが書き出す行メタ情報、`enqueue` / `e` | `book/think_sfens.txt` の行ごとに、棋譜末端から best line を何手分延長するかを上書きする値。 |

既存定跡から広く掘り始める初回は、`eval_diff 99999` と `eval_limit 99999` のように大きな値を使うと、評価値による枝刈りをほぼ無効化できます。通常運用では、目的に応じてこれらを小さくし、形勢が大きく傾いた枝を広げすぎないようにします。

## MATERIAL版を使う理由

peta shock 化には、探索用の強いエンジンではなく MATERIAL 版のやねうら王を使います。peta shock 化は定跡 DB の変換処理であり、評価関数ファイルを使って局面を深く探索する処理ではありません。

MATERIAL 版は評価関数ファイルを必要とせず、メモリ使用量が小さいため、大きな定跡 DB を変換する用途に向いています。

## 運用上の注意

- `peta_read` は変換を実行しません。外部で作った `peta_book-....db` または `peta_book-....ybb` を読み込むだけです。
- `peta next`、`peta_refutation`、`peta_depth_gap`、`peta_unsolved`、`peta_opponent` はメモリ上に読み込まれている peta_book を辿ります。DB ファイルを毎回読み直すわけではありません。
- 通常bookを探索で増やしたあとは、古い peta_book ではなく、新しく peta shock 化した peta_book を使います。
- `makebook peta_shock` に渡す通常定跡 DB は sort 済みである必要があります。BookMiner.py が `p` で書き出した `book_miner-....db` はそのまま使えます。
