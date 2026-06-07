# 7. GUI で操作する

BookMiner はコマンド入力で操作できますが、`BookMiner-gui.py` を使うと、主要コマンドをボタンから実行できます。

## 起動

BookMiner フォルダで次のコマンドを実行します。

```bash
python3 BookMiner-gui.py
```

Windows で `py` ランチャーを使っている場合は、次のように起動できます。

```bat
py BookMiner-gui.py
```

GUI は `BookMiner.py` を子プロセスとして起動します。BookMiner.py の内部処理を別実装しているわけではないので、コマンドライン版と同じ定跡 DB、同じ設定ファイル、同じログを使います。

## 基本操作

まず `BookMiner起動` を押します。ログ欄に BookMiner.py の出力が表示されます。

`棋譜抽出` を押すと KifManager を `--from_bookminer` 付きで起動します。この場合、KifManager の出力ファイルは BookMiner が読む `book/think_sfens.txt` に自動設定されます。

GUI の `think_sfens.txtを掘る` は、固定で次のファイルを読みます。

```text
book/think_sfens.txt
```

`think_sfens.txtを掘る` を押すと、先に `e eval_limit` を送信してから、`t` を BookMiner.py に送信します。

局面を掘り終えたら、次の順番で操作します。

1. `peta_shock化`
2. `peta_next`
3. `think_sfens.txtを掘る`

`peta_shock化` は `p` コマンドを送信し、現在の定跡 DB の書き出し、peta shock 化、`book/peta_book.db` の読み込みを一度に行います。

`peta_next` は、`n eval_diff [max_step]` を送信します。例えば `eval_diff` に `30` と入力して実行すると、`n 30` を送信します。`max step` を入力した場合は、`n 30 40` のように第 2 引数も送信します。

## よく使うボタン

- `BookMiner起動`: BookMiner.py を起動します。
- `BookMiner終了`: `q` を送信し、`book/book_miner.db` に保存して終了します。
- `棋譜抽出`: KifManager を起動します。
- `peta_shock化`: 現在の定跡 DB を書き出し、peta shock 化して読み込みます。
- `peta_next`: peta shock 化した定跡から、次に掘る局面を `book/think_sfens.txt` に書き出します。
- `think_sfens.txtを掘る`: eval limit を設定してから、`book/think_sfens.txt` の棋譜上の局面を投入します。
- `定跡DBのbackup`: 現在の定跡 DB を `book/backup/` に書き出します。

ボタンにマウスを乗せると、簡単な説明が表示されます。

## ログ

ログ領域は 3 つに分かれています。

- `peta_next/peta_shock化ログ`: `peta_next` の出力と `peta_shock化` の変換ログを表示します。
- `探索ログ`: 棋譜の局面を掘っているときの局面ログを表示します。
- `その他ログ`: 起動、終了、設定変更、定跡DB書き出しなどのログを表示します。

## 注意点

通常は `BookMiner終了` を押して保存終了してください。ウィンドウの `×` で閉じると、BookMiner.py の子プロセスを終了して GUI も閉じます。

`peta_shock化` は時間がかかることがあります。変換中はログ欄に `[peta_shock] running...` のような進捗が表示されます。
