# dlshogi-script

dlshogi 用の教師データ作成・整理に使う補助スクリプトを置くフォルダ。

## スクリプト一覧

| スクリプト | 内容 |
|---|---|
| `filter_hcpe_by_eval.py` | HCPEファイルから、評価値の絶対値が指定閾値以上の局面を取り除く。既定では `abs(eval) >= 25000` のrecordを削除する。 |

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
