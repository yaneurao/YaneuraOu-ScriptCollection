# BookMinerCpp ビルドチュートリアル

このチュートリアルでは、`YaneuraOu-Builder` の `bookminer-cpp` presetを使って
`BookMinerCpp.exe` をビルドする手順を説明します。

BookMinerCppは、`YaneuraOu-ScriptCollection/BookMinerCpp/source` のソースと、
やねうら王本体の `source` ディレクトリを使ってビルドします。

## 1. MSYS2を用意する

Windows x64でビルドする場合は、MSYS2の `MINGW64` 環境を使います。

```bash
pacman -Syu
pacman -S --needed git make python \
  mingw-w64-x86_64-clang \
  mingw-w64-x86_64-lld \
  mingw-w64-x86_64-winpthreads
```

x86版を作る場合は、`MINGW32` 環境で対応するpackageを入れます。

```bash
pacman -S --needed git make python \
  mingw-w64-i686-clang \
  mingw-w64-i686-lld \
  mingw-w64-i686-winpthreads
```

## 2. GUIを起動する

`YaneuraOu-ScriptCollection/YaneuraOu-Builder` ディレクトリで起動します。

```bash
python3 source/yobuild_gui.py
```

WindowsのPythonを使う場合:

```bat
py -3 source\yobuild_gui.py
```

## 3. bookminer-cpp presetを選ぶ

GUI上部の `Preset` で `bookminer-cpp` を選びます。

BookMinerCpp buildでは次を使います。

- `Engine version`
- `Platform`
- `YaneuraOu source folder`
- `Compiler`
- `Common CPP flags`
- `MSYS2 root`
- `CPU targets`

次は使いません。

- `Variants`
- `Editions`
- `SPSA preprocessing`

## 4. 設定する

代表的な設定は次の通りです。

| 項目 | 例 |
|---|---|
| `Engine version` | `V9.41` |
| `Platform` | `Windows x64` |
| `YaneuraOu source folder` | `C:\path\to\YaneuraOu\source` |
| `Compiler` | `clang++` |
| `Common CPP flags` | 空欄、または必要な `-D...` |
| `MSYS2 root` | `C:\msys64` |
| `CPU targets` | `AVX2` / `AVX512VNNI` など |

`YaneuraOu source folder` は、やねうら王本体の `Makefile` がある `source`
ディレクトリです。

`BookMinerCpp/source` は、YOSC内の既定配置から自動検出します。

## 5. ビルドする

GUIの `Run with MSYS2` を押します。

先にscriptだけ確認したい場合は `Write Script` を押します。生成されたrun directoryの
`run-all` を手動実行することもできます。

```bash
cd runs/<run>
./run-all
```

## 6. 出力を確認する

成果物はrun directory内の `artifacts/` に出力されます。

例:

```text
runs/<run>/artifacts/BookMinerCpp-AVX2.exe
runs/<run>/artifacts/BookMinerCpp-AVX512VNNI.exe
```

`BookMinerCpp.exe` を実行するには、`BookMiner/YO-MATERIAL.exe` が必要です。
`YO-MATERIAL.exe` は自分でMATERIAL版をビルドするか、やねうら王News Letterで
頒布されている最新のMATERIAL版実行ファイルを `YO-MATERIAL.exe` にリネームして
`BookMiner/` に配置してください。

## 7. トラブルシュート

`clang++` が見つからない場合は、対象platformに対応するMSYS2環境でpackageを入れたか
確認します。

```bash
echo $MSYSTEM
which clang++
```

Windows x64なら `MINGW64`、Windows x86なら `MINGW32` である必要があります。

起動時に `engine not found` が出る場合は、`BookMiner/YO-MATERIAL.exe` の配置を
確認してください。
