# scripts

floodgate、WCSC、電竜戦の棋譜から、定跡候補として掘る対局を抽出し、USI の `position` コマンドで指定できる形式に変換するスクリプト群です。

`*-kif-downloader.py` はコマンドラインから直接実行する入口です。GUIやCLIから呼び出される実装本体は `*_kif_downloader_core.py` に置いています。

出力は 1 対局 1 行です。

```text
startpos moves 7g7f 3c3d ...
startpos moves 2g2f 8c8d ...
```

同じ `startpos moves ...` 行が複数回現れた場合は、初出だけを出力します。出力順は、重複除去前の初出順を保ちます。

## 必要なもの

Python 3 と `cshogi` が必要です。`.7z` アーカイブを入力フォルダに置いて抽出する場合は `py7zr` も必要です。

Windowsでは、Python Launcher の `py` を使うのが一般的です。

```bat
py -m pip install cshogi py7zr
```

Linux/macOSでは、環境に合わせて `python3` などを使ってください。

```bash
python3 -m pip install cshogi
python3 -m pip install py7zr
```

WCSC16以前の公式棋譜アーカイブは `.lzh` 形式です。展開には `lhafile` を使います。

Windows:

```bat
py -m pip install lhafile
```

Linux/macOS:

```bash
python3 -m pip install lhafile
```

ただし、古い圧縮方式の一部は `lhafile` だけでは展開できません。その場合は外部コマンドとして 7-Zip、bsdtar、unar のいずれかを使います。

Windowsでは、7-Zip が標準のインストール先に入っていれば `C:\Program Files\7-Zip\7z.exe` または `C:\Program Files (x86)\7-Zip\7z.exe` を自動検出します。PATH に `7z` を追加していなくても構いません。

## 対象ファイル

各スクリプトに入力フォルダを指定すると、そのフォルダ以下を再帰的に走査します。

対象拡張子:

- `.csa`
- `.csv`
- `.kif`
- `.kifu`

入力フォルダ内に `.zip` または `.7z` がある場合は、`tmp` に一時展開してから、その中の棋譜も対象にします。抽出後、一時展開フォルダは削除します。

`.zip` は Python 標準ライブラリで展開します。`.7z` は `py7zr` で展開します。

現在の出力形式は `startpos moves ...` なので、平手開始局面以外の棋譜はスキップされます。

## player list

プレイヤー名によるフィルタは、2 種類のリストを同時に指定できます。

`--both-player-list PATH`

先手と後手の両方が、このリスト内のいずれかの正規表現に一致した棋譜だけを抽出します。

`--either-player-list PATH`

先手または後手の少なくとも片方が、このリスト内のいずれかの正規表現に一致した棋譜だけを抽出します。

両方を指定した場合は OR 条件です。つまり、`both-player-list` の条件、または `either-player-list` の条件のどちらかを満たす棋譜を出力します。

`--reversal-threshold X`

年・プレイヤー名・rating・決勝出場ソフトなどの条件を満たした棋譜のうち、評価値コメントから逆転が確認できる棋譜だけを抽出します。片方のプレイヤー自身が出力した評価値が一度 `X` 以上、または `-X` 以下になり、その後に同じプレイヤーの評価値が0をまたいだ場合に抽出対象になります。評価値コメントがない棋譜は除外されます。

判定仕様:

- 棋譜中のプレイヤー名は小文字化してから比較します。
- リスト側の正規表現も小文字化して扱います。
- Python の正規表現として `re.search()` で判定します。
- 空行と `#` で始まる行は無視します。

例:

```text
# yane で始まる名前
^yane.*

# dlshogi を含む名前
dlshogi
```

## floodgate-kif-extractor.py

floodgate の棋譜を抽出します。プレイヤー名フィルタに加えて、両対局者の rating 下限と対局日の範囲を指定できます。

```bash
python3 floodgate-kif-extractor.py INPUT_DIR OUTPUT_TXT [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--both-player-list both.txt] [--either-player-list either.txt] [--min-rating X] [--reversal-threshold X] [--verbose]
```

例:

```bash
python3 floodgate-kif-extractor.py \
  /path/to/floodgate-kifu \
  floodgate-positions.txt \
  --both-player-list strong-engines.txt \
  --either-player-list target-engines.txt \
  --start-date 2025-05-01 \
  --end-date 2025-05-07 \
  --min-rating 3500
```

`--start-date 2026-06-01 --end-date 2026-06-07` を指定した場合、floodgate棋譜のファイル名やパス名に含まれる対局日から、その期間内の棋譜だけを抽出します。`YYYY/MM/DD` 形式でも指定できます。月日部分は `2026/1/1` や `2026-1-1` のように1桁でも構いません。

`--min-rating 3500` を指定した場合、先手と後手の両方の rating が 3500 以上の棋譜だけを出力します。rating が見つからない棋譜は除外されます。

## floodgate-kif-downloader.py

floodgate の年別棋譜アーカイブをダウンロードします。

```bash
python3 floodgate-kif-downloader.py YEAR [--output-dir downloaded-kif/floodgate]
```

例:

```bash
python3 floodgate-kif-downloader.py 2026
```

`2008` 以降の年を指定できます。今年のものは、前日までの棋譜が含まれるアーカイブとして公開されています。

出力ファイルは指定フォルダ直下の `wdoorYYYY.7z` です。今年のアーカイブも日付を付けず、例えば `wdoor2026.7z` に保存します。サーバー上のファイルサイズと既存ファイルのサイズが同じ場合は、ダウンロードを省略します。サイズが異なる場合は `.tmp` にダウンロードしてから `wdoorYYYY.7z` に置き換えます。

## wcsc-kif-extractor.py

WCSC の棋譜を抽出します。rating フィルタはありません。

```bash
python3 wcsc-kif-extractor.py INPUT_DIR OUTPUT_TXT [--start-year YYYY] [--end-year YYYY] [--finalists-only] [--both-player-list both.txt] [--either-player-list either.txt] [--reversal-threshold X] [--verbose]
```

例:

```bash
python3 wcsc-kif-extractor.py \
  /path/to/wcsc-kifu \
  wcsc-positions.txt \
  --start-year 2020 \
  --end-year 2025 \
  --finalists-only \
  --either-player-list target-engines.txt
```

WCSCの年は `wcsc36` のようなフォルダ名やファイル名から判定します。`wcso1` は WCSC30 扱いで、2020年として扱います。

`--finalists-only` を指定すると、同じWCSC大会内の決勝棋譜から先手/後手のプレイヤー名を集め、そのどちらかが登場する棋譜だけを抽出します。`WCSC36+F3` や `WCSC23_F1_...` のように `F` が決勝、`U` が二次予選、`L` が一次予選として扱われます。

## denryu-kif-extractor.py

電竜戦の棋譜を抽出します。rating フィルタはありません。

```bash
python3 denryu-kif-extractor.py INPUT_DIR OUTPUT_TXT [--finalists-only] [--both-player-list both.txt] [--either-player-list either.txt] [--reversal-threshold X] [--verbose]
```

例:

```bash
python3 denryu-kif-extractor.py \
  /path/to/denryu-kifu \
  denryu-positions.txt \
  --finalists-only
```

`--finalists-only` を指定すると、同じ電竜戦本戦イベント内の決勝リーグまたはA級棋譜から先手/後手のプレイヤー名を集め、そのどちらかが登場する棋譜だけを抽出します。これにより、決勝に出場したソフトが予選で指した棋譜も抽出対象になります。

イベントは `dr6_production` のようなフォルダ名や、`dr6prd+...` / `dr6prod+...` のようなファイル名から判定します。決勝扱いの棋譜は、既存の本戦棋譜に合わせて `sr2pa`、`dr2prda`、`dr3prda`、`dr4a`、`dr5prda0`、`dr6prdf1` のようなステージ名、またはKIFヘッダ内の `決勝` から判定します。

## denryu-kif-downloader.py

電竜戦の棋譜をダウンロードします。電竜戦は1年に複数大会があるため、年ではなく大会URLまたは大会キーを指定します。

```bash
python3 denryu-kif-downloader.py SOURCE [--output-dir downloaded-kif/denryu] [--live] [--interval 10] [--overwrite]
```

例:

```bash
python3 denryu-kif-downloader.py https://denryu-sen.jp/denryusen/dr6_production/dr1_live.php
python3 denryu-kif-downloader.py dr6_tsec --live --interval 2
```

`--live` を指定しない場合は、大会ページまたは公式リンク集から一括ZIP/7zを探して展開します。一括ZIP/7zが見つからない場合は、live中継ページの `kifulist.txt` から `kifufiles/*.csa` を順に取得する方式にフォールバックします。`--live` を指定した場合は、最初からlive中継ページ経由で取得します。

ZIP/7zアーカイブはサーバー上のファイルサイズを確認し、前回正常に展開したアーカイブと同じサイズなら再ダウンロードを省略します。ダウンロードが必要な場合は `.tmp` に保存し、サイズ確認後に展開します。展開時も、既存の棋譜ファイルとアーカイブ内のファイルサイズが同じなら書き換えず、サイズが異なる場合だけ置き換えます。

アクセス間隔のデフォルトは 10 秒です。通常は 2 秒以上を指定してください。

既存大会の一覧は次のコマンドで確認できます。

```bash
python3 denryu-kif-downloader.py --list-tournaments
```

出力先は `downloaded-kif/denryu/dr6_production/` のように、指定した出力フォルダ配下の大会キーごとのフォルダです。

## 共通オプション

`--both-player-list PATH`

両対局者が正規表現リストに一致する棋譜だけを抽出します。指定しない場合、この条件では絞り込みません。

`--either-player-list PATH`

片方以上の対局者が正規表現リストに一致する棋譜だけを抽出します。指定しない場合、この条件では絞り込みません。

`--verbose`

パースできない棋譜や、平手開始局面ではない棋譜をスキップした理由を標準エラーに出力します。

## 終了時の表示

処理後に件数が表示されます。

```text
scanned=289 selected=289 skipped_year=0 skipped_date=0 skipped_finalist=0 skipped_name=0 skipped_rating=0 skipped_reversal=0 skipped_handicap=0 skipped_parse=0 skipped_duplicate=0
```

- `scanned`: 対象拡張子として見つけたファイル数
- `selected`: 出力した棋譜行数
- `skipped_year`: 開始年・終了年の条件で除外した対局数
- `skipped_date`: 開始日・終了日の条件で除外した対局数
- `skipped_finalist`: 決勝出場ソフト条件で除外した対局数
- `skipped_name`: プレイヤー名フィルタで除外した対局数
- `skipped_rating`: rating 条件で除外した対局数
- `skipped_reversal`: 逆転棋譜条件で除外した対局数
- `skipped_handicap`: 駒落ち除外条件で除外した対局数
- `skipped_parse`: パース不能、手なし、非 startpos などで除外したファイル数
- `skipped_duplicate`: 重複した position 行として除外した対局数
