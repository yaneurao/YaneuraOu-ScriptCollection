# 8. GUI で操作する

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

GUI は起動直後に、内部的に `BookMiner.py --from_gui` を自動実行します。このオプションが付いている場合、コマンド入力用のプロンプトはログ欄に出力されません。

## 基本操作

GUIを起動すると、BookMiner.py も自動的に起動します。ログ欄に BookMiner.py の出力が表示されます。
起動中は画面上部の状態表示が、現在の処理を表示します。

```text
状態: 定跡DBを読み込み中
状態: エンジン起動中
状態: 自動保存サービス起動完了
状態: コマンド受付を開始しました。
```

`コマンド受付を開始しました。` と表示されるまでは、`peta_shock`、`peta_next`、`enqueue`、`DB手動保存` などの操作ボタンは無効です。
これは、定跡DB読み込み、エンジン起動、自動保存サービス起動が終わる前にコマンドを送らないためです。

`棋譜抽出` を押すと KifManager を `--from_bookminer` 付きで起動します。この場合、KifManager の出力ファイルは BookMiner が読む `book/think_sfens.txt` に自動設定されます。

GUI の `enqueue` は、固定で次のファイルを読みます。

```text
book/think_sfens.txt
```

`enqueue` を押すと、先に `e eval_limit` を送信してから、`t` を BookMiner.py に送信します。

`enqueue` は、`book/think_sfens.txt` の局面を探索キューへ積む操作です。queue は、これから探索する局面を入れておく待ち行列です。queue に積まれた局面は、BookMiner の探索スレッドによって順に処理されます。

次に掘る局面を `book/think_sfens.txt` に用意する方法は2通りあります。

棋譜から新しく掘る場合は、`棋譜抽出` を使います。
既存の定跡DBを peta shock 化して leaf を延長する場合は、`peta_shock`、`peta_next` を使います。

局面を用意できたら、`enqueue` で探索キューへ積みます。

1. `棋譜抽出`、または `peta_shock` → `peta_next`
2. `enqueue`
3. 必要なら `自動enqueue` を有効にする

GUI 上でもこの手順が縦に並んでいます。

![BookMiner GUI の基本手順](assets/bookminer-workflow.svg)

```text
手順0. [ 棋譜抽出   ]  ← 手順1.～2.の代わりに think_sfens.txt を用意する
手順1. [ peta_shock ]
手順2. [ peta_next  ] eval_diff  [ X ] max step [ Y ]
手順3. [ enqueue    ] eval_limit [ Z ]
手順4. 自動enqueue  ☑ queueの残りが [ X ] より少なくなったら、手順1.～3.を自動実行する
手順5. [ DB手動保存 ] 次回自動保存 YYYY/MM/DD HH:MM:SS
```

`棋譜抽出` は KifManager を起動します。棋譜抽出結果として `book/think_sfens.txt` ができるので、この場合は `peta_shock` と `peta_next` を実行せずに `enqueue` へ進みます。

`peta_shock` は `p` コマンドを送信し、現在の定跡 DB の書き出し、peta shock 化、生成された `book/backup/peta_book-....db` の読み込みを一度に行います。

`peta_next` は、`n eval_diff [max_step]` を送信します。例えば `eval_diff` に `30` と入力して実行すると、`n 30` を送信します。`max step` を入力した場合は、`n 30 40` のように第 2 引数も送信します。

`enqueue` は、`e eval_limit` を送信してから `t` を送信します。例えば `eval_limit` に `400` と入力して実行すると、`e 400` を送信してから、`book/think_sfens.txt` の局面を探索キューへ積みます。
`eval_limit` は、到達した定跡木 leaf から出る指し手を延長するかどうかの判定に使います。途中の非 leaf 局面は `eval_limit` では打ち切りません。既存定跡を広く延長する初回は `99999` のように十分大きな値を指定してください。

`自動enqueue` を有効にすると、`enqueue進捗` の残りタスク数を GUI が監視します。
残りタスク数が指定値より少なくなったら、GUI が自動的に `peta_shock`、`peta_next`、`enqueue` をこの順番で実行します。
自動実行中に同じ処理が二重に走らないよう、次の段階へ進むのは BookMiner.py の完了タグを受け取ってからです。

自動enqueueは、探索workerがまだ処理していないqueue残数を目安にします。
探索が完全に完了した数ではありませんが、workerを遊ばせないための補充タイミングとして使います。

## よく使うボタン

- `棋譜抽出`: KifManager を起動します。
- `peta_shock`: 現在の定跡 DB を書き出し、peta shock 化して読み込みます。
- `peta_next`: peta shock 化した定跡から、次に掘る局面を `book/think_sfens.txt` に書き出します。
- `enqueue`: eval limit を設定してから、`book/think_sfens.txt` の棋譜上の局面を探索キューへ積みます。
- `自動enqueue`: queue残数が指定値より少なくなったときに `peta_shock`、`peta_next`、`enqueue` を自動実行します。
- `DB手動保存`: 現在の定跡 DB を `book/backup/` に書き出します。

ボタンにマウスを乗せると、簡単な説明が表示されます。

## GUI設定の保存

GUI の数値入力欄は、ウィンドウを閉じるときに `BookMiner-gui.pickle` へ保存されます。
保存されるのは `eval_diff`、`max step`、`eval_limit`、`自動enqueue` の queue 残数しきい値、ログ表示モードです。

ウィンドウの `×` で閉じる場合、GUI は `q` コマンドを送信しません。
DBを保存したい場合は、閉じる前に `DB手動保存` を押してください。

## ログ

ログ領域は 4 つに分かれています。

- `peta_next/peta_shockログ`: `peta_next` の出力と `peta_shock` の変換ログを表示します。
- `タスク状況ログ`: `enqueue` したタスクの投入状況と進捗を表示します。
- `探索ログ`: 棋譜の局面を掘っているときの局面ログを表示します。
- `その他ログ`: 起動、終了、設定変更、定跡DB書き出しなどのログを表示します。

探索ログには、例えば次のような行が出ます。

```text
[3] sfen ... 42 , 0.7
```

行末の `1.0` や `0.7` は、その局面で使う探索ノード数の倍率です。
MultiPV の数ではありません。
1手前の探索結果を利用できる局面では、探索ノード数を `0.7` 倍にします。
詳しくは [4. 定跡を掘るための基礎](04-basics.md#探索ログの末尾に出る-10-と-07) を参照してください。

`ログ表示` のコンボボックスで、ログ欄の並べ方を選べます。

- `4×1`: 4つのログ欄を縦に並べます。デフォルトです。
- `1×4`: 4つのログ欄を横に並べます。
- `2×2`: 4つのログ欄を2行2列に並べます。
- `タブ化`: 1つのログ欄をタブで切り替えます。

ログ内容は各表示に同時に書き込まれるので、表示を切り替えてもログは失われません。

GUI の各ログ欄は、表示行数が増えすぎないように古い行を自動的に削除します。画面上には直近約1000行が残ります。完全なログは `log/` のログファイルを確認してください。

## 起動と進捗表示

GUI には `定跡読込`、`エンジン起動`、`定跡書込`、`enqueue進捗` の progress bar があります。
その下に、現在の定跡局面数と採掘速度も表示されます。

```text
現在 12,345,678 局面    現在の採掘速度 124,567 局面/日
```

`エンジン起動` は、`settings/engine_settings.json5` の `multi` の合計数に対して、BookMiner が起動処理を投げたエンジン数を表示します。
起動を投げたあと、まだ `readyok` が返っていないエンジンがある場合は、状態表示が `エンジン応答待ち X/Y` になります。

自動保存サービスが起動すると、次回の自動保存予定時刻も表示されます。

```text
次回自動保存 2026/06/08 12:34:56
```

この採掘速度は、探索呼び出し回数ではなく、book に追加された局面数を基準にします。
GUI は BookMiner.py から送られてくる現在局面数を 1 分ごとに記録し、直近 60 分の増加分から 1 日あたりの増加局面数を推定します。
起動直後など、まだ十分なサンプルがない間は速度が `-` と表示されます。

BookMiner.py が次のようなタグ付きログを出力すると、GUI がそれを拾って progress bar を更新します。

```text
[StartupStage] stage=engine_init message=エンジン起動中
[EngineInitProgress] 12/32 ready=10
[EngineReadyProgress] 30/32
[BackupServiceStarted] next=2026/06/08_12:34:56 interval=10800
[CommandReady] message=コマンド受付を開始しました。
[BookReadProgress] 10000/12345678
[BookWriteProgress] 10000/12345678
[MiningProgress] positions=12345678
[PetaCommandDone]
[PetaNextDone] path=book/think_sfens.txt count=50000
```

起動時の `book/backup/` にある最新通常定跡 DB の読み込み、`peta_shock` 後の `book/backup/peta_book-....db` 読み込み、`DB手動保存` の書き出しで進捗が表示されます。

`enqueue進捗` は、BookMiner.py が次のようなタグ付きログを出力すると更新されます。

```text
[TaskQueueStart] 0/50000 job=1 job_progress=0/50000 job_remaining=50000 added=50000 remaining=50000 path=book/think_sfens.txt eval_limit=400
[TaskQueueProgress] 30000/50000 job=1 job_progress=30000/50000 job_remaining=20000 remaining=20000
[TaskQueueDone] 50000/50000 job=1 job_progress=50000/50000 job_remaining=0 remaining=0
```

行頭の `30000/50000` は、BookMiner 起動後に enqueue した累計タスク数に対して、worker が受け取ったタスク数です。探索が完全に完了した数ではありませんが、残りタスク量を把握するための目安になります。

`job_progress=30000/50000` は、そのログ行の `job=1` が投入した対局棋譜だけを見た進捗です。複数回 enqueue して job が混ざっている場合でも、各 job がどれくらい worker に渡ったかを確認できます。

複数回 enqueue した場合、`[TaskQueueStart]` の分母は追加分だけ増えます。例えば 50000 タスク中 30000 タスクが worker に渡った状態で 72462 行を追加 enqueue すると、次のように表示されます。

```text
[TaskQueueStart] 30000/122462 job=4 job_progress=0/72462 job_remaining=72462 added=72462 remaining=92462 path=book/think_sfens.txt eval_limit=400
```

ログは前回出力からおおむね 10 秒以上経過したとき、または最後のタスクを worker が受け取ったときに更新されます。

自動enqueueは、この `remaining` が指定値より少なくなったときに発火します。
手動で `peta_shock`、`peta_next`、`enqueue`、`DB手動保存` を実行している間は、自動enqueueは開始しません。
自動enqueue後も `remaining` がまだ指定値より少ない場合は、足りるまで続けて自動enqueueします。

## 注意点

ウィンドウの `×` で閉じると、BookMiner.py が起動中かどうかに関係なく確認ダイアログが表示されます。
`はい` を選ぶと、GUI は `q` コマンドを送らずに、起動中の BookMiner.py 子プロセスがあれば終了します。
DBを保存したい場合は、閉じる前に `DB手動保存` を押してください。

`peta_shock` は時間がかかることがあります。変換中はログ欄に `[peta_shock] running...` のような進捗が表示されます。
