# teacher

やねうら王系・dlshogi系の教師データを加工するためのスクリプト群です。

このREADMEでは、スクリプト名ではなく「何をしたいか」を軸に説明します。

コマンド例は `YaneuraOu-ScriptCollection` のrootディレクトリから実行する前提です。たとえば `teacher/pack2hcpe.py` は、このリポジトリ内の `YaneuraOu-ScriptCollection/teacher/pack2hcpe.py` を指します。`GenSfen/` は同じリポジトリ内の教師生成用フォルダです。

一部の変換・re-evalでは、dlshogi本家リポジトリをcloneした `DeepLearningShogi` のスクリプトを使います。このREADMEでは、`YaneuraOu-ScriptCollection` と同じ親フォルダに以下のようにcloneされている前提で `../DeepLearningShogi/...` と書きます。

```bash
git clone https://github.com/TadaoYamaoka/DeepLearningShogi.git
```

## 教師データのシャッフル

学習前には、教師局面をできるだけシャッフルしておくことを推奨します。

自己対局で生成した教師は、1局内の連続局面同士の自己相関が強く、駒配置や進行度も局所的に偏ります。対局順のまま学習に流すと、ミニバッチごとの分布が偏りやすく、lossの周期的な揺れや学習効率の低下につながります。

形式ごとの扱い:

| 形式 | シャッフル方法 |
|---|---|
| `psv` | `teacher/split_psv.py --shuffle` を使う。40 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `hcpe` | dlshogiの `split_hcpe.py --shuffle` を使う。38 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `pack` | 棋譜形式なので、ファイル上のレコードを単純に局面単位シャッフルする用途には向かない。 |
| `hcpe3` | 棋譜単位の可変長形式なので、単純な局面単位シャッフルには向かない。 |

PSVをシャッフルして1ファイルに出力:

```bash
python teacher/split_psv.py input.psv --outpath shuffled.psv --shuffle
```

PSVをシャッフルしながら10分割:

```bash
python teacher/split_psv.py input.psv --outpath shuffled.psv --shuffle --split 10
```

PSVを1ファイルあたり5000万局面で分割:

```bash
python teacher/split_psv.py input.psv --outpath shuffled.psv --shuffle --positions 50000000
```

複数のPSVを連結してからシャッフル:

```bash
python teacher/split_psv.py a.psv b.psv c.psv --outpath shuffled.psv --shuffle
```

乱数seedを固定したい場合:

```bash
python teacher/split_psv.py input.psv --outpath shuffled.psv --shuffle --seed 20260513
```

注意点:

- `split_psv.py` はオンメモリ処理です。巨大PSVを全体シャッフルする場合は、入力全体を載せられるRAMが必要です。
- `--split` または `--positions` を指定した場合、出力ファイル名は `shuffled-001.psv`, `shuffled-002.psv`, ... のようになります。
- `--split` と `--positions` は同時指定できません。
- 入力ファイルサイズが40で割り切れない場合は、PSVではない、または壊れたファイルとしてエラーにします。

HCPEをシャッフルする場合は、dlshogiの `split_hcpe.py` を使います。

```bash
python ../DeepLearningShogi/dlshogi/utils/split_hcpe.py input.hcpe --outpath shuffled.hcpe --shuffle
```

## 教師データのフォーマット変換

`pack` / `psv` / `hcpe` / `hcpe3` はすべて教師データとして使えますが、形式の性質が違うため、すべての方向に可逆変換できるわけではありません。

直接変換できるもの:

| 変換 | 使うもの | 備考 |
|---|---|---|
| `pack` -> `hcpe` | `teacher/pack2hcpe.py` | GenSfenのpack形式をHCPEへ展開する。 |
| `hcpe` -> `psv` | `DeepLearningShogi/dlshogi/utils/hcpe_to_psv.py` | HCPEの局面・評価値・指し手・勝敗をPSVへ変換する。 |
| `psv` -> `hcpe` | `DeepLearningShogi/dlshogi/utils/psv_to_hcpe.py` | PSVをHCPEへ変換する。PSVの `gamePly` はHCPEには入らない。 |
| `hcpe3` -> `hcpe` | `DeepLearningShogi/dlshogi/utils/hcpe3_to_hcpe.py` | HCPE3の各ゲームを局面列に展開してHCPEへ変換する。 |
| `hcpe3` -> `psv` | `teacher/hcpe3_to_psv.py` | HCPE3を局面列に展開してPSVへ変換する。複数ファイルやフォルダ入力にも対応。 |

`pack` から `hcpe`:

```bash
python teacher/pack2hcpe.py input.pack output.hcpe
```

出力ファイルを省略した場合は、入力ファイル名に `.hcpe` を付けた名前で出力します。

```bash
python teacher/pack2hcpe.py input.pack
```

この場合、`input.pack.hcpe` のようなファイル名になります。`pack` は棋譜形式、HCPEは局面単位の固定長形式なので、変換後のファイルサイズは大きくなります。目安としては10倍程度に膨らむことがあります。

評価値を数手先まで平滑化しながら `pack` から `hcpe`:

```bash
python teacher/pack2hcpe.py input.pack output.hcpe --smoothing 3 --discount 0.9
```

`--smoothing` は何手先までの評価値を使うか、`--discount` は先の評価値に掛ける割引率です。上の例では、現局面の評価値、1手先の評価値×0.9、2手先の評価値×0.9×0.9 の加重平均を現局面の評価値として使います。

```text
新しい評価値 = (eval[0] + eval[1] * 0.9 + eval[2] * 0.9 * 0.9) / (1.0 + 0.9 + 0.9 * 0.9)
```

複数の `pack` ファイルをまとめて変換したい場合、`pack` はバイナリファイルとして単純結合できます。Windowsのコマンドプロンプトなら以下のように1ファイルへ結合してから変換します。

```bat
copy /B *.pack merged.pack
python teacher/pack2hcpe.py merged.pack merged.hcpe
```

`hcpe` から `psv`:

```bash
python ../DeepLearningShogi/dlshogi/utils/hcpe_to_psv.py input.hcpe output.psv
```

`psv` から `hcpe`:

```bash
python ../DeepLearningShogi/dlshogi/utils/psv_to_hcpe.py input.psv output.hcpe
```

`hcpe3` から `hcpe`:

```bash
python ../DeepLearningShogi/dlshogi/utils/hcpe3_to_hcpe.py input.hcpe3 output.hcpe
```

`hcpe3` から `psv`:

```bash
python teacher/hcpe3_to_psv.py --input input.hcpe3 --output output.psv
```

フォルダ内の `*.hcpe3` を個別にPSVへ変換:

```bash
python teacher/hcpe3_to_psv.py --input hcpe3_dir --output psv_dir
```

フォルダ内の `*.hcpe3` を1つのPSVへ結合:

```bash
python teacher/hcpe3_to_psv.py --input hcpe3_dir --output merged.psv
```

`pack` から `psv` へ変換したい場合は、いったんHCPEへ変換してからPSVへ変換します。

```bash
python teacher/pack2hcpe.py input.pack tmp.hcpe
python ../DeepLearningShogi/dlshogi/utils/hcpe_to_psv.py tmp.hcpe output.psv
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

注意点:

- 入力と出力は別ファイルにしてください。
- 一括処理では `-source` と `-dest` を必ずセットで指定します。
- 入力ファイルサイズが38で割り切れない場合は、HCPEではない、または壊れたファイルとしてエラーにします。
- HCPE3ではなく、従来のHCPE形式を対象にします。
