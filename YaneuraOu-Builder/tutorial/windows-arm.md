# Windows ARM ビルドチュートリアル

このチュートリアルでは、YaneuraOu-Builderを使ってWindows ARM64版やねうら王の
実行ファイルを作る手順を説明します。

現在扱うCPU targetは次の2つです。

- `ARMV8`: ARMv8 + NEON
- `ARMV8_DOTPROD`: ARMv8.2 dot product

`Engine version = V9.41`、`DEV` variant、標準NNUE editionの場合、期待する
出力名は次の通りです。

```text
YaneuraOu_NNUE-V941DEV_ARMV8.exe
YaneuraOu_NNUE-V941DEV_ARMV8_DOTPROD.exe
```

## 方法1: x64 Windows + MSYS2でビルドする

これは検証済みの手順です。

x64 Windows環境では `clangarm64.exe` を使いません。`clangarm64.exe` 側の
compilerはARM64 Windows用実行ファイルなので、x64 Windows上では
`cannot execute binary file` になります。YaneuraOu-BuilderのWindows ARMビルドは
`mingw64.exe` / `MSYSTEM=MINGW64` で実行し、MSYS2のaarch64 cross compilerを
使います。

### 1. MSYS2 packageを入れる

MSYS2の `MINGW64` shellを開き、必要なpackageを入れます。

```bash
pacman -Syu
pacman -S --needed git base-devel make python p7zip lld \
  mingw-w64-cross-clang-toolchain \
  mingw-w64-clang-aarch64-winpthreads
```

`pacman -Syu` の途中でMSYS2の再起動を求められた場合は、再起動後に2つ目の
コマンドを再実行してください。

環境を確認します。

```bash
echo $MSYSTEM
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++ --version
ls /clangarm64/include/pthread.h
ls /clangarm64/lib/libpthread.a
ls /usr/bin/ld.lld*
```

`echo $MSYSTEM` は `MINGW64` になっている必要があります。

### 2. YaneuraOu-Builderを起動する

`YaneuraOu-ScriptCollection/YaneuraOu-Builder` ディレクトリで起動します。

```bash
python3 source/yobuild_gui.py
```

WindowsのPythonを使う場合:

```bat
py -3 source\yobuild_gui.py
```

### 3. Recipeを設定する

GUIで次のように設定します。

1. 既存presetを選ぶか、必要ならpresetをcloneします。
2. `Engine version` を `V9.41` など目的のバージョンにします。
3. `Platform` を `Windows arm` にします。
4. `YaneuraOu source folder` に、やねうら王の `source` ディレクトリを指定します。
5. `MSYS2 root` が通常は `C:\msys64` になっていることを確認します。
6. `Variants` は、`V941DEV` という出力名が欲しい場合は `DEV` をONにします。
7. `CPU targets` は `ARMV8` と `ARMV8_DOTPROD` をONにします。
8. 2つの標準NNUE ARM実行ファイルだけが欲しい場合、`Editions` は
   `YANEURAOU_ENGINE_NNUE` だけをONにします。

`Platform` が `Windows arm` のとき、YaneuraOu-Builderはcompilerとして次を自動使用します。

```text
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++
```

また、次の指定も自動で追加します。

```text
-idirafter /clangarm64/include
EXTRA_LDFLAGS="./mingw_aarch64_ehandler_stub.o -L/clangarm64/lib"
```

生成scriptは、`make` の前に `mingw_aarch64_ehandler_stub.c` と
`mingw_aarch64_ehandler_stub.o` を自動生成します。

### 4. ビルドを実行する

`Run with MSYS2` を押します。

run directoryは次のような名前で作られます。

```text
runs/20260617-193803-release-arm-winarm/
```

生成scriptは次の場所にあります。

```text
runs/<run>/scripts/my-uraou-dev-winarm
```

手動実行する場合は、run directoryで `run-all` を実行します。

```bash
cd runs/<run>
./run-all
```

### 5. 出力を確認する

標準NNUE editionの場合、成果物はrun内の `build/NNUE` に出ます。

```text
runs/<run>/build/NNUE/YaneuraOu_NNUE-V941DEV_ARMV8.exe
runs/<run>/build/NNUE/YaneuraOu_NNUE-V941DEV_ARMV8_DOTPROD.exe
```

package作成をONにしている場合、packageは `build/` に作られます。

```text
runs/<run>/build/yaneuraou-V941-dev-winarm-all.7z
```

MSYS2上でbinary形式を確認できます。

```bash
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-objdump -f \
  build/NNUE/YaneuraOu_NNUE-V941DEV_ARMV8.exe
```

### トラブルシュート

`clang++` 実行時に `cannot execute binary file` が出る場合、x64 Windows上で
`CLANGARM64` 用binaryを実行しようとしている可能性があります。x64 Windowsでは
`MINGW64` shellとYaneuraOu-Builderの `Windows arm` platformを使ってください。

`pthread.h` が見つからない場合は、次を確認してください。

```bash
ls /clangarm64/include/pthread.h
```

link時に `ld.lld` が見つからない場合は、次を入れてください。

```bash
pacman -S --needed lld
```

libc++ headerで `<string.h>` や `<stddef.h>` が見つからないというエラーが出る場合、
`-idirafter /clangarm64/include` を `-I/clangarm64/include` に置き換えないで
ください。YaneuraOu-Builderはlibc++のheader探索順を壊さないために `-idirafter` を
使っています。

## 方法2: ARM Windows環境でビルドする

状態: **検証中**

これは実機のWindows ARM64環境でビルドするための想定手順です。現時点では、
この手順で最後までビルドできることはまだ検証していません。

想定する環境は MSYS2 ARM64 / `CLANGARM64` です。

```text
C:\msys64\clangarm64.exe
MSYSTEM=CLANGARM64
```

`CLANGARM64` shellで、必要と思われるpackageを入れます。

```bash
pacman -Syu
pacman -S --needed git base-devel make python p7zip lld \
  mingw-w64-clang-aarch64-clang \
  mingw-w64-clang-aarch64-lld \
  mingw-w64-clang-aarch64-winpthreads
```

`pacman -Syu` の途中でMSYS2の再起動を求められた場合は、再起動してください。

確認します。

```bash
echo $MSYSTEM
which clang++
clang++ --version
```

`echo $MSYSTEM` は `CLANGARM64` になっている必要があります。

手動検証用のビルドコマンドは次の通りです。

```bash
cd /path/to/YaneuraOu/source

make -j8 tournament \
  COMPILER=clang++ \
  YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE \
  ENGINE_NAME=YaneuraOu \
  TARGET_CPU=ARMV8 \
  EXTRA_CPPFLAGS='-DHASH_KEY_BITS=128 -DTT_CLUSTER_SIZE=4 -DENGINE_VERSION=\"V9.41DEV\"'
```

dot product版:

```bash
make -j8 tournament \
  COMPILER=clang++ \
  YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE \
  ENGINE_NAME=YaneuraOu \
  TARGET_CPU=ARMV8_DOTPROD \
  EXTRA_CPPFLAGS='-DHASH_KEY_BITS=128 -DTT_CLUSTER_SIZE=4 -DENGINE_VERSION=\"V9.41DEV\"'
```

ARM native手順に関する注意点:

- 現在のYaneuraOu-Builder GUIの `Windows arm` 実行経路は、x64 Windowsからのcross build用です。
- ARM native buildでは `/opt/aarch64-w64-mingw32/bin/...` は使わない想定です。
- ARM native buildでは、x64 cross build用の例外ハンドラstubは不要な想定です。
- 検証後、YaneuraOu-Builder側には `Windows arm native` のような別toolchain modeを用意し、
  `clangarm64.exe`、`MSYSTEM=CLANGARM64`、`COMPILER=clang++` を直接選べるように
  するのが自然です。
