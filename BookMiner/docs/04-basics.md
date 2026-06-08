# 4. 定跡を掘るための基礎

この章では、BookMiner に局面を渡して掘る流れ、次に掘る局面の作り方を説明します。
用語は [1. 用語説明](01-terms.md)、`startpos moves ...` 形式は [3. USI と position コマンド](03-usi.md) で説明しています。

## 探索キューと enqueue

BookMiner は、入力ファイルに書かれている局面を読んだ瞬間に、すべてをその場で最後まで探索するわけではありません。

BookMiner の内部には「これから探索する局面」を積んでおく場所があります。この場所を queue と呼びます。queue は待ち行列という意味で、先に積まれたタスクから順に、探索用エンジンへ渡されて処理されます。

`enqueue` は、その queue にタスクを追加する操作です。

![enqueue と探索 worker](assets/queue-workers.svg)

BookMiner のコマンドラインでは `t` コマンドが enqueue に相当します。GUI では `enqueue` ボタンを押すと、内部的には `t` コマンドが送られます。

つまり、GUI の `enqueue` は「いますぐ全局面を同期的に掘り終える」ボタンではなく、次のファイルに書かれている局面を読み、まだ掘っていない局面を探索キューへ積むボタンです。

```text
book/think_sfens.txt
```

queue に積まれた局面は、BookMiner が起動している探索スレッドによって順に処理されます。処理の進捗は画面と `log/` のログに出力されます。

GUI の `enqueue進捗` は、BookMiner を起動してから enqueue した累計タスク数に対して、worker が受け取ったタスク数を表示します。

```text
enqueue進捗 30000/50000
```

これは「これまで enqueue した 50000 タスクのうち 30000 タスクが worker に渡った」という意味です。最後に渡った数件はまだ探索中かもしれませんが、残りタスク量を把握する目的では十分です。

もう一度 enqueue すると、分母は追加分だけ増えます。例えば 50000 タスク中 30000 タスクが worker に渡った状態で 72462 行を追加 enqueue した場合、進捗は `30000/122462` のようになります。

## 掘りたい局面を渡す

通常は KifManager で棋譜を抽出し、BookMiner の入力ファイルを作ります。

出力先は次の場所にします。

```text
BookMiner/book/think_sfens.txt
```

BookMiner 側では、起動後に `t` と入力すると、このファイルを読み込みます。

```text
t
```

この `t` コマンドは、GUI では `enqueue` ボタンに対応します。

別のファイルを読む場合は、path を指定します。

```text
t book/my_positions.txt
```

`t` コマンドは各行の指し手を順に辿り、まだ掘っていない局面があれば探索キューへ積みます。積まれたタスクは、探索用エンジンで順に思考されます。

棋譜で指定された途中局面、つまり定跡木の内部ノードは、eval limit では止まりません。
BookMiner は指し手を順に再生して通過します。

ただし、到達した定跡木 leaf から外へ出る指し手の評価値が eval limit を超えている場合、その指し手の先は延長しません。

また、`settings/book_miner_settings.json5` の `max_book_ply` に到達した場合も、その対局の処理はそこで止まります。

棋譜の末端まで到達した場合は、そこからエンジンの best line を `THINK_COMMAND_PLY` 手分だけ延長して掘ります。この延長中も、評価値の絶対値が eval limit 以上になったら停止します。

## 通常定跡 DB を書き出して peta shock 化する

局面を掘ったら、通常は `p` コマンドを使います。

```text
p
```

`p` は、現在の定跡 DB を通常のやねうら王定跡形式として `book/backup/` に書き出し、その書き出したファイルを peta shock 化して読み込みます。

バックアップの出力先は `book/backup/` です。

```text
book/backup/book_miner-20260607071000_12345.db
```

peta shock 化後のファイルも `book/backup/` に保存されます。通常定跡 DB と同じ timestamp と局面数を使います。

```text
book/backup/peta_book-20260607071000_12345.db
```

`w` コマンドで書き出しだけを行い、`r` コマンドで peta shock 化だけを行うこともできます。ただし、通常の周回作業では `p` を使うほうが安全です。`p` は、自分で書き出したバックアップファイルをそのまま変換元に使うため、`w` の完了確認漏れや、定期自動バックアップとの取り違えを避けやすくなります。

## peta shock 化の内部処理

`p` または `r` コマンドは、通常定跡 DB を peta shock 化します。

```text
p
```

`p` は、いま書き出した通常バックアップを `YO-MATERIAL.exe` に渡し、対応する `book/backup/peta_book-....db` を作って読み込みます。

`book/backup/peta_book-....db` は `makebook peta_shock` が出力した正規形の DB とみなし、高速読み込みします。この読み込みでは、先後反転局面との merge や古い評価値形式の補正は行いません。

`r` は、path を省略した場合、`book/backup/` にある最新の通常バックアップを `YO-MATERIAL.exe` に渡します。

`r` に path を指定する場合、通常は BookMiner フォルダからの相対 path として次のように書きます。

```text
r book/backup/book_miner-20260607071000_12345.db
```

`book/` からの相対 path として、次のように書くこともできます。

```text
r backup/book_miner-20260607071000_12345.db
```

やねうら王側のコマンドは次の形式です。

```text
makebook peta_shock <readbook> <writebook> [shrink] [fast]
```

`readbook` と `writebook` は、エンジンオプション `BookDir` からの相対パスです。

BookMiner の `r` コマンドは、内部的には `YO-MATERIAL.exe` におおむね次のようなコマンドを送ります。

```text
setoption name BookDir value book
setoption name BookFile value no_book
setoption name FlippedBook value true
setoption name USI_Hash value 1
makebook peta_shock backup/book_miner-20260607071000_12345.db backup/peta_book-20260607071000_12345.db.tmp
quit
```

変換元が `book_miner-YYYYMMDDHHMMSS_局面数.db` という名前なら、実際の出力先は対応する `backup/peta_book-YYYYMMDDHHMMSS_局面数.db.tmp` です。変換に成功すると、BookMiner はこれを `book/backup/peta_book-YYYYMMDDHHMMSS_局面数.db` に置き換えます。

変換元のファイル名から局面数が分からない場合は、変換時刻だけを使います。

```text
book/backup/peta_book-YYYYMMDDHHMMSS.db
```

![peta_shock と peta_next](assets/peta-shock-next.svg)

## 次に掘る局面を求める

peta shock 化した定跡から、leaf の先へ定跡ツリーを伸ばすための局面を求めるには `n` コマンドを使います。

```text
n 30
```

`30` は、root の best move の評価値からどの程度評価値が離れた枝まで辿るかを表す値です。

詳しいアルゴリズムについては、以下のページをご覧ください。

- [YaneuraOu-ScriptCollection/PetaNext](../../PetaNext/README.md)


```text
n 100 10
```

のように指定すると、rootの best moveの評価値から100離れた枝を、rootから10手先まで辿ります。


`n` コマンドは次のファイルを書き出します。

```text
book/think_sfens-black.txt
book/think_sfens-white.txt
book/think_sfens.txt
```

`book/think_sfens.txt` は、先手用と後手用の「leaf から先へ伸ばす局面」を交互に混ぜたものです。

`settings/book_miner_settings.json5` の `max_book_ply` に到達する局面は、次に掘る局面としては書き出されません。

`n`コマンドを使ったときに、`think_sfens.txt`に何局面を書き出したのかが表示されます。それを見て、これを掘るかどうかを決めます。

掘ることにするなら、`t` コマンドでこのファイルを読み込まれてタスクに積みます。(そのあとタスクが消化されて徐々に掘られていきます。)

[YaneuraOu-ScriptCollection/PetaNext](../../PetaNext/README.md)スクリプトを使う運用も考えられますが、BookMiner では `n` コマンドがその役割を持っています。

## peta_next の開始局面集合を変える

通常、`n` コマンドは平手の初期局面、つまり `startpos` から定跡ツリーを辿ります。
特定の局面から先だけを対象にしたい場合は、`settings/book_miner_settings.json5` の `peta_next_start_sfens_path` で指定されているファイルを作成します。

デフォルトは次の場所です。

```text
book/peta_start_sfens.txt
```

このファイルには、1 行に 1 つずつ開始局面を書きます。
形式は `startpos moves ...` です。

```text
startpos moves 7g7f 3c3d 2g2f
startpos moves 2g2f 8c8d 2f2e 8d8e
```

このファイルが存在する場合、`n` コマンドは `startpos` ではなく、ここに書かれた局面集合を peta_next の開始局面集合として扱います。
つまり、開始局面集合ファイルに書いた局面から先を辿り、leaf の先へ伸ばす局面を `book/think_sfens.txt` に書き出します。

このファイルが存在しない場合は、従来通り `startpos` から辿ります。

重要なのは、`n` コマンドはメモリ上に読み込まれている `peta_book` を辿るだけ、という点です。
`n` コマンドを実行しても、`book/backup/peta_book-....db` をファイルから読み直すわけではありません。
peta shock 化した定跡を更新したい場合は、先に `p` または `r` で `peta_book` を読み込み直してください。

`peta_start_sfens.txt` は `n` コマンド実行時に参照されます。
ただし通常運用では、掘りたい範囲を変えるときだけ編集すれば十分です。

![peta_start_sfens.txt で開始局面を変える](assets/peta-start-sfens.svg)

任意の局面の `startpos moves ...` 文字列を得るには、将棋AI用GUIの `将棋所` を使うと簡単です。
詳しくは [3. USI と position コマンド](03-usi.md#将棋所から局面文字列を得る) を参照してください。

複数の開始局面から同時に leaf の先を探したい場合は、同じファイルに複数行を書きます。

```text
startpos moves 7g7f 3c3d 2g2f
startpos moves 2g2f 8c8d 2f2e 8d8e
startpos moves 7g7f 8c8d 2g2f 3c3d
```

この仕組みは、特定の戦型や、既に掘りたいと分かっている局面の周辺だけを広げたいときに使います。
例えば、ある局面以降だけを深く掘りたい場合、その局面を `book/peta_start_sfens.txt` に書いてから `peta_next` を実行します。


## 基本の反復

最初の 1 周は次の流れです。

1. KifManager で棋譜を抽出し、`book/think_sfens.txt` を作る。
2. BookMiner を起動する。
3. `t` で `book/think_sfens.txt` の局面を探索キューへ積む。
4. `p` で定跡 DB を `book/backup/` に書き出し、peta shock 化して読み込む。
5. `n 30` などとして、次に掘る局面を作る。`book/think_sfens.txt`に書き出される。
6. 必要なら 3. に戻って繰り返す。
7. 終了するときは `q` で終了する。このとき、`book/backup/book_miner-タイムスタンプ_局面数.db` が書き出される。

GUI では、初回に KifManager で作った `think_sfens.txt` を `enqueue` したあとは、次の 3 手順を繰り返します。

![BookMiner GUI の基本手順](assets/bookminer-workflow.svg)

```text
手順1. peta_shock
手順2. peta_next
手順3. enqueue
```

`peta_shock` は、いままで掘った通常定跡 DB を peta shock 化して、次に掘る局面を高速に探せる形にします。

`peta_next` は、その peta shock 化された定跡から、leaf の先へ伸ばす局面を探して `book/think_sfens.txt` に書き出します。

`enqueue` は、その `book/think_sfens.txt` を読み、まだ掘っていない局面を探索キューへ積みます。
