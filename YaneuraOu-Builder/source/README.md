# yaneuraou-build source

このディレクトリには、やねうら王のビルドscriptを生成・実行するための
Python GUI実装があります。

## 配置とGUIの起動

`yaneuraou-build` は `YaneuraOu-ScriptCollection/yaneuraou-build` に配置します。
新規presetの初期パスは、`yaneuraou-build` の位置から
近くの `YaneuraOu/source`、`SPSA`、`BookMinerCpp/source` を探して設定します。

`yaneuraou-build` フォルダから起動します。

```bash
python3 source/yobuild_gui.py
```

Windowsで python.org 版Pythonを使っている場合は、次でも構いません。

```bat
py -3 source\yobuild_gui.py
```

## GUIなしでpresetを生成する

GUIを開かずにpresetからscriptを生成できます。

```bash
python3 source/yobuild_gui.py --generate-preset release-all
python3 source/yobuild_gui.py --generate-preset yo-material
python3 source/yobuild_gui.py --generate-preset spsa-apply
```

## 生成されるrun

GUIで編集したrecipeは、script出力時にbuild planへ展開されます。生成runには
shell scriptと次のJSONが出力されます。

- `recipe.json`
- `plan.json`
- `manifest.json`

生成scriptには source folder、work folder、output path など、単体実行に必要な値を
埋め込みます。また、script自身の場所からrun directoryを決めるため、GUIなしでも
実行できます。

各run directoryには `run-all` も生成されます。

```bash
cd runs/<run-name>
./run-all
```

## platformごとの実行

Windows platformでは、GUIが対象platformに応じたMSYS2環境を選びます。

| platform | MSYS2 environment |
|---|---|
| Windows x64 | `MINGW64` |
| Windows x86 | `MINGW64` + MSYS2 i686 cross clang |
| Windows arm | `MINGW64` + MSYS2 aarch64 cross clang |

Windows armでは `ARMV8` / `ARMV8_DOTPROD` を生成対象にできます。標準NNUEの
成果物名は、例えば次のようになります。

```text
YaneuraOu_NNUE-V941DEV_ARMV8.exe
YaneuraOu_NNUE-V941DEV_ARMV8_DOTPROD.exe
```

macOS platformでは `Run Direct` を使い、MSYS2を介さずに生成scriptを子プロセスとして
直接実行します。macOS presetの既定パスは、Windows側でビルド済みのtreeを
`/winbuild` に見せる前提です。

```text
/winbuild/source
/winbuild/tune.py
/winbuild/ParamLib.py
/winbuild/YaneuraOuV950.tune
/winbuild/YaneuraOuV950.params
```

## BookMinerCpp build

`bookminer-cpp` Preset を選択して、通常の `Write Script` / `Run with MSYS2`
からビルドします。

- `Platform` と `CPU targets` は通常通り使います。
- `Variants`、`Editions`、`SPSA` は BookMinerCpp build では使いません。
- `YaneuraOu source folder` は、BookMinerCppが参照するやねうら王 `source`
  フォルダです。
- `YaneuraOu-ScriptCollection/BookMinerCpp/source` は既定配置から自動検出します。
- 成果物は run フォルダ内の `build/` に `BookMinerCpp-AVX2.exe` のような名前で出力されます。中間ファイルは `build-intermediate/bookminer-cpp/` に作られます。
- Windows ARMのBookMinerCpp buildは未対応です。

## GUI設定

Release画面では、次を設定できます。

- platform
- YaneuraOu source folder
- CPU target
- edition
- variant
- package設定
- optional SPSA preprocessing
- optional PGO

Edition一覧は `YaneuraOu-Builder/arch-list.txt` から作ります。1行にarchを1つ記載し、
空行と `#` から始まるコメント行は無視します。記載順に表示し、初回の標準presetではONです。
保存済みpresetに新しく追加されたarchは、意図しないビルドを避けるためOFFで表示します。
保存済みpresetからは同じeditionのON/OFFだけを復元し、削除したarchは表示しません。
editionは `YANEURAOU_ENGINE_`、出力名の接頭辞は `YaneuraOu_` をarchに付けて生成します。
例外はなく、`MATERIAL` の出力名も `YaneuraOu_MATERIAL` になります。
`NNUE` ではなく `NNUE_HALFKP_256X2_32_32` のように指定してください。
重複（大小文字違いも含む）、不正な記述、空の一覧はエラーになります。
arch追加後はGUIを再起動してください。対象のやねうら王ソースがそのarchに対応している必要があります。

variant名 `DEV` と `Git` は固定です。保存済みpresetは、これら固定行のON/OFF状態と
extra flagsだけを復元します。

GUI設定とpresetは `source/yobuild_gui.pickle` に保存されます。各runにも
再現用の `recipe.json` は出力されますが、通常のGUI運用ではJSONを直接開閉する必要は
ありません。

初期4presetは組み込み固定ではなく、保存済みpreset一覧が空のときだけ作られる通常presetです。
そのため、renameやdeleteができます。

Recipe 画面の左側に preset 一覧が表示され、クリックで切り替えられます。切り替える前の編集内容は保存されます。一覧はスクロールできます。一覧上部の ↑ / ↓ で選択中の preset を移動でき、並び順は次回起動時にも保持されます。

`Open Build Folder` は、起動後に最後に生成した run の `build` フォルダを開きます。`Write Script` やビルド開始時に空の `build` フォルダを作るため、ビルド中や失敗後も開けます。

Release settings のテキスト欄や選択欄を右クリックすると、項目の値を他の preset 用にコピーできます。別の preset を一覧から選び、同じ欄を右クリックして `Paste from ...` を選ぶと、表示されているコピー元の値を貼り付けます。コピーした値は項目ごとに保持され、アプリを終了すると消えます。macOS では Control キーを押しながらのクリックでもメニューを開けます。

### PGO

`Release settings` の `PGO` タブで `Enable PGO (tournament)` をONにすると、計測用ビルド、benchによる訓練、プロファイル集約、最適化ビルドを順に実行します。両段階とも `tournament` でビルドします。Variantsの追加フラグとSPSAの前処理も通常どおり反映します。

- `Eval folder template` を1つ指定します。既定の `eval/[arch]` は、たとえば `eval/SFNN_halfkahm2_2304_15_64_k9k9_progress8` に展開されます。`[arch]` はedition名から `YANEURAOU_ENGINE_` を除いた名前です。相対パスは、指定した元の `source` フォルダの親（やねうら王のルート）を基準にします。絶対パスや `[YOSC]/eval/[arch]` も使えます。NNUE系は対応する評価関数ファイルをそこに配置してください。MATERIALでは不要です。
- `Bench arguments` の既定値は `1024 1 14 default depth` です。訓練時のHash、スレッド数、深さなどを指定できます。定跡は訓練時に無効にします。
- Clangでは対応する `llvm-profdata` が必要です。自動検出できない場合は、そのパスを指定してください。GCCでは不要です。
- MSYS2 MINGW64のClangではPGOランタイムも必要です。`pacman -S mingw-w64-x86_64-compiler-rt` で導入できます（[パッケージ](https://packages.msys2.org/packages/mingw-w64-x86_64-compiler-rt)）。ビルド前に小さなプログラムでPGOのリンク可否を確認し、失敗した場合はログに原因を表示します。PGOライブラリ不足を検知した場合は、ビルド失敗のダイアログにも導入コマンドを表示します。
- パス欄では `[YOSC]` などの共通マクロを使えます。設定はpresetに保存します。評価関数ファイルは訓練時に読み込み、成果物には同梱しません。

PGOは生成したエンジンを実行して訓練するため、CPU targetを実行できる環境で使ってください。Windows ARMへのクロスビルドでは使用できません。BookMinerCppではPGO設定を使いません。

対象のやねうら王には、更新済みの `source/Makefile` と `tools/build/pgo.py` が必要です。Builderはビルド用コピーにこの補助スクリプトも配置します。単体のmakeでも `PGO_EVAL_DIR=...` を指定して `make pgo` を実行できます。詳しい変数は、やねうら王の `tools/build/README.md` を参照してください。

ログタブでは `[PGO] START:` を青い背景、`[PGO] DONE:` を緑の背景、エラーを赤い背景で表示します。変更を反映するにはBuilderを再起動してスクリプトを生成し直してください。以前に生成したrunには古いスクリプトが残ります。

### 共通マクロ

画面上部の `Macros` タブに、全 preset で共通に使う値を名前とともに登録できます。例えば `YOSC` に `C:\Users\yaneen\CA-misc\work\YaneuraOuWork\YaneuraOu-ScriptCollection` を登録すると、tune.py 欄に `[YOSC]\SPSA\tune.py` と書けます。同様に `YaneuraOuSource` などの名前を追加し、各パス欄で `[YaneuraOuSource]` を使えます。例えば `params-file` に `example.params` を登録すれば、`[YOSC]\SPSA\param\[params-file]` のように複数のマクロを組み合わせられます。パス先頭のフォルダ用マクロは展開結果を絶対パスにしてください。名前は英字または `_` で始まり、英数字、`_`、`-` を使います。

`DEV_OPTIONS` に `-DFOO=1 -DBAR=2` のような文字列を登録すると、Variants の `Extra CPP flags` や `Common CPP flags` に `[DEV_OPTIONS]` と書けます。`Engine name` と `Engine version` にもマクロを使えます。これらのマクロ値には別のマクロも使えます。Macros の各行の ↑ / ↓ で表示順を変更でき、その順番は保存されます。

設定欄には短い表記が保存され、ビルド計画やスクリプトの生成時に登録値へ展開されます。登録値を変更すると、次に生成するビルドから新しい値が使われます。未登録のマクロ、循環参照、またはパス先頭のマクロが絶対パスに展開されないと、生成前にエラーを表示します。すでに生成されたスクリプトは変更されません。

### ShogiZone へのアップロード

ShogiZone 側で Builder 用 upload password を設定します。対応 API を含む ShogiZone に更新したうえで、YO-Build の画面上部の `ShogiZone` タブにサーバー URL と同じ password を設定してください。`Test connection` でサーバーへの接続とパスワードの認証を確認できます。URL と password は全 preset 共通で、パスワードはローカルの GUI 設定ファイルに平文で保存されます。外部ネットワークでは HTTPS を使ってください。

各 preset の `Release settings` で `Engine name` を入力し、`Upload to ShogiZone after build` を ON にすると、Windows 向け `Run with MSYS2` がすべて成功した後に自動アップロードします。`Write Script` で生成したスクリプトを GUI の外で実行した場合は自動アップロードされません。

エンジン本体は `入力名-Edition-Variant` ごとに登録され、各 CPU target の実行ファイルはその本体にまとめられます。たとえば `MyEngine-MATERIAL-Git` のようになります。同名または同じ保存フォルダの本体が既にある場合は上書きせずエラーにします。通信や登録が途中で失敗すると、送信済みのファイルや登録済みの本体が残る場合があります。エンジン本体を利用するための「エンジン登録」は ShogiZone の管理画面で別途作成してください。
