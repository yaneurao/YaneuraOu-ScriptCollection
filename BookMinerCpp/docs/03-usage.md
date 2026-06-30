# 3. 使い方

BookMinerCpp の操作手順は Python 版 BookMiner と同じです。
この章では、C++版を使うときに意識する差分だけを説明します。

## まず読むべき Python 版の章

次の章は C++版でもそのまま有効です。

- [定跡を掘るための基礎](../../BookMiner/docs/04-basics.md)
- [BookMiner.py の主要コマンド](../../BookMiner/docs/05-commands.md)
- [GUI で操作する](../../BookMiner/docs/08-gui.md)
- [既存のやねうら王定跡から掘り始める](../../BookMiner/docs/09-import-existing-book.md)

BookMinerCpp 独自のコマンド体系は作っていません。
`p`、`n`、`t`、`e`、`w`、`q` の意味は Python 版と同じです。

## GUI から使う

通常は GUI から使うのが楽です。
Python 版 BookMiner のフォルダで、`--cpp` を付けて起動します。

```bash
cd YaneuraOu-ScriptCollection/BookMiner
python3 BookMiner-gui.py --cpp
```

この場合、GUI は C++版を子プロセスとして起動します。

```text
../BookMinerCpp/BookMinerCpp.exe --from_gui
```

GUI 上の操作は Python 版と同じです。

```text
手順0. 棋譜抽出
手順1. peta_shock / peta_read
手順2. peta_next / peta next refu. / peta refutation / peta depth_gap
手順3. enqueue
手順4. 自動enqueue
手順5. DB手動保存
```

`棋譜抽出` は C++ ではなく、既存の Python 版 KifManager を起動します。
BookMinerCpp は KifManager を直接実装しません。

## CLI から使う

CLI として使う場合は、BookMinerCpp フォルダで起動します。

```bash
cd YaneuraOu-ScriptCollection/BookMinerCpp
./BookMinerCpp.exe
```

主なコマンドは Python 版と同じです。

```text
p                 現在DBを書き出し、peta_shock 化して読み込む
r                 最新の peta_book を読み込む
n 100             peta_next を実行し、think_sfens.txt を作る
nf 100 9999 200 100
                  peta_next の leaf のうち、反駁された leaf だけを作る
e 400             eval_limit を 400 にする
t                 book/think_sfens.txt を探索キューへ積む
w                 現在DBを手動保存する
q                 保存して終了する
!                 保存せず終了する
```

## 起動時に読む定跡DB

BookMinerCpp は起動時に `book/backup/` から通常DBを読みます。
候補は次です。

```text
book/backup/book_miner-YYYYMMDDHHMMSS_N.db
book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb
```

この2種類を同じ候補として扱い、ファイル名のタイムスタンプが新しいものを読みます。
通常の命名規則では、ファイル名の辞書順がタイムスタンプ順になります。

`book/backup/book_miner.db` は、タイムスタンプ付きバックアップが1つもない場合だけ読む移行用 fallback です。

`_ply100` のような `_plyN` 付きの部分書き出しは、起動時の自動読み込み対象にしません。

## 保存形式

BookMinerCpp の通常保存は `.ybb` です。

```text
book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb
```

`N` は保存対象の局面数です。
保存は一度 `*.tmp` に書き出してから正式名へ置き換えます。
そのため、書き出し途中で落ちても完成済みのファイルを壊しにくい作りです。

## peta shock 化

`p` コマンドは、BookMinerCpp の `.ybb` バックアップを `YO-MATERIAL.exe` に渡します。起動時に読み込んだ通常DB、または最後に `w` で書き出した通常DBからメモリ内容が変わっていなければ、通常DBを再書き出しせず、その既存ファイルを再利用します。

```text
makebook peta_shock backup/book_miner-....ybb backup/peta_book-....ybb.tmp
```

peta shock 化の出力形式は入力形式に合わせます。BookMinerCpp の通常バックアップは `.ybb` なので、通常は `.ybb` が出力されます。

```text
book/backup/peta_book-YYYYMMDDHHMMSS_N.ybb
```

`N` は変換元の通常DBの局面数です。

`r` コマンドは peta shock 化を実行せず、`book/backup/` にある最新の `peta_book-....db` または `peta_book-....ybb`、または指定した peta book を読み込みます。別マシンで peta shock 化した定跡を使う場合は、その peta book を `book/backup/` に置いてから `r` を実行します。

## 既存定跡を持ち込む

既存のやねうら王標準定跡 `.db` から始める手順は Python 版と同じです。

```text
book/backup/book_miner.db
```

に置いてから BookMinerCpp を起動してください。
タイムスタンプ付きの `book_miner-*.db` や `book_miner-*.ybb` がある場合は、そちらが優先されます。

起動後に `p` または `w` で保存すると、以後は `.ybb` の通常バックアップに乗ります。
