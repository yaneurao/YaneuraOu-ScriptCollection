# はじめに

`YaneuraOu-Builder` は、GUIでビルド設定を編集し、具体的なbuild planに展開して、
再現用の `recipe.json` / `plan.json` / `manifest.json` と実行scriptを
`runs/` 以下に出力します。

## GUIを起動する

`YaneuraOu-ScriptCollection/YaneuraOu-Builder` ディレクトリで起動します。

```bash
python3 source/yobuild_gui.py
```

Windowsで python.org 版Pythonを使っている場合は、次でも構いません。

```bat
py -3 source\yobuild_gui.py
```

## run directory

生成されたrunは次のようなフォルダに出力されます。

```text
runs/<timestamp>-<run-name>-<platform>/
```

各runには、次のファイルが入ります。

- `recipe.json`
- `plan.json`
- `manifest.json`
- `scripts/`
- `run-all`

手動実行する場合は、run directoryで `run-all` を実行します。

```bash
cd runs/<run>
./run-all
```

## ローカル作業用ディレクトリ

`refs/` と `runs/` はGit管理対象外です。参照用ファイル、ログ、生成script、
ビルド成果物を置くローカル作業用ディレクトリとして扱います。

Windows ARM版のビルド手順は [Windows ARM ビルドチュートリアル](windows-arm.md)、
BookMinerCppのビルド手順は [BookMinerCpp ビルドチュートリアル](bookminer-cpp.md)
を参照してください。
