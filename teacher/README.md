# teacher

やねうら王系・dlshogi系の教師データを加工するためのスクリプト群です。

このREADMEでは、スクリプト名ではなく「何をしたいか」を軸に説明します。

コマンド例は `YaneuraOu-ScriptCollection` のrootディレクトリから実行する前提です。たとえば `teacher/convert_teacher.py` は、このリポジトリ内の `YaneuraOu-ScriptCollection/teacher/convert_teacher.py` を指します。`GenSfen/` は同じリポジトリ内の教師生成用フォルダです。

一部のre-evalでは、dlshogi本家リポジトリをcloneした `DeepLearningShogi` のスクリプトを使います。このREADMEでは、`YaneuraOu-ScriptCollection` と同じ親フォルダに以下のようにcloneされている前提で `../DeepLearningShogi/...` と書きます。

```bash
git clone https://github.com/TadaoYamaoka/DeepLearningShogi.git
```

## 教師データのシャッフル

学習前には、教師局面をできるだけシャッフルしておくことを推奨します。

自己対局で生成した教師は、1局内の連続局面同士の自己相関が強く、駒配置や進行度も局所的に偏ります。対局順のまま学習に流すと、ミニバッチごとの分布が偏りやすく、lossの周期的な揺れや学習効率の低下につながります。

形式ごとの扱い:

| 形式 | シャッフル方法 |
|---|---|
| `psv` | `teacher/split_teacher.py --shuffle` を使う。40 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `hcpe` | `teacher/split_teacher.py --shuffle` を使う。38 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `pack` | 棋譜形式なので、ファイル上のレコードを単純に局面単位シャッフルする用途には向かない。 |
| `hcpe3` | 棋譜単位の可変長形式なので、単純な局面単位シャッフルには向かない。 |

### HCPE/PSVフォルダをオフメモリでシャッフルして分割する

巨大なHCPE/PSV教師フォルダを学習前に混ぜ直したい場合は `teacher/shuffle_split_teacher_external.py` を使う。
入力フォルダ内の `.hcpe` または `.psv` をすべて読み、局面を表す 32 byteから計算したbucketへ一時分配し、bucketごとにシャッフルして出力フォルダへ分割する。
入力全体を一度にメモリへ載せないので、`split_teacher.py --shuffle` より大きな教師データを扱いやすい。

```bash
python teacher/shuffle_split_teacher_external.py src_teacher_folder dst_teacher_folder --positions 10000000
```

出力は以下のようになる。

```text
dst_teacher_folder/shuffled-00001.hcpe
dst_teacher_folder/shuffled-00002.hcpe
...
```

PSVフォルダを指定した場合は `.psv` で出力する。

```text
dst_teacher_folder/shuffled-00001.psv
dst_teacher_folder/shuffled-00002.psv
...
```

出力ファイル名のprefixを変えたい場合:

```bash
python teacher/shuffle_split_teacher_external.py src_teacher_folder dst_teacher_folder --positions 10000000 --prefix train
```

主なオプション:

| オプション | デフォルト | 説明 |
|---|---:|---|
| `--positions` | `10000000` | 1出力ファイルあたりの局面数。 |
| `--prefix` | `shuffled` | 出力ファイル名のprefix。 |
| `--digits` | `5` | 出力ファイル番号のゼロ埋め桁数。10000ファイル以上になるなら5桁以上が必要。 |
| `--bucket-count` | `1024` | 一時bucket数。大きいほどbucketごとのメモリ使用量は下がる。 |
| `--chunk-records` | `1000000` | 入力を読む単位。 |
| `--seed` | `0` | bucket順とbucket内shuffleのseed。 |
| `--format` | 自動判定 | `hcpe` または `psv`。入力フォルダに両方ある場合は明示する。 |
| `--recursive` | off | 入力フォルダを再帰的に探索する。 |
| `--tmp-dir` | 出力フォルダ | 一時bucketファイルの作成先。 |
| `--force` | off | 出力先の既存ファイルを許可し、同じprefixの出力を上書きする。 |

注意点:

- 対象は `.hcpe` と `.psv`。同じ入力フォルダ内で両形式を混在させる場合は `--format` を指定する。
- bucketごとのシャッフルなので、厳密な全体Fisher-Yates shuffleではない。棋譜内の連続局面による自己相関を壊す目的には十分実用的。
- 一時ファイルとして入力とほぼ同じサイズの容量が追加で必要。
- 出力フォルダに既存ファイルがある場合は、誤上書きを避けるためデフォルトではエラーにする。

### 固定長教師ファイルをオンメモリでシャッフルする

PSVをシャッフルして1ファイルに出力:

```bash
python teacher/split_teacher.py input.psv --output shuffled.psv --shuffle
```

HCPEをシャッフルして1ファイルに出力:

```bash
python teacher/split_teacher.py input.hcpe --output shuffled.hcpe --shuffle
```

シャッフルしながら10分割:

```bash
python teacher/split_teacher.py input.psv --output shuffled.psv --shuffle --split 10
python teacher/split_teacher.py input.hcpe --output shuffled.hcpe --shuffle --split 10
```

1ファイルあたり5000万局面で分割:

```bash
python teacher/split_teacher.py input.psv --output shuffled.psv --shuffle --positions 50000000
python teacher/split_teacher.py input.hcpe --output shuffled.hcpe --shuffle --positions 50000000
```

複数ファイルを連結してからシャッフル:

```bash
python teacher/split_teacher.py a.psv b.psv c.psv --output shuffled.psv --shuffle
python teacher/split_teacher.py a.hcpe b.hcpe c.hcpe --output shuffled.hcpe --shuffle
```

乱数seedを固定したい場合:

```bash
python teacher/split_teacher.py input.psv --output shuffled.psv --shuffle --seed 20260513
```

注意点:

- `split_teacher.py` はオンメモリ処理です。巨大ファイルを全体シャッフルする場合は、入力全体を載せられるRAMが必要です。
- 出力形式は入力形式と同じです。出力ファイルに拡張子を付ける場合は、入力と同じ `.psv` または `.hcpe` にしてください。
- `--output` は出力ファイル名、または分割時の出力ファイル名のベースです。`--outpath` も互換エイリアスとして使えます。
- `--split` または `--positions` を指定した場合、出力ファイル名は `shuffled-001.psv`, `shuffled-002.psv`, ... のようになります。
- `--split` と `--positions` は同時指定できません。
- `--uniq` を指定すると、シャッフルや分割の前に同一レコードを除去します。
- `--uniq-each-split` を指定すると、分割後の各出力ファイルごとに同一レコードを除去します。
- 入力ファイルサイズがレコードサイズで割り切れない場合は、壊れたファイルとしてエラーにします。
- `psv` と `hcpe` を同時に指定することはできません。

### HCPE3フォルダ内のファイルをN個ずつ結合する

1つのフォルダ内に小さいHCPE3ファイルが多数ある場合は、`teacher/concat_hcpe3.py` を使って、ファイル単位でN個ずつ単純結合します。

たとえば入力フォルダ内のHCPE3を10ファイルずつまとめる場合:

```bash
python teacher/concat_hcpe3.py \
  --output merged_teacher \
  --source src_teacher 10
```

出力は以下のようになる。

```text
merged_teacher/merged-00001.hcpe3
merged_teacher/merged-00002.hcpe3
...
merged_teacher/merged-manifest.tsv
```

`--source src_teacher 10` は `--source src_teacher --group-size 10` と同じ意味です。
入力ファイルはファイル名の辞書順に処理します。HCPE3にはファイル全体のヘッダがないため、完全なHCPE3ファイル同士のバイナリ結合として扱います。

主なオプション:

| オプション | デフォルト | 説明 |
|---|---:|---|
| `--output` | 必須 | 出力フォルダ。 |
| `--source DIR [COUNT]` | 必須 | 入力フォルダ。`COUNT` を付けた場合は1出力あたりの入力ファイル数。 |
| `--group-size` | なし | 1出力あたりの入力ファイル数。`--source DIR COUNT` の代わりに使える。 |
| `--pattern` | `*.hcpe3` | 入力ファイル名のglob pattern。 |
| `--recursive` | off | 入力フォルダを再帰的に探索する。 |
| `--prefix` | `merged` | 出力ファイル名のprefix。 |
| `--digits` | `5` | 出力ファイル番号のゼロ埋め桁数。 |
| `--drop-remainder` | off | 最後に指定ファイル数へ満たない余りがある場合、その余りを出力しない。 |
| `--no-manifest` | off | manifest TSVを出力しない。 |
| `--force` | off | 既存の出力ファイルとmanifestの上書きを許可する。 |

### HCPE3フォルダを棋譜数比率で結合する

HCPE3は棋譜単位の可変長形式なので、PSV/HCPEのような局面単位シャッフルには向きません。
複数の方法で生成したHCPE3教師フォルダを混ぜたい場合は、`teacher/concat_hcpe3_round_robin.py` を使って、各フォルダの棋譜数比率で棋譜recordを取り出して結合します。

たとえば `teacher1/` と `teacher2/` と `teacher3/` のHCPE3を混ぜ、1出力ファイルを最大8GiB程度に抑える場合:

```bash
python teacher/concat_hcpe3_round_robin.py \
  --output mixed_teacher \
  --source teacher1 \
  --source teacher2 \
  --source teacher3 \
  --max-output-size 8G
```

出力は以下のようになる。

```text
mixed_teacher/mixed-00001.hcpe3
mixed_teacher/mixed-00002.hcpe3
...
mixed_teacher/mixed-manifest.tsv
```

最初に各source内の各HCPE3ファイルを走査して、ファイルごとの棋譜数を数えます。
たとえば `teacher1` が1000局、`teacher2` が100局なら、source間は `1000:100`、つまり実質 `10:1` の比率で混ざるようにします。
さらに選ばれたsource内でも、各HCPE3ファイルを棋譜数比率で選ぶため、1つの出力HCPE3は複数の入力ファイルから集めた棋譜recordで構成されます。
`--max-output-size` を指定した場合は、次の棋譜recordを追加すると上限を超えるタイミングで次の出力ファイルへ切り替える。
HCPE3にはファイル全体のヘッダがないため、完全なHCPE3棋譜record同士のバイナリ結合として扱います。

HCPE3は可変長record列なので、棋譜数を数えるときも各recordの `moveNum` と各手の `candidateNum` を読んで次のrecord位置まで進む必要があります。
カウント中と出力中の進捗はstderrへ表示します。

manifest TSVは、1つのmixedファイルにつき1行です。各sourceの `ranges` には、使用した入力HCPE3ファイルと、そのファイル内の棋譜番号範囲を記録します。
同じ入力ファイルから連続番号で使われた棋譜は、出力上で隣接していなくても1つのrangeにまとめます。

```text
output	bytes	games	source1_games	source1_bytes	source1_ranges	source2_games	source2_bytes	source2_ranges
mixed_teacher/mixed-00001.hcpe3	8589930000	120000	40000	2863310000	teacher1/a.hcpe3:1-40000	80000	5726620000	teacher2/a.hcpe3:1-50000;teacher2/b.hcpe3:1-30000
```

`--max-output-size` を指定しない場合は、すべてのsourceの棋譜を1つのHCPE3へ出力します。

主なオプション:

| オプション | デフォルト | 説明 |
|---|---:|---|
| `--output` | 必須 | 出力フォルダ。 |
| `--source DIR` | 必須 | 入力フォルダ。複数回指定できる。各sourceの棋譜数を数え、その比率で自動混合する。 |
| `--pattern` | `*.hcpe3` | 入力ファイル名のglob pattern。 |
| `--recursive` | off | 各入力フォルダを再帰的に探索する。 |
| `--prefix` | `mixed` | 出力ファイル名のprefix。 |
| `--digits` | `5` | 出力ファイル番号のゼロ埋め桁数。 |
| `--max-output-size` | なし | 出力ファイルサイズの上限。`512M`, `8G`, byte数などで指定する。 |
| `--max-outputs` | なし | 出力ファイル数の上限。 |
| `--no-manifest` | off | manifest TSVを出力しない。 |
| `--force` | off | 既存の出力ファイルとmanifestの上書きを許可する。 |
| `--progress-interval` | `5.0` | 進捗表示の間隔秒。 |
| `--no-progress` | off | 進捗表示をしない。 |
| `--max-open-files` | `64` | n-way merge中に同時に開いたままにする入力HCPE3ファイル数。 |

注意点:

- 入力ファイルは各フォルダ内でファイル名の辞書順に列挙し、同率時はこの順で選択します。
- source内の複数入力HCPE3は、1ファイルずつ読み切るのではなく、棋譜record単位で混ぜます。
- 入力HCPE3内の棋譜recordは先頭から順番に処理します。局面単位では分割しません。
- すべてのsourceの棋譜を使い切ります。`--max-outputs` を指定した場合は、その出力数に達したところで停止します。
- `--max-output-size` は棋譜record境界で判定します。1棋譜record自体が上限より大きい場合、そのrecordだけで上限を超えた出力ファイルを作ります。
- `mixed-manifest.tsv` には、各出力ファイルにどの入力範囲を結合したかを、1出力1行で記録します。

## 教師データのフォーマット変換

`pack` / `psv` / `hcpe` / `hcpe3` はすべて教師データとして使えますが、形式の性質が違うため、すべての方向に可逆変換できるわけではありません。

直接変換できるもの:

| 変換 | 使うもの | 備考 |
|---|---|---|
| `pack` -> `hcpe` | `teacher/convert_teacher.py` | GenSfenのpack形式をHCPEへ展開する。複数ファイルやフォルダ入力にも対応。 |
| `hcpe` -> `psv` | `teacher/convert_teacher.py` | HCPEの局面・評価値・指し手・勝敗をPSVへ変換する。複数ファイルやフォルダ入力にも対応。 |
| `psv` -> `hcpe` | `teacher/convert_teacher.py` | PSVをHCPEへ変換する。PSVの `gamePly` はHCPEには入らない。複数ファイルやフォルダ入力にも対応。 |
| `hcpe3` -> `hcpe` | `teacher/convert_teacher.py` | HCPE3の各ゲームを局面列に展開してHCPEへ変換する。複数ファイルやフォルダ入力にも対応。 |
| `hcpe3` -> `psv` | `teacher/convert_teacher.py` | HCPE3を局面列に展開してPSVへ変換する。複数ファイルやフォルダ入力にも対応。 |

`pack` から `hcpe`:

```bash
python teacher/convert_teacher.py --input input.pack --output output.hcpe
```

`pack` は棋譜形式、HCPEは局面単位の固定長形式なので、変換後のファイルサイズは大きくなります。目安としては10倍程度に膨らむことがあります。

複数の `pack` ファイルをまとめて変換したい場合、`pack` はバイナリファイルとして単純結合できます。Windowsのコマンドプロンプトなら以下のように1ファイルへ結合してから変換します。

```bat
copy /B *.pack merged.pack
python teacher/convert_teacher.py --input merged.pack --output merged.hcpe
```

`hcpe` から `psv`:

```bash
python teacher/convert_teacher.py --input input.hcpe --output output.psv
```

`psv` から `hcpe`:

```bash
python teacher/convert_teacher.py --input input.psv --output output.hcpe
```

`hcpe3` から `hcpe`:

```bash
python teacher/convert_teacher.py --input input.hcpe3 --output output.hcpe
```

`hcpe3` から `psv`:

```bash
python teacher/convert_teacher.py --input input.hcpe3 --output output.psv
```

`convert_teacher.py` は入力形式を `--input` から推定します。入力がファイルなら拡張子で判定し、入力がフォルダなら、そのフォルダ内の教師ファイルの拡張子から判定します。出力が拡張子つきファイルなら、出力形式はその拡張子から判定します。出力がフォルダなら、将来変換先が増えたときに意味が変わらないように `--to` の指定を必須にしています。

フォルダ内の各ファイルを個別に変換:

```bash
python teacher/convert_teacher.py --input hcpe_dir --output psv_dir --to psv
python teacher/convert_teacher.py --input psv_dir --output hcpe_dir --to hcpe
python teacher/convert_teacher.py --input pack_dir --output hcpe_dir --to hcpe
python teacher/convert_teacher.py --input hcpe3_dir --output hcpe_dir --to hcpe
python teacher/convert_teacher.py --input hcpe3_dir --output psv_dir --to psv
```

フォルダ内の各ファイルを1つの出力ファイルへ結合:

```bash
python teacher/convert_teacher.py --input hcpe_dir --output merged.psv
python teacher/convert_teacher.py --input psv_dir --output merged.hcpe
python teacher/convert_teacher.py --input pack_dir --output merged.hcpe
python teacher/convert_teacher.py --input hcpe3_dir --output merged.hcpe
python teacher/convert_teacher.py --input hcpe3_dir --output merged.psv
```

サブフォルダも含めて変換したい場合は `--recursive` を指定します。固定長形式同士の変換では、`--batch-size` で一度に処理するレコード数を変更できます。

`pack` から `psv` へ変換したい場合は、いったんHCPEへ変換してからPSVへ変換します。

```bash
python teacher/convert_teacher.py --input input.pack --output tmp.hcpe
python teacher/convert_teacher.py --input tmp.hcpe --output output.psv
```

直接変換スクリプトがないもの:

| 変換 | 扱い |
|---|---|
| `pack` -> `psv` | `pack -> hcpe -> psv` の2段階で変換する。 |
| `pack` -> `hcpe3` | 既存packをそのままHCPE3へ変換するスクリプトはない。必要なら `pack -> hcpe -> hcpe3` とするが、後段はONNX再評価を伴う。 |
| `psv` -> `hcpe3` | 直接変換スクリプトはない。必要なら `psv -> hcpe -> hcpe3` とするが、後段はONNX再評価を伴う。 |
| `hcpe` / `psv` / `hcpe3` -> `pack` | 逆変換スクリプトはない。`pack` は棋譜形式なので、局面列から元の対局単位データを復元できない。 |

## re-eval

既存教師の評価値を、ONNXモデルで再評価して差し替える用途です。

📝 大きなモデルで評価値を付け替えた教師データから小さなモデルを学習させる場合、これは`知識蒸留`と呼ばれます。

HCPEの各局面を再評価し、HCPE3として出力する場合は `teacher/hcpe3_re_eval_from_hcpe.py` を使います。これは単なる `hcpe -> hcpe3` の可逆変換ではありません。各HCPEレコードを「`moveNum=1` の1ゲーム」としてHCPE3へ詰め、valueはモデル出力で再評価し、policyはモデルのpolicy出力を合法手上位 `--top-k` に絞って `MoveVisits` として保存します。

```bash
python teacher/hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3
```

TensorRT Execution Providerを優先する場合:

```bash
python teacher/hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3 --tensorrt
```

主なオプション:

| オプション | 既定値 | 内容 |
|---|---:|---|
| `--a` | `756.0864962951762` | value `(0..1)` からscore `(cp)` へ変換する係数。本家 `hcpe3_re_eval.py` と同一。 |
| `--batch-size`, `-b` | `1024` | 推論バッチサイズ。HCPEレコード単位。 |
| `--top-k` | `8` | `MoveVisits` に書き出す候補手数。policy上位K手だけをsoftmaxしてuint16量子化する。 |
| `--tensorrt` | false | TensorRT Execution Providerを優先する。 |

出力HCPE3の構成:

| HCPE3フィールド | 値の作り方 |
|---|---|
| `hcp` | 入力HCPEの `hcp` をそのまま使う。 |
| `moveNum` | `1` 固定。 |
| `result` | 入力HCPEの `gameResult` をHCPE3 resultの下位2 bitに流し込む。 |
| `gameInfo` | `0`。 |
| `MoveInfo.selectedMove16` | 入力HCPEの `bestMove16` をそのまま使う。 |
| `MoveInfo.eval` | モデルのvalue出力を `value_to_score(values, a)` でscore `(cp)` に変換した値。 |
| `MoveInfo.candidateNum` | `min(--top-k, 合法手数)`。 |
| `MoveVisits[i].move16` | policy確率の降順で選んだ上位 `--top-k` 手の `move16`。 |
| `MoveVisits[i].visitNum` | 上位 `--top-k` 手のlogitをsoftmaxし、`int(p_i * 65535)` でuint16量子化した値。 |

HCPEの評価値だけを再評価してHCPEとして出力する場合は、dlshogiの `hcpe_re_eval.py` を使います。

```bash
python ../DeepLearningShogi/dlshogi/utils/hcpe_re_eval.py model.onnx input.hcpe output.hcpe
```

HCPE3の各 `MoveInfo.eval` だけを再評価してHCPE3として出力する場合は、dlshogiの `hcpe3_re_eval.py` を使います。

```bash
python ../DeepLearningShogi/dlshogi/utils/hcpe3_re_eval.py model.onnx input.hcpe3 output.hcpe3
```

注意点:

- policy教師はMCTSのvisit分布ではなく、モデルのpolicy出力を分布化したものです。
- `teacher/hcpe3_re_eval_from_hcpe.py` の `selectedMove16` は元HCPEの `bestMove16` をそのまま使います。`--top-k` の候補内に含まれていなくても、学習側が `MoveVisits` をpolicy教師として使う限り問題ありません。
- `--top-k` を大きくするとファイルサイズが大きくなります。まずは `--top-k 8` 程度が扱いやすいです。

WindowsでONNX Runtime GPU版を使う場合は、cuDNN / cuBLASのDLLがPATHに通っている必要があります。pipでインストールする場合:

```powershell
pip install onnxruntime-gpu nvidia-cudnn-cu12
# TensorRTも使うなら追加
pip install tensorrt-cu12
```

pip版 `nvidia-cudnn-cu12` のDLLは `...\site-packages\nvidia\cudnn\bin\` に置かれますが、これはWindowsの既定DLL検索パスに含まれません。`teacher/hcpe3_re_eval_from_hcpe.py` は起動時に、pipで入れた `nvidia.<lib>\bin` を `%PATH%` と `os.add_dll_directory()` に追加します。

TensorRTをzip配布版で入れる場合は、PATHに `lib\` ではなくDLLが置かれている `bin\` を通してください。`--tensorrt` 付きの初回起動ではONNXからTensorRT engineをビルドするため、数分から十数分かかることがあります。

## 教師データのフィルタリング

HCPEから評価値が大きすぎる局面を除外したい場合は、`teacher/filter_hcpe_by_eval.py` を使います。

HCPEは1局面38 byteの固定長レコードで、評価値はoffset 32にlittle-endian signed int16として保存されています。このスクリプトはHCPEをレコード単位で読み、`abs(eval) >= threshold` の局面を除外して別ファイルへ書き出します。

基本形:

```bash
python teacher/filter_hcpe_by_eval.py input.hcpe output.hcpe
```

出力ファイルを省略した場合は、入力ファイル名に `.filtered` を付けます。

```bash
python teacher/filter_hcpe_by_eval.py input.hcpe
```

閾値を変更する場合:

```bash
python teacher/filter_hcpe_by_eval.py input.hcpe output.hcpe --threshold 30000
```

フォルダ内のファイルを一括処理する場合:

```bash
python teacher/filter_hcpe_by_eval.py -source hcpe/ -dest hcpe-filtered-by-eval/
```

主なオプション:

| オプション | 既定値 | 内容 |
|---|---:|---|
| `--threshold` | `25000` | `abs(eval) >= threshold` のレコードを削除する。 |
| `--chunk-records` | `1000000` | 一度に読み込むHCPEレコード数。大きいHCPEを丸読みしないための処理単位。 |
| `-source`, `--source` | なし | 一括処理する入力フォルダ。直下の通常ファイルを処理する。 |
| `-dest`, `--dest` | なし | 一括処理の出力フォルダ。入力ファイルと同じ相対pathで出力する。 |
| `--recursive` | false | `-source` 配下のサブフォルダも処理する。 |

### 引き分け局面を取り除く (`teacher/filter_drawn_games.py`)

HCPE / PSV ファイルから対局結果が引き分け (`game_result == 0`) の局面を除外したい場合は、`teacher/filter_drawn_games.py` を使います。形式 (`.hcpe` / `.psv`) は拡張子で自動判別します。

想定用途は **検証用局面集の前処理**:

- BulletOu の `test_value_accuracy` および YaneuraOu の `test eval_accuracy` は、accuracy を計算する際に引き分け局面を分母分子の両方から除外します (= 「W vs L の符号一致率」を測る)。これは dlshogi 本家の検証用局面集に引き分けが含まれていないことに合わせた仕様です。
- dlshogi `train.py` でも検証用局面ファイルを `--test_data` で渡しますが、ここに引き分けが含まれていると上記メトリクスと数値が合わなくなります。検証局面ファイル側からも引き分けを取り除いておくと、3 経路 (BulletOu / やねうら王 / dlshogi) の accuracy が直接比較できる数値になります。
- 学習用局面 (= `--teacher` に渡すもの) からも除外したい場合に使うことも可能ですが、訓練側 loss 関数では引き分け局面はラベル平滑化の役割を果たすので、必ずしも除外する必要はありません。判断はユーザー側で。

基本形:

```bash
python teacher/filter_drawn_games.py input.hcpe output.hcpe
python teacher/filter_drawn_games.py input.psv  output.psv
```

出力ファイルを省略した場合は、入力ファイル名に `.no-drawn` を付けます (例: `input.hcpe` → `input.no-drawn.hcpe`)。

```bash
python teacher/filter_drawn_games.py input.hcpe
```

フォルダ内の `.hcpe` / `.psv` を一括処理する場合:

```bash
python teacher/filter_drawn_games.py -source teacher/ -dest teacher-no-drawn/
python teacher/filter_drawn_games.py -source teacher/ -dest teacher-no-drawn/ --recursive
```

主なオプション:

| オプション | 既定値 | 内容 |
|---|---:|---|
| `--chunk-records` | `1000000` | 一度に読み込むレコード数。 |
| `-source`, `--source` | なし | 一括処理する入力フォルダ。`.hcpe` / `.psv` を処理対象にする。 |
| `-dest`, `--dest` | なし | 一括処理の出力フォルダ。入力ファイルと同じ相対pathで出力する。 |
| `--recursive` | false | `-source` 配下のサブフォルダも処理する。 |

レコードレイアウト上の引き分け判定 (= byte 値 0 を draw と扱う) は HCPE / PSV どちらも同じなので、auto-detect で両形式を同じスクリプトで扱っています。

注意点:

- 入力と出力は別ファイルにしてください。
- 一括処理では `-source` と `-dest` を必ずセットで指定します。
- 入力ファイルサイズが38で割り切れない場合は、HCPEではない、または壊れたファイルとしてエラーにします。
- HCPE3ではなく、従来のHCPE形式を対象にします。
