# 3. 定跡を掘るための基礎

この章では、BookMiner に渡す入力ファイル、局面を掘る流れ、次に掘る局面の作り方を説明します。用語は [1. 用語説明](01-terms.md) で説明しています。

## `startpos moves ...` 形式

BookMiner の入力ファイルでは、1 行を 1 対局として書きます。

```text
startpos moves 7g7f 3c3d 2g2f 8c8d
startpos moves 2g2f 8c8d 2f2e 8d8e
```

この形式は、平手初期局面から指し手を順に進めた局面列を表します。

`sfen ... moves ...` 形式も、USI の `position` コマンドとして解釈できる形なら扱えます。ただし、通常の運用では `startpos moves ...` を使います。

## 探索キューと enqueue

BookMiner は、入力ファイルに書かれている局面を読んだ瞬間に、すべてをその場で最後まで探索するわけではありません。

BookMiner の内部には「これから探索する局面」を積んでおく場所があります。この場所を queue と呼びます。queue は待ち行列という意味で、先に積まれたタスクから順に、探索用エンジンへ渡されて処理されます。

`enqueue` は、その queue にタスクを追加する操作です。

BookMiner のコマンドラインでは `t` コマンドが enqueue に相当します。GUI では `enqueue` ボタンを押すと、内部的には `t` コマンドが送られます。

つまり、GUI の `enqueue` は「いますぐ全局面を同期的に掘り終える」ボタンではなく、次のファイルに書かれている局面を読み、まだ掘っていない局面を探索キューへ積むボタンです。

```text
book/think_sfens.txt
```

queue に積まれた局面は、BookMiner が起動している探索スレッドによって順に処理されます。処理の進捗は画面と `log/` のログに出力されます。

GUI の `enqueue進捗` は、`think_sfens.txt` の総行数に対して、worker が受け取ったタスク数を表示します。

```text
enqueue進捗 30000/50000
```

これは「50000 行のうち 30000 行が worker に渡った」という意味です。最後に渡った数件はまだ探索中かもしれませんが、残りタスク量を把握する目的では十分です。

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

途中で評価値の絶対値が eval limit 以上になった場合、その対局の処理はそこで止まります。

また、`settings/book_miner_settings.json` の `max_book_ply` に到達した場合も、その対局の処理はそこで止まります。

棋譜の末端まで到達した場合は、そこからエンジンの best line を `THINK_COMMAND_PLY` 手分だけ延長して掘ります。この延長中も、評価値の絶対値が eval limit 以上になったら停止します。

## 通常定跡 DB を書き出して peta shock 化する

局面を掘ったら、通常は `p` コマンドを使います。

```text
p
```

`p` は、現在の定跡 DB を通常のやねうら王定跡形式として `book/backup/` に書き出し、その書き出したファイルを peta shock 化して `book/peta_book.db` として読み込みます。

バックアップの出力先は `book/backup/` です。

```text
book/backup/book_miner-20260607071000_12345.db
```

`w` コマンドで書き出しだけを行い、`r` コマンドで peta shock 化だけを行うこともできます。ただし、通常の周回作業では `p` を使うほうが安全です。`p` は、自分で書き出したバックアップファイルをそのまま変換元に使うため、`w` の完了確認漏れや、定期自動バックアップとの取り違えを避けやすくなります。

## peta shock 化の内部処理

`p` または `r` コマンドは、通常定跡 DB を peta shock 化します。

```text
p
```

`p` は、いま書き出した通常バックアップを `YO-MATERIAL.exe` に渡し、`book/peta_book.db` を作って読み込みます。

`r` は、path を省略した場合、`book/backup/` にある最新の通常バックアップを `YO-MATERIAL.exe` に渡します。

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
makebook peta_shock backup/book_miner-20260607071000_12345.db peta_book.tmp.db
quit
```

変換に成功すると、BookMiner は `book/peta_book.tmp.db` を `book/peta_book.db` に置き換えます。

## 次に掘る局面を求める

peta shock 化した定跡から、次に掘るべき leaf 局面を求めるには `n` コマンドを使います。

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

`book/think_sfens.txt` は、先手用と後手用の leaf 局面を交互に混ぜたものです。

`settings/book_miner_settings.json` の `max_book_ply` に到達する局面は、次に掘る局面としては書き出されません。

`n`コマンドを使ったときに、`think_sfens.txt`に何局面を書き出したのかが表示されます。それを見て、これを掘るかどうかを決めます。

掘ることにするなら、`t` コマンドでこのファイルを読み込まれてタスクに積みます。(そのあとタスクが消化されて徐々に掘られていきます。)

[YaneuraOu-ScriptCollection/PetaNext](../../PetaNext/README.md)スクリプトを使う運用も考えられますが、BookMiner では `n` コマンドがその役割を持っています。


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

```text
手順1. peta_shock
手順2. peta_next
手順3. enqueue
```

`peta_shock` は、いままで掘った通常定跡 DB を peta shock 化して、次に掘る局面を高速に探せる形にします。

`peta_next` は、その peta shock 化された定跡から、次に掘る leaf 局面を探して `book/think_sfens.txt` に書き出します。

`enqueue` は、その `book/think_sfens.txt` を読み、まだ掘っていない局面を探索キューへ積みます。
