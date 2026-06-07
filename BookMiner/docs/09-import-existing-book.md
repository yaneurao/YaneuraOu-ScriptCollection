# 9. 既存のやねうら王定跡から掘り始める

この章では、既存のやねうら王標準定跡ファイルを BookMiner に読み込ませ、その定跡の leaf 局面から先を掘り足す手順を説明します。

## 目的

既存定跡を BookMiner に読み込ませると、その定跡ツリーを出発点として使えます。

このときにやりたいことは、既存定跡の末端、つまり leaf 局面を列挙し、その leaf から先を探索して、定跡ツリーを伸ばすことです。

探索後に再度 peta shock 化すると、既存定跡内の局面評価も、新しく探索した leaf 側の評価値をもとに計算し直されます。

## 既存定跡を配置する

BookMiner.py を終了してから、既存のやねうら王標準定跡ファイルを次の名前で配置します。

```text
book/backup/book_miner.db
```

例えば、既存定跡が `user_book1.db` なら、そのファイルを `book_miner.db` にリネームして、BookMiner フォルダ内の `book/backup/` に置きます。

```text
book/backup/book_miner.db
```

このファイルは、通常バックアップがまだ無い場合の読み込み入口です。

注意点:

- `book/backup/book_miner-YYYYMMDDHHMMSS_N.db` が存在する場合、BookMiner はそちらの最新ファイルを優先して読み込みます。
- 既存定跡から開始したい場合は、`book/backup/` に既存の `book_miner-*.db` が無い状態にしてください。
- `_plyN` 付きのファイルは部分書き出しなので、起動時の自動読み込み対象にはなりません。

## BookMiner を起動する

CLI なら次のように起動します。

```bash
python3 BookMiner.py
```

GUI なら次のように起動します。

```bash
python3 BookMiner-gui.py
```

起動時に `book/backup/book_miner.db` が読み込まれます。

## 手順1. peta_shock 化する

既存定跡を読み込んだら、まず peta shock 化します。

CLI:

```text
p
```

GUI:

```text
手順1. peta_shock
```

`p` コマンドは、現在メモリ上にある定跡を `book/backup/` に正規の名前で書き出し、そのファイルを peta shock 化して読み込みます。

出力例:

```text
book/backup/book_miner-20260607103251_14505901.db
book/backup/peta_book-20260607103251_14505901.db
```

この時点で、既存定跡は BookMiner の通常バックアップ形式に乗ります。

## 手順2. peta_next で leaf を列挙する

次に、peta shock 化した定跡から leaf 局面を列挙します。

既存定跡全体の leaf を広く取りたい場合は、`eval_diff` に大きな値を指定します。

CLI:

```text
n 99999
```

GUI:

```text
手順2. peta_next  eval_diff 99999
```

`99999` は、評価値差による枝刈りを実質的に無効化するための値です。これにより、既存定跡内で辿れる枝を広く辿り、末端の局面を `book/think_sfens.txt` に書き出します。

出力先:

```text
book/think_sfens.txt
```

ただし、`settings/book_miner_settings.json` の `max_book_ply` に到達する局面は、次に掘る局面としては書き出されません。必要なら `max_book_ply` を調整してください。

## 手順3. enqueue する

`peta_next` が書き出した `book/think_sfens.txt` を探索キューへ積みます。

CLI:

```text
e 99999
t
```

GUI:

```text
手順3. enqueue  eval_limit 99999
```

`eval_limit` も大きな値にしておくと、評価値が大きく傾いた局面でも途中で打ち切られにくくなります。

`enqueue` は `book/think_sfens.txt` に書かれた各行を読み、まだ掘っていない局面を探索キューへ積みます。探索キューに積まれた局面は、探索 worker によって順に処理されます。

## 探索後にもう一度 peta_shock 化する

enqueue したタスクが処理されたら、もう一度 peta shock 化します。

CLI:

```text
p
```

GUI:

```text
手順1. peta_shock
```

これにより、新しく探索された leaf の評価値をもとに、peta shock 化された定跡が作り直されます。

このあとさらに広げたい場合は、次の手順を繰り返します。

```text
手順1. peta_shock
手順2. peta_next  eval_diff 99999
手順3. enqueue    eval_limit 99999
```

通常運用では、既存定跡から初回 leaf を掘り足したあと、`eval_diff` や `eval_limit` を目的に応じて小さくしていきます。
