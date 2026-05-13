# teacher-script

やねうら王系・dlshogi系の教師局面ファイルを加工するためのスクリプト群です。

## スクリプト一覧

| スクリプト | 内容 |
|---|---|
| `split_psv.py` | PSVファイルを局面単位で分割し、必要ならシャッフルする。 |
| `filter_hcpe_by_eval.py` | HCPEファイルから、評価値の絶対値が指定閾値以上の局面を取り除く。 |
| `hcpe3_re_eval_from_hcpe.py` | HCPEファイルの各局面をONNXモデルで再評価し、policy蒸留付きのHCPE3として書き出す。 |

## 局面のシャッフルについて

学習前には、教師局面をできるだけシャッフルしておくことを推奨します。

自己対局で生成した教師は、1局内の連続局面同士の自己相関が強く、駒配置や進行度も局所的に偏ります。対局順のまま学習に流すと、ミニバッチごとの分布が偏りやすく、lossの周期的な揺れや学習効率の低下につながります。

形式ごとの扱いは次の通りです。

| 形式 | シャッフル方法 |
|---|---|
| `psv` | `split_psv.py --shuffle` を使う。40 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `hcpe` | dlshogiの `split_hcpe.py --shuffle` を使う。38 byte固定長の局面レコードなので局面単位でシャッフルできる。 |
| `pack` | 棋譜形式なので、ファイル上のレコードを単純に局面単位シャッフルする用途には向かない。 |
| `hcpe3` | 棋譜単位の可変長形式なので、単純な局面単位シャッフルには向かない。 |

## split_psv.py

`.psv` は40 byte固定長の `PsvRecord` / `PackedSfenValue` を並べた形式です。このスクリプトは、PSVを局面レコード単位で読み込み、分割・シャッフル・重複削除を行います。

単一ファイルへシャッフルして出力:

```bash
python teacher-script/split_psv.py input.psv --outpath shuffled.psv --shuffle
```

分割も同時に行う場合:

```bash
python teacher-script/split_psv.py input.psv --outpath shuffled.psv --shuffle --split 10
```

1ファイルあたりの局面数を指定する場合:

```bash
python teacher-script/split_psv.py input.psv --outpath shuffled.psv --shuffle --positions 50000000
```

複数のPSVを連結してからシャッフルする場合:

```bash
python teacher-script/split_psv.py a.psv b.psv c.psv --outpath shuffled.psv --shuffle
```

`--split` または `--positions` を指定した場合、出力ファイル名は `shuffled-001.psv`, `shuffled-002.psv`, ... のようになります。分割しない場合は `--outpath` で指定したファイルに直接出力します。

主なオプション:

| オプション | 内容 |
|---|---|
| `--outpath PATH` | 出力先。分割時はこの名前をベースに連番ファイルを作る。省略時は入力ファイル名をベースにする。 |
| `--shuffle` | レコード順をランダムにシャッフルする。 |
| `--seed N` | `--shuffle` の乱数seedを指定する。 |
| `--split N` | 出力を最大N個のファイルに分割する。 |
| `--positions N` | 1ファイルあたり最大N局面で分割する。 |
| `--uniq` | 出力前に重複レコードを削除する。 |
| `--uniq_each_split` | 分割後の各ファイル単位で重複レコードを削除する。 |

注意点:

- 入力ファイルサイズが40で割り切れない場合は、PSVではない、または壊れたファイルとしてエラーにします。
- このスクリプトはオンメモリ処理です。巨大PSVを全体シャッフルする場合は、入力全体を載せられるRAMが必要です。
- `--split` と `--positions` は同時指定できません。

## filter_hcpe_by_eval.py

HCPEは1局面38 byteの固定長レコードで、評価値はoffset 32にlittle-endian signed int16として保存されています。このスクリプトはHCPEをレコード単位で読み、評価値が大きすぎる局面を除外して別ファイルへ書き出します。

基本形:

```bash
python teacher-script/filter_hcpe_by_eval.py input.hcpe output.hcpe
```

出力ファイルを省略した場合は、入力ファイル名に `.filtered` を付けます。

```bash
python teacher-script/filter_hcpe_by_eval.py input.hcpe
```

閾値を変更する場合:

```bash
python teacher-script/filter_hcpe_by_eval.py input.hcpe output.hcpe --threshold 30000
```

フォルダ内のファイルを一括処理する場合:

```bash
python teacher-script/filter_hcpe_by_eval.py -source hcpe/ -dest hcpe-filtered-by-eval/
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

## hcpe3_re_eval_from_hcpe.py

HCPEファイルの各局面をONNXモデルで再評価し、HCPE3形式で書き出します。DeepLearningShogiの `dlshogi/utils/hcpe3_re_eval.py` をベースに、入力をHCPEへ差し替えたものです。

各HCPEレコードは「`moveNum=1` の1ゲーム」としてHCPE3に詰めます。valueはモデル出力で再評価し、policyはモデルのpolicy出力を合法手上位 `--top-k` に絞って `MoveVisits` として保存します。

基本形:

```bash
python teacher-script/hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3
```

TensorRT Execution Providerを優先する場合:

```bash
python teacher-script/hcpe3_re_eval_from_hcpe.py model.onnx input.hcpe output.hcpe3 --tensorrt
```

主なオプション:

| オプション | 既定値 | 内容 |
|---|---:|---|
| `--a` | `756.0864962951762` | value `(0..1)` からscore `(cp)` へ変換する係数。本家 `hcpe3_re_eval.py` と同一。 |
| `--batch-size`, `-b` | `1024` | 推論バッチサイズ。HCPEレコード単位。 |
| `--top-k` | `8` | `MoveVisits` に書き出す候補手数。policy上位K手だけをsoftmaxしてuint16量子化する。 |
| `--tensorrt` | false | TensorRT Execution Providerを優先する。 |

### 出力HCPE3の構成

各HCPEレコードに対応する出力HCPE3ゲームの中身:

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

### 意図と注意

- HCPEからHCPE3学習パイプラインへ、より大きいモデルから蒸留したvalue・policy教師を流したい用途です。
- policy教師はMCTSのvisit分布ではなく、モデルのpolicy出力を分布化したものです。
- `result` はHCPEの `gameResult` を流し込むだけで、千日手・入玉宣言・最大手数などの上位bitは立てません。
- `selectedMove16` は元HCPEの `bestMove16` をそのまま使います。`--top-k` の候補内に含まれていなくても、学習側が `MoveVisits` をpolicy教師として使う限り問題ありません。
- 入力ファイルサイズが38で割り切れない場合はエラーにします。

### WindowsでのGPUセットアップ

ONNX Runtime GPU版はcuDNN / cuBLASのDLLを実行時にロードするため、それらがPATHに通っている必要があります。pipでインストールする場合:

```powershell
pip install onnxruntime-gpu nvidia-cudnn-cu12
# TensorRTも使うなら追加
pip install tensorrt-cu12
```

pip版 `nvidia-cudnn-cu12` のDLLは `...\site-packages\nvidia\cudnn\bin\` に置かれますが、これはWindowsの既定DLL検索パスに含まれません。本スクリプトは起動時に、pipで入れた `nvidia.<lib>\bin` を `%PATH%` と `os.add_dll_directory()` に追加します。

TensorRTをzip配布版で入れる場合は、PATHに `lib\` ではなくDLLが置かれている `bin\` を通してください。`--tensorrt` 付きの初回起動ではONNXからTensorRT engineをビルドするため、数分から十数分かかることがあります。

### policy蒸留のスケール感

`--top-k` を変えるとファイルサイズが大きく変わります。本家自己対局HCPE3はMCTSで実際に訪問した手だけが `MoveVisits` に入っています。

| 設定 | 1局面あたりのMoveVisits | ファイルサイズ感 |
|---|---:|---|
| `--top-k 4` | 16 B | HCPEより小さくなることもある。 |
| `--top-k 8` | 32 B | HCPEの2倍程度。 |
| `--top-k 16` | 64 B | 大きめ。 |
| 全合法手相当 | 数百から千B程度 | 本家HCPE3の数倍になりうる。 |

policy教師として現実的に指される手だけを残すなら、まずは `--top-k 8` 程度が扱いやすいです。
