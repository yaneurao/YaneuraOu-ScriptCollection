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

GUI から起動するときは、内部的に `BookMiner.py --from_gui` を実行します。このオプションが付いている場合、コマンド入力用のプロンプトはログ欄に出力されません。

## 基本操作

まず `BookMiner起動` を押します。ログ欄に BookMiner.py の出力が表示されます。

`棋譜抽出` を押すと KifManager を `--from_bookminer` 付きで起動します。この場合、KifManager の出力ファイルは BookMiner が読む `book/think_sfens.txt` に自動設定されます。

GUI の `enqueue` は、固定で次のファイルを読みます。

```text
book/think_sfens.txt
```

`enqueue` を押すと、先に `e eval_limit` を送信してから、`t` を BookMiner.py に送信します。

`enqueue` は、`book/think_sfens.txt` の局面を探索キューへ積む操作です。queue は、これから探索する局面を入れておく待ち行列です。queue に積まれた局面は、BookMiner の探索スレッドによって順に処理されます。

局面を掘り終えたら、次の順番で操作します。

1. `peta_shock`
2. `peta_next`
3. `enqueue`

GUI 上でもこの 3 手順が縦に並んでいます。

```text
手順1. [ peta_shock ]
手順2. [ peta_next  ] eval_diff  [ X ] max step [ Y ]
手順3. [ enqueue    ] eval_limit [ Z ]
```

`peta_shock` は `p` コマンドを送信し、現在の定跡 DB の書き出し、peta shock 化、生成された `book/backup/peta_book-....db` の読み込みを一度に行います。

`peta_next` は、`n eval_diff [max_step]` を送信します。例えば `eval_diff` に `30` と入力して実行すると、`n 30` を送信します。`max step` を入力した場合は、`n 30 40` のように第 2 引数も送信します。

`enqueue` は、`e eval_limit` を送信してから `t` を送信します。例えば `eval_limit` に `400` と入力して実行すると、`e 400` を送信してから、`book/think_sfens.txt` の局面を探索キューへ積みます。

## よく使うボタン

- `BookMiner起動`: BookMiner.py を起動します。
- `BookMiner終了`: `q` を送信し、`book/backup/` に通常定跡 DB を書き出して終了します。
- `棋譜抽出`: KifManager を起動します。
- `peta_shock`: 現在の定跡 DB を書き出し、peta shock 化して読み込みます。
- `peta_next`: peta shock 化した定跡から、次に掘る局面を `book/think_sfens.txt` に書き出します。
- `enqueue`: eval limit を設定してから、`book/think_sfens.txt` の棋譜上の局面を探索キューへ積みます。
- `定跡DBのbackup`: 現在の定跡 DB を `book/backup/` に書き出します。

ボタンにマウスを乗せると、簡単な説明が表示されます。

## ログ

ログ領域は 4 つに分かれています。

- `peta_next/peta_shockログ`: `peta_next` の出力と `peta_shock` の変換ログを表示します。
- `タスク状況ログ`: `enqueue` したタスクの投入状況と進捗を表示します。
- `探索ログ`: 棋譜の局面を掘っているときの局面ログを表示します。
- `その他ログ`: 起動、終了、設定変更、定跡DB書き出しなどのログを表示します。

GUI の各ログ欄は、表示行数が増えすぎないように古い行を自動的に削除します。画面上には直近約1000行が残ります。完全なログは `log/` のログファイルを確認してください。

## 定跡DBの読み書き進捗

GUI には `定跡読込`、`定跡書込`、`enqueue進捗` の progress bar があります。

BookMiner.py が次のようなタグ付きログを出力すると、GUI がそれを拾って progress bar を更新します。

```text
[BookReadProgress] 10000/12345678
[BookWriteProgress] 10000/12345678
```

起動時の `book/backup/` にある最新通常定跡 DB の読み込み、`peta_shock` 後の `book/backup/peta_book-....db` 読み込み、`BookMiner終了` や `定跡DBのbackup` の書き出しで進捗が表示されます。

`enqueue進捗` は、BookMiner.py が次のようなタグ付きログを出力すると更新されます。

```text
[TaskQueueStart] 0/50000 job=1 added=50000 remaining=50000 path=book/think_sfens.txt eval_limit=400
[TaskQueueProgress] 30000/50000 job=1 remaining=20000
[TaskQueueDone] 50000/50000 job=1 remaining=0
```

この数値は、BookMiner 起動後に enqueue した累計タスク数に対して、worker が受け取ったタスク数です。探索が完全に完了した数ではありませんが、残りタスク量を把握するための目安になります。

複数回 enqueue した場合、`[TaskQueueStart]` の分母は追加分だけ増えます。例えば 50000 タスク中 30000 タスクが worker に渡った状態で 72462 行を追加 enqueue すると、次のように表示されます。

```text
[TaskQueueStart] 30000/122462 job=4 added=72462 remaining=92462 path=book/think_sfens.txt eval_limit=400
```

ログは前回出力からおおむね 10 秒以上経過したとき、または最後のタスクを worker が受け取ったときに更新されます。

## 注意点

通常は `BookMiner終了` を押して保存終了してください。ウィンドウの `×` で閉じると、BookMiner.py の子プロセスを終了して GUI も閉じます。

`peta_shock` は時間がかかることがあります。変換中はログ欄に `[peta_shock] running...` のような進捗が表示されます。
