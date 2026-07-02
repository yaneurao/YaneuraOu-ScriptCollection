# YaneuraOu-Builder GUI 構想

このメモは、`YaneuraOu-Builder/refs/` に分散しているビルド作業を Python GUI から統一的に扱うための構想である。

## 目的

GUI の目的は、単に既存スクリプトをボタン化することではない。

次の作業を同じ「ビルドレシピ」と「ビルドキュー」で扱えるようにする。

- 頒布用やねうら王パッケージの作成
- `YO-MATERIAL.exe` の作成
- SPSA tune 用実行ファイルの作成
- SPSA apply 後ソースからの実行ファイル作成
- Windows x64 / Windows x86 / Windows arm / macOS 向け script 生成
- ローカルで実行できるものは GUI から実行
- 別環境で実行するものは script と manifest を生成

## 基本方針

GUI は「設定を選ぶ画面」と「実際に走るコマンド」を分離する。

内部的には、すべてを次の3層で扱う。

| 層 | 役割 |
|---|---|
| Build Recipe | 何を作るか。version、source、edition、CPU、SPSA、package設定など。 |
| Build Plan | recipe から展開された具体的な job 一覧。`make clean`、`make`、copy、package など。 |
| Build Run | 実行結果。ログ、終了コード、生成物、所要時間、使用した git commit。 |

これにより、GUI 上で「生成されるコマンドを確認してから実行」「script だけ出力」「失敗 job だけ再実行」ができる。

## 推奨 GUI 技術

Python 標準に寄せるなら `tkinter` + `ttk` が妥当である。

理由:

- KifManager ですでに tkinter ベースの GUI 実装がある。
- Windows / macOS で追加依存が少ない。
- ビルドツールの GUI としては、派手な描画より安定したフォーム・表・ログ表示が重要。

ただし、長時間プロセスを扱うため、ビルド実行は必ず worker thread / subprocess に分離する。GUI thread で `make` を直接待たない。

## 画面構成

### 1. Recipes

ビルド種別を選ぶ画面。

プリセット:

- `Release all`
- `YO-MATERIAL`
- `SPSA tune`
- `SPSA apply`

各 preset は Release Build 画面の設定に展開して編集可能にする。最終的には JSON に保存する。
これらは削除不可の組み込みではなく、保存済みpresetが空のときだけ初期投入される通常presetとして扱う。

### 2. Matrix

`Release all` 用の matrix 編集画面。

項目:

- version
- DEV / Git の有無
- platform
- やねうら王 source folder
- CPU target
- evaluation edition
- compiler
- jobs
- extra cpp flags
- package name
- MSYS2 root
- SPSA preprocessing

UI:

- platform は `Windows x64`, `Windows x86`, `Windows arm`, `macOS` から選ぶ combo box
- source folder は text box と folder 選択 button
- CPU target は選択中 platform に対応する checkbox group
- evaluation edition はコード側の固定一覧から作る checkbox group
- edition 行には `YANEURAOU_EDITION` と出力 prefix を持たせる
- 保存済み preset / recipe は固定Edition一覧を増減させず、各行のON/OFF状態だけを復元する
- 重複行は警告表示
- build target は `tournament` 固定で、GUI 上では選択させない
- Windows platform では、生成した script を MSYS2 の環境で実行する button を用意する。Windows x64 は `MSYSTEM=MINGW64` の `clang++`、Windows x86 は `MSYSTEM=MINGW64` から `clang++ --target=i686-w64-windows-gnu`、Windows arm は `/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++` を使う。
- macOS platform では、生成した script を GUI から直接 subprocess として実行する button を用意する。実行中は script 実行 button を無効化し、stdout / stderr を Logs に流す。
- macOS platform の既定パスは、Windows側でビルド済みのフォルダを `/winbuild` に見せる前提にする。source folder は `/winbuild/source`、SPSA 関連ファイルは `/winbuild/tune.py`、`/winbuild/ParamLib.py`、`/winbuild/YaneuraOuV950.tune`、`/winbuild/YaneuraOuV950.params` を使う。
- Windows arm の GUI 対応範囲は、現時点では x64 Windows + MSYS2 `MINGW64` からの cross build に限定する。CPU target は `ARMV8` と `ARMV8_DOTPROD` で、script 生成時に pthread include path、lld 前提のcross compiler、MSYS2 cross CRT の不足回避 stub を自動で組み込む。詳細は [current-build-flow.md](current-build-flow.md) の Windows ARM 調査メモを参照する。
- SPSA tune/apply は別タブを作らず、Release Build 画面の preprocessing 設定として扱う
- 小さい画面では `Small Window` で compact geometry に切り替えられる。Recipe page は縦scroll可能にし、macOS の小さめのdisplayでも下部設定へ到達できるようにする。

### 3. Special Builds

`YO-MATERIAL`、`SPSA tune`、`SPSA apply` のような特殊ビルドも、別画面にはしない。preset を読み込むと Release Build 画面の platform、source、CPU、edition、SPSA preprocessing に展開する。

#### YO-MATERIAL

入力:

- source directory
- output path
- version
- target CPU
- material level
- hash key bits
- TT cluster size

デフォルト:

- edition: `YANEURAOU_ENGINE_MATERIAL`
- target: `tournament`
- CPU: `AVX2`
- material level: `9`
- output: `YO-MATERIAL.exe`

#### SPSA tune

入力:

- base source directory
- work directory
- `tune.py`
- `ParamLib.py`
- `.tune`
- `.params`
- tune command mode: `tune`
- edition
- target CPU
- output exe
- version

処理:

1. work directory を作る
2. source をコピー
3. SPSA 関連ファイルをコピー
4. `python3 tune.py tune ...` を実行
5. Makefile build
6. artifact を指定先へコピー

#### SPSA apply

SPSA tune とほぼ同じだが、tune command mode は `apply`。

SPSA 適用後ソースを使って、通常の頒布パッケージ matrix に流し込めるようにするのが重要である。つまり `apply` は最終成果物ではなく、「派生 source directory を作る前処理」としても扱えるようにする。

### 5. Build Plan

recipe から展開した job 一覧を確認する画面。

列:

- status
- platform
- edition
- CPU
- target
- output name
- command
- work directory
- estimated artifact path

操作:

- plan 作成
- script 出力
- 選択 job だけ実行
- 失敗 job だけ再実行
- command コピー
- dry run

ここで初めて「何が実行されるか」をユーザーが確認できる。

### 6. Runner / Logs

ビルド中の画面。

表示:

- 現在実行中 job
- 全体進捗
- job ごとの経過時間
- stdout / stderr
- エラーの先頭と末尾
- 生成された artifact

ログは GUI 内表示だけでなく、run directory に保存する。

推奨構成:

```text
YaneuraOu-Builder/runs/20260616-031500-release-all/
  recipe.json
  plan.json
  env.json
  logs/
    0001-YANEURAOU_ENGINE_NNUE-AVX2.log
  artifacts/
  package/
```

### 7. Artifacts / Package

ビルド済み成果物を確認して、頒布用パッケージを作る画面。

表示:

- artifact path
- file size
- SHA256
- build recipe
- source git commit
- version
- CPU target
- edition

操作:

- package 作成
- package 内容確認
- `obj/` 除外
- script / manifest を同梱
- checksums 生成

## Build recipe のデータモデル

最小限の構造例:

```json
{
  "name": "release-all",
  "version": "V9.40",
  "source_dirs": {
    "win": "D:/doc/VSCodeProject/YaneuraOu/YaneuraOu-GitHub/YaneuraOu/source"
  },
  "platforms": ["win64"],
  "variants": [
    {"name": "DEV", "extra_cppflags": ["-DUSE_LAZY_EVALUATE"]},
    {"name": "Git", "extra_cppflags": []}
  ],
  "target": "tournament",
  "compiler": "clang++",
  "jobs": 8,
  "common_cppflags": [
    "-DHASH_KEY_BITS=128",
    "-DTT_CLUSTER_SIZE=4"
  ],
  "editions": [
    {
      "edition": "YANEURAOU_ENGINE_NNUE",
      "artifact_prefix": "YaneuraOu_NNUE_halfkp_256x2_32_32"
    }
  ],
  "cpus": {
    "win64": ["SSE41", "SSE42", "AVX2", "ZEN1", "ZEN2", "AVXVNNI", "AVX512", "AVX512VNNI"]
  },
  "package": {
    "enabled": true,
    "format": "7z",
    "exclude": ["obj"]
  }
}
```

GUI はこの recipe を編集し、Build Plan に展開する。内部形式では platform は list だが、MVP の GUI では platform combo box から1つだけ選んで recipe を生成する。

## 実行 backend

現状の運用を考えると、backend は複数必要である。

| backend | 用途 |
|---|---|
| Local shell | GUI を起動した環境で直接 `make` できる場合。 |
| MSYS2 shell | Windows で MSYS2 の `clang++`, `make`, `7z` を使う場合。 |
| Script generation only | macOS や別PCで実行するための script を生成する場合。 |
| Remote/SSH | 将来的に別マシンでビルドする場合。初期実装では不要。 |

最初から remote 実行まで作る必要はない。まずは「script generation only」と「local shell」を堅く作るのがよい。

## Validation

GUI では、実行前に以下を検査する。

- source directory に `Makefile` があるか
- `YANEURAOU_EDITION` が空でないか
- CPU target が Makefile の既知 target か
- version が空でないか
- output path が重複していないか
- edition/artifact prefix の重複がないか
- `7z` が必要なときに存在するか
- SPSA tune/apply の `.tune`, `.params`, `tune.py`, `ParamLib.py` が存在するか
- work directory が削除対象になる場合、確認が必要か
- git dirty state を成果物 manifest に記録したか

## 生成する script の方針

GUI から script を出す場合、既存の `my-uraou-dev-win64` 形式を維持しつつ、次を改善する。

- platform に応じた comment 記法を使う
  - Windows/MSYS2/macOS shell: `#`
  - Windows cmd: `REM`
- `mkdir -p` を使う
- `set -e` の有無を選べるようにする
- source folder、work directory、output path など、単体実行に必要な値をscript内の変数として埋め込む
- script自身の場所から run directory を決め、どのcurrent directoryから起動しても同じ場所に出力する
- source copy を行うか、既存 `build/source` を使うかを選べるようにする
- build manifest を出力する
- job ごとに log file を分ける
- package 作成前に artifact 数を検証する

## MVP

最初に作るべき最小構成:

1. `refs` 相当の preset recipe を GUI に表示する。
2. version、source folder、platform、CPU、edition をフォームで選べる。
3. Build Plan を表で表示する。
4. script generation only で `my-uraou-*.sh` を生成する。
5. `YO-MATERIAL` を single build recipe として生成できる。
6. SPSA apply を「source 前処理 + Release Build」として生成できる。
7. 生成された script と recipe JSON を同じ run directory に保存する。

この段階では GUI から直接長時間 build を実行しなくてもよい。現在の運用を壊さず、まず script 生成を統一する。

## 次段階

MVP の次に追加する機能:

- GUI から local build 実行
- job 単位のログ表示
- 失敗 job の再実行
- artifact checksum 生成
- package 作成
- `YaneuraOu/source/Makefile` から edition / CPU 候補を自動抽出
- SPSA tune/apply の param preset 管理
- run history の検索

## 画面イメージ

構成は次のようなタブ UI がよい。

```text
[Recipe] [Build Plan] [Logs]
```

重要なのは、ユーザーが「何を作るか」と「実際にどのコマンドが走るか」を同時に確認できることである。

特に頒布用 package は、build matrix が大きいため、いきなり実行ボタンを押す UI にはしない。必ず Build Plan を生成し、件数、出力名、重複、未設定値、警告を確認してから実行または script 出力する。

## 設計上の注意

- GUI の内部状態を Python オブジェクトだけに閉じ込めない。recipe / plan / run result はファイルに残す。
- 既存の手書きスクリプトは、少なくとも初期段階では「互換出力」として生成できるようにする。
- ビルド成果物には、必ず `ENGINE_VERSION`、source git commit、dirty state、recipe hash を記録する。
- SPSA apply 後ソースから release-all を作る流れを標準 workflow として扱う。
- `YO-MATERIAL.exe` は補助ツールだが、頒布パッケージ作成では必須成果物として扱えるようにする。
