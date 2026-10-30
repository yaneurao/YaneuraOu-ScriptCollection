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
- 成果物はrunフォルダ内の `artifacts/` に `BookMinerCpp-AVX2.exe` のような名前で出力されます。
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

Edition一覧は `YaneuraOu-Builder/arch-list.txt` から作ります。1行にarchを1つ記載し、
空行と `#` から始まるコメント行は無視します。記載順に表示し、初回・新規追加archはONです。
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

Recipe 画面の左側に preset 一覧が表示され、クリックで切り替えられます。切り替える前の編集内容は保存されます。一覧はスクロールできます。

Release settings のテキスト欄や選択欄を右クリックすると、項目の値を他の preset 用にコピーできます。別の preset を一覧から選び、同じ欄を右クリックして `Paste from ...` を選ぶと、表示されているコピー元の値を貼り付けます。コピーした値は項目ごとに保持され、アプリを終了すると消えます。macOS では Control キーを押しながらのクリックでもメニューを開けます。

### 共通パス

画面上部の `Paths` タブに、全 preset で共通に使うフォルダを名前と絶対パスで登録できます。例えば `YOSC` に `C:\Users\yaneen\CA-misc\work\YaneuraOuWork\YaneuraOu-ScriptCollection` を登録すると、tune.py 欄に `[YOSC]\SPSA\tune.py` と書けます。同様に `YaneuraOuSource` などの名前を追加し、各パス欄の先頭で `[YaneuraOuSource]` を使えます。名前は英字または `_` で始まり、英数字と `_` を使います。

設定欄には短い表記が保存され、ビルド計画やスクリプトの生成時に実パスへ展開されます。登録先を変更すると、次に生成するビルドから新しいパスが使われます。登録されていない名前や絶対パスでない登録先を使うと、生成前にエラーを表示します。すでに生成されたスクリプトは変更されません。

### ShogiZone へのアップロード

ShogiZone 側で Builder 用 upload password を設定します。対応 API を含む ShogiZone に更新したうえで、YO-Build の画面上部の `ShogiZone` タブにサーバー URL と同じ password を設定してください。`Test connection` でサーバーへの接続とパスワードの認証を確認できます。URL と password は全 preset 共通で、パスワードはローカルの GUI 設定ファイルに平文で保存されます。外部ネットワークでは HTTPS を使ってください。

各 preset の `Release settings` で `Engine name` を入力し、`Upload to ShogiZone after build` を ON にすると、Windows 向け `Run with MSYS2` がすべて成功した後に自動アップロードします。`Write Script` で生成したスクリプトを GUI の外で実行した場合は自動アップロードされません。

エンジン本体は `入力名-Edition-Variant` ごとに登録され、各 CPU target の実行ファイルはその本体にまとめられます。たとえば `MyEngine-MATERIAL-Git` のようになります。同名または同じ保存フォルダの本体が既にある場合は上書きせずエラーにします。通信や登録が途中で失敗すると、送信済みのファイルや登録済みの本体が残る場合があります。エンジン本体を利用するための「エンジン登録」は ShogiZone の管理画面で別途作成してください。
