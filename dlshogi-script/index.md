# dlshogi-script

dlshogi 用の教師データ作成・整理に使う補助スクリプトを置くフォルダ。

## スクリプト一覧

| スクリプト | 内容 |
|---|---|
| `filter_hcpe_by_eval.py` | HCPEファイルから、評価値の絶対値が指定閾値以上の局面を取り除く。既定では `abs(eval) >= 25000` のrecordを削除する。 |
| `dlshogi-trainer.py` | dlshogi の `train.py` を教師ファイル単位で順に呼び出す学習補助スクリプト。中断再開、round継続、SWA、AMP、CosineAnnealingLRの指定をまとめて扱う。 |
| `extract_train_log.py` | `dlshogi-trainer.py` / `train.py` が出力した `train-*.log` から accuracy、loss、entropy、SWA accuracy などを抽出してCSVにする。 |

## filter_hcpe_by_eval.py

HCPEは1局面38byteの固定長recordで、評価値はoffset 32に little-endian signed int16 として保存されている。このスクリプトはHCPEをrecord単位で読み、評価値が大きすぎる局面を除外して別ファイルへ書き出す。

基本形:

```bash
python filter_hcpe_by_eval.py input.hcpe output.hcpe
```

フォルダ内のファイルを一括処理する場合:

```bash
python filter_hcpe_by_eval.py -source hcpe/ -dest hcpe-filtered-by-eval/
```

出力ファイルを省略した場合は、入力ファイル名に `.filtered` を付ける。

```bash
python filter_hcpe_by_eval.py input.hcpe
```

閾値を変更する場合:

```bash
python filter_hcpe_by_eval.py input.hcpe output.hcpe --threshold 30000
```

主なoption:

| option | 既定値 | 内容 |
|---|---:|---|
| `--threshold` | `25000` | `abs(eval) >= threshold` のrecordを削除する。 |
| `--chunk-records` | `1000000` | 一度に読み込むHCPE record数。大きいHCPEを丸読みしないための処理単位。 |
| `-source`, `--source` | なし | 一括処理する入力フォルダ。直下の通常ファイルを処理する。 |
| `-dest`, `--dest` | なし | 一括処理の出力フォルダ。入力ファイルと同じ相対pathで出力する。 |
| `--recursive` | false | `-source` 配下のサブフォルダも処理する。 |

注意点:

- 入力と出力は別ファイルにする。
- 一括処理では `-source` と `-dest` を必ずセットで指定する。
- 入力ファイルサイズが38で割り切れない場合は、HCPEではない、または壊れたファイルとしてエラーにする。
- HCPE3ではなく、従来のHCPE形式を対象にする。

## dlshogi-trainer.py

dlshogi の `train.py` を、教師データフォルダ内の `.hcpe` / `.hcpe3` ごとに1回ずつ呼び出すための補助スクリプト。大きい教師データを1ファイルずつ学習し、checkpoint と log を連番で出力する。

基本形:

```bat
cd C:\shogi\learner
python dlshogi-trainer.py
```

既定では以下を使う。

| 項目 | 既定値 |
|---|---|
| dlshogi本体 | `C:\shogi\DeepLearningShogi` |
| 教師データ | `C:\shogi\teacher\yane-distill` |
| test data | `C:\shogi\teacher\test\test20231010_fg2021_dls5_ryfc20_ev8250k825.hcpe` |
| model root | `C:\shogi\model` |
| network | `exp___i20x256` |
| batchsize | `1024` |
| lr | `0.03` |
| scheduler | `CosineAnnealingLR(T_max=<教師ファイル数>, eta_min=1e-5)` |
| AMP | ON。既定dtypeは `bfloat16`。 |
| SWA | ON。 |
| use_average | ON。`train.py` に `--use_average` を渡す。 |

出力先は、`--out_dir` を指定しない場合 `--model_root` と `--network` から自動で決まる。既定では次のフォルダになる。

```text
C:\shogi\model\exp___i20x256
```

学習対象のnetworkを変える場合:

```bat
python dlshogi-trainer.py --network exp_i_a40x512_swish
```

教師データ、test data、出力rootを変える場合:

```bat
python dlshogi-trainer.py ^
  --train_dir C:\shogi\teacher\yane-distill ^
  --test_data C:\shogi\teacher\test\test.hcpe ^
  --model_root C:\shogi\model ^
  --network exp_i_a40x512_swish
```

明示的に出力先を指定する場合:

```bat
python dlshogi-trainer.py ^
  --network exp_i_a40x512_swish ^
  --out_dir C:\shogi\model\exp_i_a40x512_swish-test1
```

### 中断再開とround継続

同じコマンドを再実行すると、既存checkpointを見て自動で再開する。

- 最新roundが途中なら、次の未完了教師ファイルから再開する。
- 最新roundが完了済みなら、`_round2`, `_round3` のような次roundのフォルダを作り、前roundの最終checkpointから開始する。
- 次round開始時はoptimizerとschedulerを自動でresetし、lrを `0.03` から再スタートする。

特定checkpointから次roundを手動開始する場合:

```bat
python dlshogi-trainer.py ^
  --resume_checkpoint C:\shogi\model\exp_i_a40x512_swish\checkpoint-0021.pth ^
  --reset_optimizer ^
  --reset_scheduler
```

特定の教師ファイル番号から始める場合:

```bat
python dlshogi-trainer.py --start_index 12
```

`--start_index` は、ソート済み教師ファイル一覧の何番目から処理を始めるかを指定する。番号は1始まりである。`--start_index 12` なら、1から11番目の教師ファイルを単にスキップし、12番目から処理する。

重要な挙動:

- `--start_index` は「教師ファイルの開始位置」を変えるだけで、checkpoint番号の付け方は変えない。
- `train-0012.log` のようなlog名は、常に教師ファイル番号から決まる。
- checkpointの完了判定と直前checkpoint探索は、`checkpoint_offset + 教師ファイル番号` で行う。
- 1周目で12番目の教師ファイルを学習する場合、直前の `checkpoint-0011.pth` が出力先に存在すれば、そこから自動でresumeし、通常 `checkpoint-0012.pth` が出力される。
- 2周目以降では `checkpoint_offset` が付く。例えば1周21ファイルなら、2周目の12番目は `checkpoint-0033.pth`、直前は `checkpoint-0032.pth` になる。
- 直前checkpointが存在しない状態で `--start_index` だけを指定しても、`train.py` のepoch番号は自動では進まない。この場合、log名は `train-0012.log` でも、checkpointは `checkpoint-0012.pth` にならない。
- 直前checkpointがなく、かつ `--resume_checkpoint` または自動判定された初期checkpointがある場合は、`start_index` 番目の最初の学習だけそのcheckpointからresumeする。ただし、出力checkpoint番号は「resumeしたcheckpoint番号 + 1」になる。これは通常 `--start_index 1` のround開始用の挙動であり、`--start_index 12` のような途中番号へepochを飛ばす用途ではない。
- 直前checkpointも初期checkpointもない場合は、`start_index` 番目の教師ファイルから新規学習を開始してしまう。この場合、log番号とcheckpoint番号が対応しなくなるため、通常は使わない。

途中で `train-0012.log` の処理中に止まった場合は、通常は `--start_index` を指定せず同じコマンドを再実行すればよい。既に存在するcheckpointは `already done` としてスキップされ、次の未完了教師ファイルへ進む。

`--start_index` は、出力先に直前checkpointが存在している状態で、そこより前の教師ファイル確認を省略したい場合に使う。教師ファイル一覧の前半を意図的に使わずに新規学習を始める用途には向かない。

### 主なoption

| option | 内容 |
|---|---|
| `--dlshogi_dir` | `DeepLearningShogi` の場所。 |
| `--train_dir` | `.hcpe` / `.hcpe3` 教師ファイルを置いたフォルダ。 |
| `--test_data` | `train.py` に渡すtest用HCPE。 |
| `--model_root` | 自動命名時のmodel出力root。 |
| `--out_dir` | model/checkpoint/logの出力先を明示指定する。 |
| `--network` | `train.py --network` に渡すnetwork名。 |
| `--batchsize` | 学習/テストのbatch size。 |
| `--gpu` | GPU ID。 |
| `--lr` | 初期learning rate。 |
| `--eta_min` | `CosineAnnealingLR` の最小learning rate。 |
| `--val_lambda` | `train.py --val_lambda` に渡す値。 |
| `--amp_dtype` | `bfloat16` または `float16`。既定は `bfloat16`。 |
| `--no_amp` | AMPを使わない。 |
| `--no_average` | `train.py --use_average` を渡さない。 |
| `--no_swa` | SWAを使わない。 |
| `--swa_freq`, `--swa_n_avr`, `--swa_start_epoch` | `train.py` のSWA関連option。 |
| `--start_index` | ソート済み教師ファイル一覧の何番目から処理するか。1始まり。checkpoint番号は変えない。 |
| `--resume_checkpoint` | 最初の教師ファイルの前に読み込むcheckpoint。 |
| `--reset_optimizer` | `--resume_checkpoint` 使用時にoptimizerをresetする。 |
| `--reset_scheduler` | `--resume_checkpoint` 使用時にschedulerをresetする。 |

注意点:

- `train.py` は教師1ファイルごとに呼ばれる。
- 各呼び出しのlogは `train-0001.log`, `train-0002.log` のように出力される。
- 最後の教師ファイルの呼び出しでだけ `--model` を渡し、最終modelを書き出す。
- SWAは既定でONだが、`train.py` のSWA BatchNorm更新は最後の教師ファイルだけで行われる。SWA平均重み自体はcheckpointに保存され、途中再開できる。

## extract_train_log.py

`train-*.log` から学習結果をCSVへ抽出する。`dlshogi-trainer.py` の出力フォルダを指定すると、そのフォルダ内の `train-*.log` を読み、同じ親フォルダにある `_round2`, `_round3` なども自動で読む。

基本形:

```bat
cd C:\shogi\learner
python extract_train_log.py C:\shogi\model\exp_i_a40x512_swish
```

出力先を省略した場合、カレントディレクトリに `<model-name>.csv` を出力する。

```text
exp_i_a40x512_swish.csv
```

出力先を指定する場合:

```bat
python extract_train_log.py C:\shogi\model\exp_i_a40x512_swish ^
  --output C:\shogi\model\exp_i_a40x512_swish\summary.csv
```

複数のlogファイルやフォルダを指定することもできる。

```bat
python extract_train_log.py ^
  C:\shogi\model\exp_i_a40x512_swish ^
  C:\shogi\model\exp_i_b40x512_swish ^
  --output C:\shogi\model\compare.csv
```

教師ファイルpathから共通rootを取り除きたい場合:

```bat
python extract_train_log.py C:\shogi\model\exp_i_a40x512_swish ^
  --teacher_root C:\shogi\teacher
```

CSVに含まれる主な列:

| 列 | 内容 |
|---|---|
| `epoch` | `train.py` が出力したepoch番号。 |
| `swa_test_accuracy` | SWA modelのpolicy accuracy。SWA行がないlogでは `nan`。 |
| `swa_test_value_accuracy` | SWA modelのvalue accuracy。 |
| `test_accuracy` | 通常modelのpolicy accuracy。 |
| `test_value_accuracy` | 通常modelのvalue accuracy。 |
| `train_loss_policy`, `train_loss_result`, `train_loss_value`, `train_loss_total` | train loss。 |
| `test_loss_policy`, `test_loss_result`, `test_loss_value`, `test_loss_total` | test loss。 |
| `test_entropy_policy`, `test_entropy_value` | test entropy。 |
| `position_num` | 教師ファイルから読んだ局面数。 |
| `lr` | logから抽出したlearning rate。 |
| `val_lambda` | `val_lambda`。 |
| `batchsize` | batch size。 |
| `teacher` | 学習に使った教師ファイルpath。 |

注意点:

- `train.py` が `--model` を指定された呼び出しでだけSWA test accuracyを出すため、SWA列は最終教師ファイル以外では `nan` になりやすい。
- `dlshogi-trainer.py` でroundを分けている場合も、baseフォルダを指定すれば `_round2`, `_round3` を自動で読む。
