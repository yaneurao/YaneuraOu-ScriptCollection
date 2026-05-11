# dlshogi-script

dlshogi 用の教師データ作成・整理に使う補助スクリプトを置くフォルダ。

## スクリプト一覧

| スクリプト | 内容 |
|---|---|
| `filter_hcpe_by_eval.py` | HCPEファイルから、評価値の絶対値が指定閾値以上の局面を取り除く。既定では `abs(eval) >= 25000` のrecordを削除する。 |
| `hcpe3_re_eval_from_hcpe.py` | HCPE ファイルの各局面を ONNX モデルで再評価し、HCPE3 形式 (1 hcpe = moveNum=1 のゲーム、policy 蒸留付き) で書き出す。value と policy の両方を大きいモデルから蒸留して HCPE3 学習パイプラインに流したい用途。 |

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

## hcpe3_re_eval_from_hcpe.py

DeepLearningShogi/dlshogi/utils/hcpe3_re_eval.py をベースにして、入力を HCPE 形式に差し替えたもの。各 hcpe レコードを「moveNum=1 の 1 ゲーム」として HCPE3 に詰める。

基本形:

```bash
python hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3
```

TensorRT EP を使う場合:

```bash
python hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3 --tensorrt
```

主なoption:

| option | 既定値 | 内容 |
|---|---:|---|
| `--a` | `756.0864962951762` | value (0..1) → score (cp) の係数。本家 `hcpe3_re_eval.py` と同一。 |
| `--batch-size`, `-b` | `1024` | 推論バッチサイズ (HCPE レコード単位)。 |
| `--tensorrt` | false | TensorRT Execution Provider を優先する。 |

### 出力レコードの構成

各 hcpe レコードに対応する出力 HCPE3 ゲームの中身:

| HCPE3 フィールド | 値の作り方 |
|---|---|
| `hcp` | 入力 hcpe の `hcp` をそのまま |
| `moveNum` | `1` 固定 |
| `result` | 入力 hcpe の `gameResult` を HCPE3 result の下位 2bit に流し込む |
| `gameInfo` | `0` |
| `MoveInfo.selectedMove16` | 入力 hcpe の `bestMove16` |
| `MoveInfo.eval` | モデルの value 出力を `value_to_score(values, a)` で score (cp) に変換した値 |
| `MoveInfo.candidateNum` | 現局面の合法手数 |
| `MoveVisits[i].move16` | 現局面の各合法手の `move16` |
| `MoveVisits[i].visitNum` | モデル policy の logit を合法手だけ抽出 → softmax → `int(p_i * 65535)` で uint16 量子化 |

### 意図と注意

- HCPE3 学習パイプラインに大きいモデルから蒸留した教師を流したい用途。value だけでなく policy も蒸留される。
- 元データに対する policy 教師は MCTS 由来ではなく **モデルの policy 出力をそのまま分布化したもの** になる。これは AlphaZero 系の改良 policy (visit 分布) とは性質が違う。
- `result` は HCPE の gameResult を流し込むだけで、千日手 / 入玉宣言 / 最大手数 などの上位 bit は立てない。`train.py` 側で result を重視する設定 (`--alpha_r`) で使う場合は、ここを別途設定する必要がある。
- selectedMove16 = 元 hcpe の bestMove16 をそのまま使う。モデル policy の argmax を採用したい用途では、呼び出し側で書き換えるか、本スクリプトを派生させる。
- 入力ファイルサイズが 38 で割り切れない場合はエラーにする。
