# Windows x64 / x86 ビルドチュートリアル

このチュートリアルでは、YaneuraOu-Builderを使ってWindows x64版、Windows x86版の
やねうら王実行ファイルをビルドする手順を説明します。

YaneuraOu-Builderでは次のplatformを使います。

| 対象 | YaneuraOu-Builder platform | MSYS2 launcher | `MSYSTEM` |
|---|---|---|---|
| Windows x64 | `Windows x64` | `C:\msys64\mingw64.exe` | `MINGW64` |
| Windows x86 | `Windows x86` | `C:\msys64\mingw32.exe` | `MINGW32` |

MSYS2はenvironmentごとにPATHとpackage prefixが分かれています。x64用は
`MINGW64`、x86用は `MINGW32` で作業してください。

参考:

- <https://www.msys2.org/docs/environments/>
- <https://packages.msys2.org/package/mingw-w64-x86_64-clang>
- <https://packages.msys2.org/package/mingw-w64-x86_64-lld>
- <https://packages.msys2.org/package/mingw-w64-x86_64-winpthreads>
- <https://packages.msys2.org/package/mingw-w64-i686-clang>
- <https://packages.msys2.org/package/mingw-w64-i686-lld>
- <https://packages.msys2.org/package/mingw-w64-i686-winpthreads>
- <https://packages.msys2.org/package/make>

## 1. MSYS2をインストールする

MSYS2を通常通り `C:\msys64` にインストールします。

YaneuraOu-Builderの `Run with MSYS2` は、platformに応じて次のshellを使います。

- Windows x64: `C:\msys64\mingw64.exe`
- Windows x86: `C:\msys64\mingw32.exe`

`MSYS2 root` を変更している場合は、GUI上の `MSYS2 root` にそのインストール先を
指定してください。

## 2. 共通packageを更新する

まず `MINGW64` shellを開きます。

```bash
pacman -Syu
```

途中でMSYS2の再起動を求められた場合はshellを閉じ、再度 `MINGW64` shellを開いて
次を実行します。

```bash
pacman -Syu
```

## 3. Windows x64用packageを入れる

`MINGW64` shellで実行します。

```bash
pacman -S --needed git make python p7zip \
  mingw-w64-x86_64-clang \
  mingw-w64-x86_64-lld \
  mingw-w64-x86_64-winpthreads
```

確認します。

```bash
echo $MSYSTEM
which clang++
clang++ --version
which lld
which make
which 7z
```

`echo $MSYSTEM` は `MINGW64` になっている必要があります。

## 4. Windows x86用packageを入れる

x86版もビルドする場合は、`MINGW32` shellを開いて実行します。

```bash
pacman -S --needed git make python p7zip \
  mingw-w64-i686-clang \
  mingw-w64-i686-lld \
  mingw-w64-i686-winpthreads
```

確認します。

```bash
echo $MSYSTEM
which clang++
clang++ --version
which lld
which make
which 7z
```

`echo $MSYSTEM` は `MINGW32` になっている必要があります。

## 5. YaneuraOu-Builderを起動する

`YaneuraOu-ScriptCollection/YaneuraOu-Builder` ディレクトリで起動します。

```bash
python3 source/yobuild_gui.py
```

WindowsのPythonを使う場合:

```bat
py -3 source\yobuild_gui.py
```

## 6. Windows x64をビルドする

GUIで次のように設定します。

1. 既存presetを選ぶか、必要ならpresetをcloneします。
2. `Engine version` を `V9.41` など目的のバージョンにします。
3. `Platform` を `Windows x64` にします。
4. `YaneuraOu source folder` に、やねうら王の `source` ディレクトリを指定します。
5. `Compiler` は通常 `clang++` にします。
6. `MSYS2 root` が通常は `C:\msys64` になっていることを確認します。
7. `Variants` で必要なvariantをONにします。
8. `CPU targets` で必要なCPU targetをONにします。
9. `Editions` で必要なeditionをONにします。

x64でよく使うCPU target例:

- `SSE41`
- `SSE42`
- `AVX2`
- `ZEN1`
- `ZEN2`
- `AVXVNNI`
- `AVX512`
- `AVX512VNNI`

設定後、`Run with MSYS2` を押します。YaneuraOu-Builderは `mingw64.exe` 相当の環境、
つまり `MSYSTEM=MINGW64` で生成scriptを実行します。

## 7. Windows x86をビルドする

GUIで次のように設定します。

1. 既存presetを選ぶか、必要ならpresetをcloneします。
2. `Platform` を `Windows x86` にします。
3. `YaneuraOu source folder` に、やねうら王の `source` ディレクトリを指定します。
4. `Compiler` は通常 `clang++` にします。
5. `MSYS2 root` が通常は `C:\msys64` になっていることを確認します。
6. `CPU targets` と `Editions` を必要に応じて選びます。

設定後、`Run with MSYS2` を押します。YaneuraOu-Builderは `mingw32.exe` 相当の環境、
つまり `MSYSTEM=MINGW32` で生成scriptを実行します。

## 8. 出力を確認する

run directoryは次のような名前で作られます。

```text
runs/20260617-193803-release-all-win64/
runs/20260617-193803-release-all-win32/
```

生成scriptは次の場所にあります。

```text
runs/<run>/scripts/my-uraou-dev-win64
runs/<run>/scripts/my-uraou-dev-win32
```

手動実行する場合は、run directoryで `run-all` を実行します。

```bash
cd runs/<run>
./run-all
```

ビルド成果物はrun内の `build/<edition-dir>/` 以下に出ます。package作成をONにしている
場合、packageは `build/` に作られます。

例:

```text
runs/<run>/build/yaneuraou-V941-dev-win64-all.7z
runs/<run>/build/yaneuraou-V941-dev-win32-all.7z
```

## 9. トラブルシュート

`clang++` が見つからない場合は、ビルド対象に対応するshellでpackageを入れたか確認します。

```bash
echo $MSYSTEM
which clang++
```

x64ビルドなら `MINGW64`、x86ビルドなら `MINGW32` である必要があります。

link時に `lld` が見つからない場合は、対象environment側で次を入れてください。

```bash
# MINGW64
pacman -S --needed mingw-w64-x86_64-lld

# MINGW32
pacman -S --needed mingw-w64-i686-lld
```

`7z` が見つからない場合は、次を入れてください。

```bash
pacman -S --needed p7zip
```

GUIから `Run with MSYS2` を押したときに意図しない環境で動いているように見える場合は、
Logsに出る `MSYSTEM=...` を確認してください。
