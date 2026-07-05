# 5. BookMiner.py の主要コマンド

BookMiner を起動すると、プロンプトに対してコマンドを入力できます。用語は [1. 用語説明](01-terms.md) で説明しています。

![BookMiner.py の主要コマンド早見図](assets/command-map.svg)

## `h`

ヘルプを表示します。

```text
h
```

## `t`

掘る局面を読み込み、探索キューへ積みます。

```text
t
```

`t` は固定で次のファイルを読みます。

```text
book/think_sfens.txt
```

入力ファイルは、1 行が 1 つの `startpos moves ...` 形式です。

行末にカンマ区切りで `book_extend_ply=...` を付けると、その行だけ棋譜末端からの best line 延長手数を上書きできます。

```text
startpos moves 7g7f 3c3d, book_extend_ply=20
```

`book_extend_ply` が無い行、または `book_extend_ply=None` の行は、`t` コマンド第3引数の `book_extend_ply` を使います。同じ局面が複数行にある場合は、`book_extend_ply` が大きい行を採用します。数値指定は `None` より優先されます。

`t` は、入力ファイルの各行を辿り、まだ掘っていない局面をバックグラウンドの思考タスクとして投入します。この投入操作を GUI では `enqueue` と呼びます。

引数は GUI の並び順と同じで、`eval_limit`、`max_book_ply`、`book_extend_ply` の順です。

```text
t 400 200 6
```

`eval_limit` は第1引数で指定します。省略時は `400` です。`None` を指定した場合もデフォルト値の `400` を使います。

```text
t 99999
```

`t` コマンドで棋譜を辿るとき、定跡木の内部ノードは `eval_limit` では打ち切りません。
ただし、次の指し手が定跡木の外へ出る枝で、その評価値の絶対値がこの値を超えている場合は、その指し手の先へ進みません。
棋譜の末端まで到達できた場合は、そこから先の best line 延長でもこの値を使います。

最大手数は第2引数で指定できます。省略時は `settings/book_miner_settings.json5` の `max_book_ply` です。`None` を指定した場合も設定値を使います。

```text
t 400 200
```

棋譜末端から best line を何手分延長するかは、第3引数で指定できます。省略時は `6` です。`None` を指定した場合もデフォルト値の `6` を使います。

```text
t 400 200 8
```

```text
t 400 200 None
```

queue は、これから探索する局面を一時的に積んでおく待ち行列です。`enqueue` は、その queue に局面を追加する操作です。queue に積まれた局面は、探索スレッドによって順に処理されます。

進捗は画面と `log/` のログで確認してください。

GUI の `enqueue進捗` は、worker が受け取ったタスク数をもとに表示されます。探索が完全に終わった数ではありませんが、BookMiner 起動後に enqueue した累計タスクに対して、どこまで worker に渡ったかを確認できます。複数回 enqueue した場合、分母は追加分だけ増えます。

`settings/book_miner_settings.json5` の `max_book_ply` に到達した局面は思考しません。`t` の第2引数に最大手数を指定した場合は、その job だけ指定値を使います。さらに `book_extend_ply` を指定した場合は、その job だけ棋譜末端からの best line 延長手数を変更します。`None` は省略時と同じ意味です。

## `w`

現在の定跡 DB を、やねうら王の通常定跡形式で `book/backup/` に書き出します。

```text
w
```

出力例:

```text
book/backup/book_miner-20260607071000_12345.ybb
```

手数制限を付けることもできます。

```text
w 100
```

この場合、初期局面から 100 手目までの局面だけを書き出し、ファイル名に `_ply100` が付きます。

```text
book/backup/book_miner-20260607071000_12345_ply100.ybb
```

`_plyN` 付きのファイルは一部だけを書き出したものです。起動時の自動読み込み対象にはなりません。

書き出しは一度 `tmp-*.ybb` に行い、完了後に `*.ybb` へ置換します。書き出し途中のファイルを完成済みバックアップとして扱わないためです。

## `p`

現在の定跡 DB を peta shock 化して読み込みます。通常は現在の定跡 DB を `book/backup/` に書き出し、その書き出したファイルを peta shock 化します。

```text
p
```

`p` は、現在の定跡 DB をその場で peta shock 化し、すぐに `peta_book` として使えるようにするコマンドです。

何を変換しているのか、なぜ `peta_book` が必要なのかは [10. peta shock 化](10-peta-shock.md) を参照してください。

重要なのは、`p` は `book/backup/` の最新ファイルを探すのではなく、`p` 自身が書き出したバックアップファイル、または読み込み後に未変更であることが確認できている既存バックアップファイルを peta shock 化することです。これにより、定期自動バックアップや別の書き出しとタイミングが重なった場合でも、意図しないファイルを変換元にしにくくなります。

起動直後や `w` 直後のように、メモリ上の通常bookが最後に読み込み/保存した通常DBから変わっていない場合、`p` は通常DBを再書き出しせず、その既存ファイルを再利用します。このときログには `p command source book reused = ...` が出ます。

通常の周回作業では、BookMiner が動いている環境で `p` を使うのが基本です。

`p` で新しく通常定跡 DB を書き出した場合、通常定跡 DB と peta shock 化後の DB は、同じ timestamp と局面数を持つペアになります。既存通常DBを再利用した場合も、その通常DBに対応する `peta_book-....ybb` が作られます。

```text
book/backup/book_miner-20260607103251_14505901.ybb
book/backup/peta_book-20260607103251_14505901.ybb
```

## `r`

peta shock 化済みの `book/backup/peta_book-....ybb` を読み込みます。既存の `.db` 形式も読み込めます。

```text
r
```

`r` は read の略です。
`r` 自体は peta shock 化を行いません。別マシンで変換した定跡を持ち込む場合など、先に自分で `peta_book-....ybb` を作って `book/backup/` に置いてから使います。

path を省略した場合は、`book/backup/` にある最新の `peta_book-....ybb` または `peta_book-....db` を読みます。

読み込む peta book を明示することもできます。

```text
r book/backup/peta_book-20260607071000_12345.ybb
```

指定した path は、まず BookMiner.py の実行フォルダからの相対 path として解決されます。通常は BookMiner フォルダで起動するので、上のように `book/backup/...` と指定します。

次に、`book/` からの相対 path としても解決します。そのため、次の指定も同じファイルを指します。

```text
r backup/peta_book-20260607071000_12345.ybb
```

GUI の `peta_read` ボタンは引数なしの `r` を送るため、最新の `peta_book-....ybb` または `peta_book-....db` を読みます。外部で peta shock 化した結果を使う場合は、そのファイルを `book/backup/` に置いてから `peta_read` を押します。

このあと `pn` コマンドを使うと、次に掘る局面を列挙できます。

## `pn`

peta shock 化して読み込んだ定跡から、leaf の先へ定跡ツリーを伸ばすための局面を書き出します。

```text
pn 30
```

アルゴリズムの説明は下記のページをご覧ください。

- [10. peta shock 化](10-peta-shock.md)
- [YaneuraOu-ScriptCollection/PetaNext](../../PetaNext/README.md)

出力先:

```text
book/think_sfens-black.txt
book/think_sfens-white.txt
book/think_sfens.txt
```

第 1 引数は eval diff です。root の best move からどの程度評価値が離れた枝まで辿るかを指定します。

例えば `pn 100` は、best move から評価値が大きく離れすぎていない枝も辿って、leaf の先へ伸ばす局面を探す、という意味です。

値を大きくすると、より多くの枝を辿るので出力される局面が増えます。値を小さくすると、best move に近い枝だけを辿ります。

第 2 引数で最大 step 数(rootからの手数)を指定できます。省略時は `9999` です。

```text
pn 30 40
```

第 3 引数で最大手数を指定できます。

```text
pn 30 40 200
```

`settings/book_miner_settings.json5` の `max_book_ply` に到達する局面は、出力対象から除外されます。第 3 引数を指定した場合は、その値を使います。`None` を指定すると省略時のデフォルト値を使います。

第 4 引数で `book_extend_ply` を指定できます。数値を指定すると、書き出される `book/think_sfens.txt` の各行に `book_extend_ply=...` が付きます。

`settings/book_miner_settings.json5` の `peta_next_start_sfens_path` で指定されたファイルが存在する場合、`pn` コマンドは `startpos` ではなく、そのファイルに書かれた局面集合から辿り始めます。
`pn` コマンドは、すでにメモリ上に読み込まれている `peta_book` を辿ります。`pn` を実行しても、peta shock 化済みDBファイルを読み直すわけではありません。
詳しくは [4. 定跡を掘るための基礎](04-basics.md#peta_next-の開始局面集合を変える) を参照してください。

## peta_next_refutation

`peta_next` と同じように peta_book を辿りますが、leaf として見つかった局面のうち、定跡から抜ける最後の1手が反駁された手だけを書き出します。

```text
pnf 30 100 9999 200 None
```

引数は順に `eval_diff`、`eval_refutation_margin`、`max_step`、`max_book_ply`、`book_extend_ply` です。`eval_refutation_margin` の省略時は `100`、`max_step` の省略時は `9999` です。任意引数に `None` を指定するとデフォルト値を使います。

leaf を作る最後の1手について、peta shock 後のDBでは depth 0 の best であり、peta shock 前の通常bookでは best ではなく、次の条件を満たすものだけを `book/think_sfens.txt` へ書き出します。

```text
peta shock後の反駁候補手評価値 - peta shock後の旧best手評価値 >= eval_refutation_margin
```

通常の `peta_next` では leaf が多すぎる場合に、反駁された leaf だけを優先して掘るためのコマンドです。

## peta_next_gap

peta shock 後、`peta_next` と同じように root から BFS で辿れる範囲で、best以外の登録済み指し手が best より浅く、depth差ぶん追加で掘ると best を逆転しうる場合に抽出します。

```text
png 30 0.1 9999 200 None
```

引数は順に `eval_diff`、`eval_per_ply`、`max_step`、`max_book_ply`、`book_extend_ply` です。`eval_diff` と `max_step` は `peta_next` と同じ意味です。`eval_per_ply` の省略時は `0.1` です。0以上の数値を指定し、`0.5` のような小数も指定できます。任意引数に `None` を指定するとデフォルト値を使います。
判定式は次の通りです。

```text
候補手評価値 + (best.depth - 候補手.depth) * eval_per_ply >= best評価値
```

例えば best が `eval=100 depth=10`、候補手が `eval=95 depth=1`、`eval_per_ply=1` の場合、`95 + (10 - 1) * 1 = 104` なので抽出対象です。

ただし、best の `depth` が `1000` 以上の局面は対象外です。peta shock 後の番兵値や過大な depth を、実際に読んだ手数として扱って大量抽出することを避けるためです。

出力先:

```text
book/think_sfens.txt
```

抽出された行は、候補手を指したあと、peta_book 上の best PV を depth 0 または DB 外まで辿った leaf 局面です。

## peta_unsolved

負けた棋譜などを `book/think_unsolved_sfens.txt` に入れておき、その棋譜上の各prefix局面から peta_book 上の best PV を leaf まで辿った局面を `book/think_sfens.txt` に書き出します。

```text
pu None None 200 None
```

引数は順に `eval_diff`、`max_step`、`max_book_ply`、`book_extend_ply` です。`None` を指定するとデフォルト値を使います。GUIで空欄にした場合も `None` として送信します。

`eval_diff` は、棋譜のroot局面の評価値からroot側視点でどれだけ悪化したprefixを除外するかです。`None` の場合は `99999` 扱いになり、通常は評価値差では除外しません。

`pu` は `book/think_sfens.txt` を書き出すだけです。書き出し後の `enqueue` は手動で実行してください。

## peta_opponent

過去に頒布した定跡など、対策したい相手定跡を `book/book_opponent/` に置き、現在読み込んでいる peta_book と仮想対局させます。
双方が自分の手番で best 候補を辿り、どちらかの定跡が切れた地点から現在の peta_book の PV leaf まで進めた局面を `book/think_sfens.txt` に書き出します。

```text
po 0 9999 200 20
```

引数は順に `eval_diff`、`max_step`、`max_book_ply`、`book_extend_ply` です。`None` を指定するとデフォルト値を使います。GUIで空欄にした場合も `None` として送信します。

```text
po None None 200 None
```

`eval_diff` は、各局面で best と同評価値または近い評価値の候補をどこまで辿るかです。通常は `0` で、best と同評価値の候補だけを辿ります。

`book_extend_ply` を数値で指定すると、書き出される `book/think_sfens.txt` の各行に `book_extend_ply=...` が付きます。その後 `enqueue` したとき、この値が `t` コマンド側の `book_extend_ply` より優先されます。`book_extend_ply=None` の場合は行メタデータを付けず、通常通り `enqueue` 側の `book extend ply` を使います。

Python版 BookMiner.py / BookMinerCpp ともに、`book/book_opponent/*.db` と `*.ybb` の両方を相手定跡として使えます。

## `i`

局面を問い合わせます。

```text
i startpos
```

`startpos moves ...` 形式で、指定した経路上の局面を確認することもできます。

```text
i startpos moves 7g7f 3c3d 2g2f
```

## `m`

先後反転した局面が両方登録されている場合に、片側へマージします。

```text
m
```

通常の周回作業で必ず使うコマンドではありません。

💡 先後反転した局面が両方登録されている定跡DBを開始時に用いる時にこのコマンドで修復します。

## `b`

定跡ツリーを幅優先に辿り、各局面の ply を付け直します。

```text
b
```

通常の周回作業で必ず使うコマンドではありません。

💡 各局面のplyが壊れてしまっている定跡DBを用いる時にこのコマンドで修復します。

## `q`

現在の定跡 DB を `book/backup/` に書き出して終了します。

```text
q
```

出力ファイル名には、書き出し時刻と局面数が入ります。

```text
book/backup/book_miner-20260607103251_14505901.ybb
```

## `!`

保存せず終了します。

```text
!
```

直前までの作業を捨てる可能性があるので、通常は `q` を使ってください。
