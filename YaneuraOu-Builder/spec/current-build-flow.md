# 現行 YaneuraOu-Builder 仕様

このメモは `YaneuraOu-Builder/refs/` に置かれている既存スクリプトの挙動を整理する。

対象ファイル:

- `YaneuraOu-Builder/refs/make-yaneuraou-all.py`
- `YaneuraOu-Builder/refs/my-material`
- `YaneuraOu-Builder/refs/mynnue-tune`
- `YaneuraOu-Builder/refs/mynnue-apply`

## 全体像

現状のビルドは、用途ごとに独立したスクリプトを手で用意して実行する方式である。

| 用途 | 現行ファイル | 主な成果物 |
|---|---|---|
| 頒布用パッケージ一括生成 | `make-yaneuraou-all.py` が `my-uraou-*-*` スクリプトを生成 | `yaneuraou-V940-*-*-all.7z` |
| ペタショック化用 Material 実行ファイル | `my-material` | `YO-MATERIAL.exe` |
| SPSA 調整用ビルド | `mynnue-tune` | `*-tune.exe` |
| SPSA 適用後ビルド | `mynnue-apply` | `*_V941apply.exe` |

いずれも `YaneuraOu/source/Makefile` を直接呼び出す。基本形は次の通りである。

```bash
make clean YANEURAOU_EDITION=<edition>
make -j8 tournament COMPILER=clang++ YANEURAOU_EDITION=<edition> ENGINE_NAME="YaneuraOu" TARGET_CPU=<cpu> EXTRA_CPPFLAGS="..."
cp YaneuraOu-by-gcc[.exe] <artifact>
```

Windows では MSYS2 上で実行する前提になっている。Windows x64 / x86 の違いは、現行スクリプト上は `win64` / `win32` という出力スクリプト名に現れる。GUI から実行する場合は、platform に応じて `MSYSTEM=MINGW64` / `MSYSTEM=MINGW32` を設定し、MSYS2 の login bash 経由で生成scriptを実行する。Windows arm は x64 Windows + MSYS2 `MINGW64` からの cross build として扱い、ARM native MSYS2 実行は現時点では対象外とする。

## Windows ARM / MSYS2 CLANGARM64 調査メモ

調査日: 2026-06-16

ローカルの `Stockfish` repository は、調査前に `git pull --ff-only` で `stockfish-dev-20260614-74a0a737` まで更新した。

### MSYS2 側の状況

MSYS2 には Windows ARM64 / AArch64 向けの `CLANGARM64` environment がある。

| platform | MSYS2 launcher | `MSYSTEM` | package prefix |
|---|---|---|---|
| Windows x64 | `C:\msys64\mingw64.exe` | `MINGW64` | `mingw-w64-x86_64-*` |
| Windows x86 | `C:\msys64\mingw32.exe` | `MINGW32` | `mingw-w64-i686-*` |
| Windows ARM64 | `C:\msys64\clangarm64.exe` | `CLANGARM64` | `mingw-w64-clang-aarch64-*` |

参考:

- <https://www.msys2.org/docs/environments/>
- <https://www.msys2.org/docs/arm64/>

したがって、Windows ARM64 実機上で MSYS2 native build を行うなら、`mingw64.exe` / `mingw32.exe` ではなく `clangarm64.exe`、または `MSYSTEM=CLANGARM64` を設定した MSYS2 login shell を使う必要がある。

x64 Windows host から Windows ARM64 binary を作る場合は別である。`clangarm64.exe` は ARM64 Windows 用の実行ファイルを起動する環境なので、x64 host 上で `/clangarm64/bin/clang++` を実行すると `cannot execute binary file: Exec format error` になる。この場合は `MINGW64` または `MSYS` shell 上で MSYS2 の cross clang (`/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++`) を使う。

ただし、これは「MSYS2 が Windows ARM64 toolchain を提供している」という意味であり、やねうら王の Makefile と `TARGET_CPU` matrix がそのまま Windows ARM64 をビルドできることまでは意味しない。

### Stockfish Makefile の状況

Stockfish `src/Makefile` は ARM architecture として `armv8`、`armv8-dotprod`、`arm64-universal` などを持っている。`armv8` 系では `USE_NEON` が有効になり、`armv8-dotprod` では dot product 用の compile flag も追加される。

一方、Windows compiler の自動選択は x86 / x64 前提になっている。

- `COMP=mingw` では `bits=64` のとき `x86_64-w64-mingw32-c++`、`bits=32` のとき `i686-w64-mingw32-c++` を選ぶ。
- `COMP=clang` かつ Windows target では `x86_64-w64-mingw32-clang++` を選ぶ。
- `aarch64-w64-mingw32-*` や CLANGARM64 用 compiler を自動選択する分岐は見当たらない。
- `COMPCXX` を指定すると `CXX` を上書きできる。

実際に `Stockfish/src` で `make -n config-sanity ARCH=armv8 COMP=mingw` を確認すると、`arch='armv8'`、`target_windows='yes'`、`neon='yes'` である一方、`CXX: x86_64-w64-mingw32-c++` になった。つまり、Stockfish は ARM architecture 自体は扱えるが、Windows ARM64 を compiler selection まで含めた first-class target として自動処理しているわけではない。

Stockfish で Windows ARM64 を試す場合も、CLANGARM64 環境上で `COMPCXX=clang++` または適切な `aarch64-w64-mingw32-clang++` 相当を明示し、`ARCH=armv8` / `ARCH=armv8-dotprod` などを組み合わせる検証が必要になる。

### YaneuraOu-Builder への示唆

YaneuraOu-Builder 側で `Windows arm` platform を扱う場合、少なくとも次を分けて検討する必要がある。

- MSYS2 実行環境: ARM64 Windows 実機で native build するなら `MSYSTEM=CLANGARM64` / `clangarm64.exe` を選ぶ。x64 Windows host から cross build するなら `MSYSTEM=MINGW64` / `mingw64.exe` 上で cross compiler を使う。
- compiler: x64 host cross build では `/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++` を明示する。
- `TARGET_CPU`: 既存の `SSE41`、`AVX2`、`AVX512` 系は Windows ARM64 では使えないため、やねうら王 Makefile 側に ARM64 / NEON 向け target が必要になる。
- compile flags: x86 SIMD 前提の `-mavx*` / `-msse*` 系 flag を混入させない。
- profile build / PGO: build 中に生成した exe を実行する方式の場合、x64 Windows host では ARM64 exe をそのまま実行できない可能性がある。ARM64 Windows 実機、または emulator / run prefix の扱いを検討する必要がある。

結論として、MSYS2 は Windows ARM64 toolchain を提供しているが、YaneuraOu-Builder で `Windows arm` を選べるようにするだけでは不十分である。現行GUIでは、やねうら王 Makefile の `ARMV8` / `ARMV8_DOTPROD` target、x64 hostで実行可能な cross compiler、MSYS2 cross CRT の不足回避 stub を組み合わせた cross build のみを対応範囲とする。

### x64 Windows host からの Windows ARM64 cross build 実測

調査日: 2026-06-17

x64 Windows + MSYS2 `MINGW64` shell から、Windows ARM64 用 `YaneuraOu-by-gcc.exe` のリンクまで到達した。現時点では MSYS2 cross CRT の不足をスタブで補う暫定手順であり、正式対応ではない。

必要な主な MSYS2 package:

```bash
pacman -S --needed mingw-w64-cross-clang-toolchain lld mingw-w64-clang-aarch64-winpthreads
```

確認:

```bash
echo $MSYSTEM
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++ --version
ls /clangarm64/include/pthread.h
ls /clangarm64/lib/libpthread.a
ls /usr/bin/ld.lld*
```

`clangarm64.exe` ではなく、x64 host 上で実行できる cross compiler を明示する。

```bash
COMPILER=/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++
```

`pthread.h` は `/clangarm64/include` にあるが、通常の `-I/clangarm64/include` で足すと libc++ の `<cstring>` / `<cstddef>` が `string.h` / `stddef.h` を探す順序を壊す。`-I` ではなく `-idirafter /clangarm64/include` を使う。

MSYS2 の cross CRT (`/opt/aarch64-w64-mingw32/lib/libmingw32.a`) には、調査時点で `_gnu_exception_handler` と `__mingw_oldexcpt_handler` が入っていなかった。一方、`/clangarm64/lib/libmingw32.a` には存在した。

確認コマンド:

```bash
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-nm -g /opt/aarch64-w64-mingw32/lib/libmingw32.a | grep -E '(_gnu_exception_handler|__mingw_oldexcpt_handler)'
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-nm -g /clangarm64/lib/libmingw32.a | grep -E '(_gnu_exception_handler|__mingw_oldexcpt_handler)'
```

`/opt` 側に定義がないため、暫定的に次の stub object を作って `EXTRA_LDFLAGS` へ追加した。

```bash
cd ~/shogi/source

cat > mingw_aarch64_ehandler_stub.c <<'EOF'
#include <windows.h>

LPTOP_LEVEL_EXCEPTION_FILTER __mingw_oldexcpt_handler = 0;

LONG CALLBACK _gnu_exception_handler(EXCEPTION_POINTERS *exception_data) {
    (void)exception_data;
    return EXCEPTION_CONTINUE_SEARCH;
}
EOF

/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang \
  -idirafter /clangarm64/include \
  -c mingw_aarch64_ehandler_stub.c \
  -o mingw_aarch64_ehandler_stub.o
```

実測で通った make command の基本形:

```bash
PATH=/opt/aarch64-w64-mingw32/bin:$PATH make -j8 tournament \
  COMPILER=/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++ \
  YANEURAOU_EDITION=YANEURAOU_ENGINE_NNUE \
  ENGINE_NAME=YaneuraOu \
  TARGET_CPU=ARMV8 \
  EXTRA_CPPFLAGS='-idirafter /clangarm64/include -DHASH_KEY_BITS=128 -DTT_CLUSTER_SIZE=4 -DENGINE_VERSION=\"V9.41Git\"' \
  EXTRA_LDFLAGS='./mingw_aarch64_ehandler_stub.o -L/clangarm64/lib'
```

`TARGET_CPU=ARMV8_DOTPROD` では、やねうら王 Makefile 側で `-DUSE_NEON_DOTPROD -march=armv8.2-a+dotprod` が追加される。

リンク時には `aarch64-w64-mingw32-ld.lld` が使われ、`/opt/aarch64-w64-mingw32/lib/crt2.o`、`/opt/aarch64-w64-mingw32/lib/crtbegin.o`、`/usr/lib/clang/21/lib/windows/libclang_rt.builtins-aarch64.a` などが投入された。`/usr/bin/ld.lld` がない場合は `pacman -S --needed lld` が必要である。

生成物確認:

```bash
/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-objdump -f YaneuraOu-by-gcc.exe
```

注意点:

- `TARGET_CPU=ARMV8` は NEON 有効、`TARGET_CPU=ARMV8_DOTPROD` は NEON dot product 有効の Windows ARM64 target である。
- この binary は x64 host では通常実行できないため、PGO や build 中に生成 exe を実行する工程には使えない。
- `mingw_aarch64_ehandler_stub.o` は MSYS2 cross CRT 側の不足を補う暫定回避であり、YaneuraOu-Builder に組み込む場合は `Windows ARM cross build (experimental)` として扱う。
- `-L/clangarm64/lib` は pthread / MinGW library の探索に必要だが、header path は `-idirafter /clangarm64/include` にする。`-I/clangarm64/include` は使わない。
- YaneuraOu-Builder GUI は Windows arm script 生成時に `mingw_aarch64_ehandler_stub.c` と `mingw_aarch64_ehandler_stub.o` を自動生成し、`EXTRA_LDFLAGS` に追加する。
- `YANEURAOU_ENGINE_NNUE` の Windows arm 成果物は、既存の詳細な評価関数prefixではなく、互換用に `YaneuraOu_NNUE-V941DEV_ARMV8.exe`、`YaneuraOu_NNUE-V941DEV_ARMV8_DOTPROD.exe` のような名前にする。

## `make-yaneuraou-all.py`

### 目的

Windows x64、Windows x86、macOS 向けに、評価関数形式と CPU ターゲットの組み合わせを一括ビルドするための実行スクリプトを生成する。

この Python スクリプト自体は直接ビルドしない。`my-uraou-dev-win64` のようなシェルスクリプトを出力し、そのスクリプトを別途実行する。

### 固定パラメータ

| 項目 | 現状値 |
|---|---|
| エンジンバージョン | `V9.40` |
| package version 表記 | `V940` |
| platform | `win64`, `win32`, `mac` |
| build target | `tournament` |
| compiler | `clang++` |
| make 並列数 | `-j8` |
| engine name | `YaneuraOu` |
| hash key | `-DHASH_KEY_BITS=128` |
| TT cluster size | `-DTT_CLUSTER_SIZE=4` |
| package format | `7z` |

YaneuraOu-Builder GUI では package version を UI 入力させず、engine version から自動生成する。例えば `V9.41` は旧来互換の `V941` になり、package 名は `yaneuraou-V941-dev-win64-all.7z` のようになる。`V9.41YANE` のように数字以降の suffix がある場合は `V941YANE` とする。

### ソースフォルダ

ソース位置はスクリプト内に固定されている。

| platform | source folder |
|---|---|
| Windows | `D:\doc\VSCodeProject\YaneuraOu\YaneuraOu-GitHub\YaneuraOu\source` |
| macOS | `/winbuild/source` |

ただし、既存refsの生成スクリプトでは `cp -r` がコメントアウトされており、実際には `build/source` が事前に存在する前提になっている。GUI生成scriptではこの制約を外し、指定された source folder を `build/source` にコピーしてからビルドする。macOS の default は、Windows側でビルド済みのフォルダを `/winbuild` に見せる前提で `/winbuild/source` を使う。

### CPU ターゲット

Windows:

- `SSE41`
- `SSE42`
- `AVX2`
- `ZEN1`
- `ZEN2`
- `AVXVNNI`
- `AVX512`
- `AVX512VNNI`

Windows arm:

- `ARMV8`
- `ARMV8_DOTPROD`

macOS:

- `APPLEM1`
- `APPLEAVX2`
- `APPLESSE42`

`ZEN3` はコメントアウトされている。`evallearn` は廃止済みで、YaneuraOu-Builder GUI では選択肢として扱わない。

### 評価関数 matrix

現状の `target_evals` は次の通り。

| `YANEURAOU_EDITION` | 出力名 prefix |
|---|---|
| `YANEURAOU_ENGINE_NNUE` | `YaneuraOu_NNUE_halfkp_256x2_32_32` |
| `YANEURAOU_ENGINE_SFNN1536` | `YaneuraOu_SFNN1536` |
| `YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3` | `YaneuraOu_SFNN_halfka2_1024_7_64_k3k3` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_1024X2_8_32` | `YaneuraOu_NNUE_halfkp_1024x2_8_32` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_1024X2_8_64` | `YaneuraOu_NNUE_halfkp_1024x2_8_64` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_768X2_16_64` | `YaneuraOu_NNUE_halfkp_768x2_16_64` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_512X2_8_64` | `YaneuraOu_NNUE_halfkp_512x2_8_64` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_384X2_8_96` | `YaneuraOu_NNUE_halfkp_384x2_8_96` |
| `YANEURAOU_ENGINE_NNUE_HALFKPE9` | `YaneuraOu_NNUE_halfkpe9_256x2_32_32` |
| `YANEURAOU_ENGINE_NNUE_HALFKP_VM_256X2_32_32` | `YaneuraOu_NNUE_halfkpvm_256x2_32_32` |
| `YANEURAOU_ENGINE_NNUE_KP256` | `YaneuraOu_NNUE_kp_256x2_32_32` |
| `YANEURAOU_ENGINE_KPPT` | `YaneuraOu_KPPT` |
| `YANEURAOU_ENGINE_KPP_KKPT` | `YaneuraOu_KPP_KKPT` |

注意点:

- `YANEURAOU_ENGINE_NNUE_HALFKP_1024X2_8_32` が2回登録されている。
- `YANEURAOU_ENGINE_MATERIAL` はコメントアウトされている。
- Makefile 側で未知の NNUE/SFNN architecture は architecture header 生成に回るため、評価関数名の追加には Makefile 側の対応も必要になる。

### DEV / Git の2系統

`dev = [True, False]` により、各 platform について2種類の生成スクリプトを作る。

| `d` | 表示文字列 | script name | `USE_LAZY_EVALUATE` |
|---|---|---|---|
| `True` | `DEV` | `my-uraou-dev-<platform>` | 付与する |
| `False` | `Git` | `my-uraou-git-<platform>` | 付与しない |

`ENGINE_VERSION` は `V9.40DEV` または `V9.40Git` のように埋め込まれる。

### 生成スクリプトの流れ

生成される `my-uraou-*-*` は、おおむね次の処理を行う。

1. script自身の場所から run directory を決めて `cd` する
2. source folder などをscript内変数として設定する
3. `mkdir build`
4. GUI生成scriptでは source folder を `build/source` にコピー
5. `cd build/source`
6. macOS の場合は Makefile の `PYTHON = ...` を生成scriptが検出した `python3` / `python` に置換
7. build target、評価関数、CPU target の三重ループで `make clean` と `make`
8. `YaneuraOu-by-gcc` または `YaneuraOu-by-gcc.exe` を評価関数別フォルダにコピー
9. 生成スクリプト自身を `build/` にコピー
10. `7z a yaneuraou-V940-<dev/git>-<platform>-all.7z -xr!obj *`

GUI生成scriptでは、run directory 直下に `run-all` も生成する。zsh / bash から手動実行する場合は、run directory に移動して `./run-all` を実行すれば、`scripts/` 配下の生成scriptを順に実行できる。

出力ファイル名は次の形になる。

```text
<edition_filestr>-V940<DEV/Git>_<CPU>
```

Windows arm の `YANEURAOU_ENGINE_NNUE` だけは次の形にする。

```text
YaneuraOu_NNUE-V941DEV_ARMV8.exe
YaneuraOu_NNUE-V941DEV_ARMV8_DOTPROD.exe
```

`evallearn` は廃止済みであり、生成対象には含めない。

## `my-material`

### 目的

ペタショック化で使う頒布用 `YO-MATERIAL.exe` を生成する。

### 現状の流れ

1. `D:\doc\VSCodeProject\YaneuraOu\YaneuraOu-Dev\source` を `~/shogi` にコピー
2. `cd source`
3. `YANEURAOU_ENGINE_MATERIAL` を `TARGET_CPU=AVX2` で tournament build
4. `MATERIAL_LEVEL=9`
5. `HASH_KEY_BITS=128`
6. `TT_CLUSTER_SIZE=4`
7. `ENGINE_VERSION=V9.40YANE`
8. `../bin/YO-MATERIAL.exe` にコピー

この成果物は、BookMiner / makebook のペタショック化用途で必要になる。

## `mynnue-tune`

### 目的

SPSA 調整用のソースを作り、調整対象 architecture の実行ファイルを生成する。

### 現状の流れ

1. `~/shogi/source-tune/` を作成
2. `~/shogi/source-tune/source` を削除
3. `YaneuraOu-Dev/source` を `source-tune/source` にコピー
4. `SPSA/tune.py` と `ParamLib.py` をコピー
5. `SPSA/param/YaneuraOuV931.tune` と `.params` をコピー
6. `python3 tune.py tune YaneuraOuV931.tune ~/shogi/source-tune/source`
7. `source` に移動して tournament build
8. `YANEURAOU_ENGINE_NNUE_SFNNwoPSQT_HALFKA2_1024_7_64_LS9`
9. `TARGET_CPU=AVX512VNNI`
10. `ENGINE_VERSION=V9.31YANE`
11. `source-tune/YANEURAOU_ENGINE_NNUE_SFNNwoPSQT_HALFKA2_1024_7_64_LS9-tune.exe` にコピー

## `mynnue-apply`

### 目的

SPSA パラメータをソースに適用し、その適用後ソースから頒布・検証用の実行ファイルを生成する。

### 現状の流れ

1. `~/shogi/source-tune/` を作成
2. `~/shogi/source-tune/source` を削除
3. `YaneuraOu-Dev/source` を `source-tune/source` にコピー
4. `SPSA/tune.py` と `ParamLib.py` をコピー
5. `SPSA/param/YaneuraOuV940.tune` と `.params` をコピー
6. `python3 tune.py apply YaneuraOuV940.tune ~/shogi/source-tune/source`
7. `source` に移動して tournament build
8. `YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3`
9. `TARGET_CPU=AVX512VNNI`
10. `ENGINE_VERSION=V9.41YANE`
11. `source-tune/YANEURAOU_ENGINE_SFNN_halfka2_1024_7_64_k3k3_V941apply.exe` にコピー

## 現状の課題

GUI 化で解消したい課題は次の通り。

- ソースパス、SPSAパス、出力先、バージョンがスクリプト内に固定されている。
- 似た `make` コマンドが複数ファイルに分散している。
- 評価関数・CPU・platform の matrix が Python リストとして埋め込まれており、GUI から選べない。
- SPSA tune/apply の param 名と出力名が固定されている。
- `make-yaneuraou-all.py` は「スクリプト生成」と「ビルドレシピ定義」が混ざっている。
- 生成スクリプトに macOS でも `REM` 行が出る。
- `mkdir build` が存在済みの場合に失敗表示になる。
- 既存refsの生成scriptは `build/source` の事前配置に依存している。
- ビルド結果の成功/失敗、所要時間、生成物一覧、再実行対象が機械的に追跡されない。
- 頒布パッケージに何を含めるか、何を除外するかが `7z` コマンド直書きになっている。
- ビルド履歴、使用した git commit、SPSA param、Makefile option が成果物と結び付いていない。
