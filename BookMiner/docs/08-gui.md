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

`コマンド受付を開始しました。` と表示されるまでは、`peta_shock`、`peta_read`、`peta next`、`enqueue`、`DB手動保存` などの操作ボタンは無効です。
これは、定跡DB読み込み、エンジン起動、自動保存サービス起動が終わる前にコマンドを送らないためです。

`棋譜抽出` を押すと KifManager を `--from_bookminer` 付きで起動します。この場合、KifManager の出力ファイルは BookMiner が読む `book/think_sfens.txt` に自動設定されます。

GUI の `enqueue` は、固定で次のファイルを読みます。

```text
book/think_sfens.txt
```

`enqueue` を押すと、引数なしの `e` を BookMiner.py に送信します。探索条件は `book/think_sfens.txt` の各行に付いたメタ情報を使います。

`enqueue` は、`book/think_sfens.txt` の局面を探索キューへ積む操作です。queue は、これから探索する局面を入れておく待ち行列です。queue に積まれた局面は、BookMiner の探索スレッドによって順に処理されます。

次に掘る局面を `book/think_sfens.txt` に用意する方法はいくつかあります。

棋譜から新しく掘る場合は、`棋譜抽出` を使います。
既存の定跡DBを peta shock 化して leaf を延長する場合は、BookMiner 上で変換するなら `peta_shock`、別マシンなどで変換済みの `peta_book-....db` または `.ybb` を持ち込むなら `peta_read` のあとに `peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent` のいずれかを使います。
peta shock 化の意味、`peta next`、`peta refutation`、`peta_depth_gap`、`peta_unsolved`、`peta_opponent` の関係は [10. peta shock 化](10-peta-shock.md) を参照してください。

局面を用意できたら、`enqueue` で探索キューへ積みます。

1. `棋譜抽出`、または `peta_shock` / 外部変換後の `peta_read` → `peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent`
2. `enqueue`
3. 必要なら `自動enqueue` を有効にする

GUI 上でもこの手順が縦に並んでいます。

![BookMiner GUI の基本手順](assets/bookminer-workflow.svg)

```text
手順0. [ 棋譜抽出   ]  ← 手順1.～2.の代わりに think_sfens.txt を用意する
手順1. [ peta_shock ] [ peta_read  ]
手順2. デフォルト値                            eval_diff [ 30 ] max step [ 99999 ] game ply limit [ 200 ] book extend ply [ 6 ] eval_limit [ 400 ]
        [ peta next       ]                    eval_diff [ X  ] max step [ Y     ] game ply limit [ P   ] book extend ply [ T ] eval_limit [ Z ] 自動 [✓]
        [ peta refutation ] eval refu. [ R ]   eval_diff [ X  ] max step [ Y     ] game ply limit [ P   ] book extend ply [ T ] eval_limit [ Z ] 自動 [ ]
        [ peta depth gap  ] eval/ply  [ G ]   eval_diff [ X  ] max step [ Y     ] game ply limit [ P   ] book extend ply [ T ] eval_limit [ Z ] 自動 [ ]
        [ peta unsolved   ] eval_drop_limit [ X ]             max step [ Y     ] game ply limit [ P   ] book extend ply [ T ] eval_limit [ Z ] 自動 [ ]
        [ peta opponent   ]                    eval_diff [ X  ] max step [ Y     ] game ply limit [ P   ] book extend ply [ T ] eval_limit [ Z ] 自動 [ ]
手順3. [ enqueue    ]
手順4. 自動enqueue  ☑ queueの残りが [ X ] より少なくなったら、手順2の自動チェック分をまとめてenqueue
手順5. [ DB手動保存 ] 次回自動保存 YYYY/MM/DD HH:MM:SS
```

`手順2.` の見出しボタンを押すと、手順2の詳細行を折りたたみ/展開できます。
この折りたたみ状態は `BookMiner-gui.pickle` に保存され、次回起動時にも再現されます。

`棋譜抽出` は KifManager を起動します。棋譜抽出結果として `book/think_sfens.txt` ができるので、この場合は `peta_shock`、`peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent` を実行せずに `enqueue` へ進みます。

`peta_shock` は `p` コマンドを送信し、現在の定跡 DB の書き出し、peta shock 化、生成された `book/backup/peta_book-....db` の読み込みを一度に行います。起動時に既存 `.ybb` を読み込んで未変更なら、`.ybb -> .ybb` として変換します。

`peta_read` は `r` コマンドを送信し、`book/backup/` にある最新の `peta_book-....db` または `peta_book-....ybb` を読み込みます。`peta_read` 自体は peta shock 化を行わないため、別マシンや手動の `makebook peta_shock` で先に peta book を作って、このフォルダに置いておく必要があります。

手順2の各行で空欄にした共通項は、`デフォルト値` 行の値を使います。明示的に `None` と入力した場合だけ、CLI へ `None` を送ります。初期値は `eval_diff=30`、`max step=99999`、`game ply limit=200`、`book extend ply=6`、`eval_limit=400` です。
GUI は各 peta 操作と `enqueue` の直前に `sd eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送って、この `デフォルト値` 行を BookMiner.py / BookMinerCpp 側へ反映します。KifManager で作ったような行メタ情報なしの `think_sfens.txt` を `enqueue` した場合も、このデフォルト値行が使われます。

`peta next` は `pn eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。例えばデフォルト値行が初期値のままなら、行側をすべて空欄にして実行すると `pn 30 99999 200 6 400` を送信します。

`peta refutation` は `pr eval_refutation_margin eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。通常の `peta next` で見つかる leaf のうち、定跡から抜ける最後の1手が元DBでは best ではなく、peta shock後の旧best手との差が `eval_refutation_margin` 以上あるものだけを抽出します。`max step` は `peta next` とは別に指定できます。

`peta depth gap` は `pdg eval_per_ply eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。`peta next` と同じ範囲で、best以外の登録済み指し手がbestより浅く、depth差ぶん延長すれば best を逆転しうる場合に、そのPV leafを `book/think_sfens.txt` に書き出します。`eval/ply` は、1手深く掘ったときに評価値がどれくらい改善しうると仮定するかの値です。デフォルトは `0.1` で、`0.5` のような小数も指定できます。

`peta unsolved` は `pu eval_drop_limit max_step game_ply_limit book_extend_ply eval_limit` を送信します。`book/think_unsolved_sfens.txt` にある棋譜の各prefix局面から、peta_book 上の best PV を leaf まで辿った局面を `book/think_sfens.txt` に書き出します。`eval_drop_limit` は棋譜rootの評価値からroot側視点でどれだけ悪化したprefixを除外するかです。負けた棋譜の変化周辺を重点的に掘りたいときに使います。`自動` にチェックすると、自動enqueueの手順2にも含めます。

`peta opponent` は `po eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。`book/book_opponent/` に置いた過去配布定跡などを相手定跡とみなし、現在読み込んでいる peta_book と best 進行を辿ります。どちらかの定跡が切れた地点から、現在の peta_book の PV leaf まで進めた局面を `book/think_sfens.txt` に書き出します。

手順2の各行の `game ply limit`、`book extend ply`、`eval_limit` を数値で指定すると、書き出す各行に `game_ply_limit=...`、`book_extend_ply=...`、`eval_limit=...` が付きます。その行を `enqueue` したときは、この行ごとの値で探索します。

`enqueue` は、`sd ...` でデフォルト値を反映してから、引数なしの `e` を送信します。
`eval_limit` は、定跡木の外へ出る枝を延長するかどうかの判定に使います。途中の局面が定跡木の内部ノードなら `eval_limit` では打ち切りませんが、DB外へ出る指し手の評価値が `eval_limit` を超えていれば、そこで停止します。既存定跡を広く延長する初回は `99999` のように十分大きな値を指定してください。
`game ply limit` は、この手数に到達したらそれ以上掘らない上限です。`peta next` の候補書き出しと、`enqueue` 後の探索workerの両方に使われます。`book extend ply` は、入力棋譜の末端まで到達できたあと、best line を追加で何手分延長するかです。空欄または `None` はデフォルト値の `6` です。

`自動enqueue` を有効にすると、`enqueue進捗` の残りタスク数を GUI が監視します。
残りタスク数が指定値より少なくなったら、GUI が自動的に `peta_shock` を実行し、そのあと手順2で `自動` にチェックされている抽出を上から順に実行します。
各抽出が `book/think_sfens.txt` に書き出した内容は、GUI が `book/think_sfens-tmp.txt` に追記します。重複行はこの追記時に除外します。行末メタ情報が付いている同一局面が複数ある場合は、より大きい値を持つ行を残します。
チェックされた手順2をすべて実行したら、GUI は `book/think_sfens-tmp.txt` を `book/think_sfens.txt` に置き換えてから `enqueue` します。
自動実行中に同じ処理が二重に走らないよう、次の段階へ進むのは BookMiner.py の完了タグを受け取ってからです。

自動enqueueは、探索workerがまだ処理していないqueue残数を目安にします。
探索が完全に完了した数ではありませんが、workerを遊ばせないための補充タイミングとして使います。

## よく使うボタン

- `棋譜抽出`: KifManager を起動します。
- `peta_shock`: 現在の定跡 DB を書き出し、peta shock 化して読み込みます。
- `peta_read`: 外部で peta shock 化して `book/backup/` に置いた最新の `peta_book-....db` または `peta_book-....ybb` を読み込みます。
- `peta next`: peta shock 化した定跡から、次に掘る局面を `book/think_sfens.txt` に書き出します。
- `peta refutation`: `peta next` の leaf のうち、反駁された leaf だけを `book/think_sfens.txt` に書き出します。
- `peta depth gap`: depthが浅く逆転しうる候補手のPV leafを `book/think_sfens.txt` に書き出します。
- `peta unsolved`: `book/think_unsolved_sfens.txt` の棋譜prefixからPV leafを `book/think_sfens.txt` に書き出します。
- `peta opponent`: `book/book_opponent/` の相手定跡と現行 peta_book の best 進行から、対策候補leafを `book/think_sfens.txt` に書き出します。
- `enqueue`: `book/think_sfens.txt` の行メタ情報に従って、棋譜上の局面を探索キューへ積みます。
- `自動enqueue`: queue残数が指定値より少なくなったときに `peta_shock`、手順2で `自動` チェックされた抽出、`enqueue` を自動実行します。
- `DB手動保存`: 現在の定跡 DB を `book/backup/` に書き出します。

ボタンにマウスを乗せると、簡単な説明が表示されます。

## GUI設定の保存

GUI の数値入力欄は、ウィンドウを閉じるときに `BookMiner-gui.pickle` へ保存されます。
保存されるのは手順2のデフォルト値、各 `eval_diff`、`peta unsolved` の `eval_drop_limit`、各 `eval refu.`、各 `max step`、各 `book extend ply`、`eval/ply`、各 `eval_limit`、各 `game ply limit`、手順2の `自動` チェック状態、手順2の折りたたみ状態、`自動enqueue` の queue 残数しきい値、ログ表示モードです。

ウィンドウの `×` で閉じる場合、GUI は `q` コマンドを送信しません。
DBを保存したい場合は、閉じる前に `DB手動保存` を押してください。

## ログ

ログ領域は 4 つに分かれています。

- `コマンドログ`: 起動、終了、設定変更、定跡DB書き出しなどのログを表示します。
- `タスク状況ログ`: `enqueue` したタスクの投入状況と進捗を表示します。
- `探索ログ`: 棋譜の局面を掘っているときの局面ログを表示します。
- `petaログ`: `peta next` / `peta refutation` / `peta depth gap` / `peta unsolved` / `peta opponent` の出力、`peta_shock` の変換ログ、`peta_read` の読み込みログを表示します。

探索ログには、例えば次のような行が出ます。

```text
[3] sfen ... 42 , 0.7
```

行末の `1.0` や `0.7` は、その局面で使う探索ノード数の倍率です。
MultiPV の数ではありません。
1手前の探索結果を利用できる局面では、探索ノード数を `0.7` 倍にします。
詳しくは [4. 定跡を掘るための基礎](04-basics.md#探索ログの末尾に出る-10-と-07) を参照してください。

`ログ表示` のコンボボックスで、ログ欄の並べ方を選べます。

- `4×1`: 4つのログ欄を縦に並べます。
- `1×4`: 4つのログ欄を横に並べます。
- `2×2`: 4つのログ欄を2行2列に並べます。デフォルトです。
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
[PetaReadDone]
[PetaNextDone] path=book/think_sfens.txt count=50000
```

起動時の `book/backup/` にある最新通常定跡 DB の読み込み、`peta_shock` / `peta_read` 後の `book/backup/peta_book-....db` または `peta_book-....ybb` 読み込み、`DB手動保存` の書き出しで進捗が表示されます。

`enqueue進捗` は、BookMiner.py が次のようなタグ付きログを出力すると更新されます。

```text
[TaskQueueStart] 0/50000 job=1 job_progress=0/50000 job_remaining=50000 added=50000 remaining=50000 path=book/think_sfens.txt eval_limit=400 game_ply_limit=200 book_extend_ply=6
[TaskQueueProgress] 30000/50000 job=1 job_progress=30000/50000 job_remaining=20000 remaining=20000 eval_limit=400 game_ply_limit=200 book_extend_ply=6
[TaskQueueJobDone] 50000/50000 job=1 job_progress=50000/50000 job_remaining=0 remaining=0 eval_limit=400 game_ply_limit=200 book_extend_ply=6
[TaskQueueDone] 50000/50000 job=1 job_progress=50000/50000 job_remaining=0 remaining=0 eval_limit=400 game_ply_limit=200 book_extend_ply=6
```

行頭の `30000/50000` は、BookMiner 起動後に enqueue した累計タスク数に対して、完了したタスク数です。

`job_progress=30000/50000` は、そのログ行の `job=1` が投入した対局棋譜だけを見た完了数です。複数回 enqueue して job が混ざっている場合でも、各 job がどれくらい完了したかを確認できます。

`[TaskQueueJobDone]` は、その `job` の全タスクが完了したときに出ます。`remaining` が 0 でなければ、他の job のタスクがまだ残っています。

`タスク状況ログ` の `タスク一覧` チェックを入れると、ログ表示の代わりに現存 job の一覧を表示します。
各行には `job`、`残り` (`job_remaining`)、`母数` (`job_progress` の分母)、`eval_limit`、`game_ply_limit`、`book_extend_ply` が表示されます。
`eval_limit`、`game_ply_limit`、`book_extend_ply` が job 内で複数値に分かれている場合は `mixed` と表示されます。
`[TaskQueueJobDone]` または `job_remaining=0` を受け取った job は一覧から削除されます。

複数回 enqueue した場合、`[TaskQueueStart]` の分母は追加分だけ増えます。例えば 50000 タスク中 30000 タスクが完了した状態で 72462 行を追加 enqueue すると、次のように表示されます。

```text
[TaskQueueStart] 30000/122462 job=4 job_progress=0/72462 job_remaining=72462 added=72462 remaining=92462 path=book/think_sfens.txt eval_limit=400 game_ply_limit=mixed book_extend_ply=mixed
[TaskQueueJobDone] 102462/122462 job=4 job_progress=72462/72462 job_remaining=0 remaining=20000 eval_limit=400 game_ply_limit=mixed book_extend_ply=mixed
```

`[TaskQueueProgress]` は、おおむね 10 秒ごとに、前回出力時から完了数が変わっている job について出力されます。
job の最後のタスクが完了したときは `[TaskQueueJobDone]`、全体 queue の最後のタスクが完了したときは `[TaskQueueDone]` が即時に出ます。

自動enqueueは、この `remaining` が指定値より少なくなったときに発火します。
手動で `peta_shock`、`peta_read`、手順2の各 peta 操作、`enqueue`、`DB手動保存` を実行している間は、自動enqueueは開始しません。
自動enqueue後も `remaining` がまだ指定値より少ない場合は、足りるまで続けて自動enqueueします。

自動enqueueで手順2を複数チェックしている場合、抽出ごとの `book/think_sfens.txt` は直接 enqueue されません。
GUI がそれぞれの結果を `book/think_sfens-tmp.txt` へ集約し、最後に `book/think_sfens.txt` へ置き換えてから enqueue します。

## 注意点

ウィンドウの `×` で閉じると、BookMiner.py が起動中かどうかに関係なく確認ダイアログが表示されます。
`はい` を選ぶと、GUI は `q` コマンドを送らずに、起動中の BookMiner.py 子プロセスがあれば終了します。
DBを保存したい場合は、閉じる前に `DB手動保存` を押してください。

`peta_shock` は時間がかかることがあります。変換中はログ欄に `[peta_shock] running...` のような進捗が表示されます。
