# 2. ビルドとセットアップ

この章では、BookMinerCpp をビルドして起動するまでの準備を説明します。
BookMiner 全体の使い方は [Python 版 BookMiner のチュートリアル](../../BookMiner/README.md) を参照してください。

## 必要なもの

- `clang++`
- `make`
- 探索用 USI エンジン
- `YO-MATERIAL.exe`
- GUI から使う場合は Python 版 `BookMiner-gui.py`

`../makebook/convert_db_to_ybb.py` / `../makebook/convert_ybb_to_db.py` を使って `.db` と `.ybb` を変換する場合は、Python、`cshogi`、`numpy` も必要です。

## ビルド

Linux / MSYS2 / MinGW では、`BookMinerCpp/source/` で `make` します。

```bash
cd YaneuraOu-ScriptCollection/BookMinerCpp/source
make CXX=clang++
```

出力先は次の通りです。

```text
BookMinerCpp/BookMinerCpp.exe
```

BookMinerCpp の `source/Makefile` は、BookMinerCpp 本体とは別に、やねうら王の一部を静的ライブラリとしてビルドします。

```text
BookMinerCpp/source/build/yaneuraou/yaneuraou_core.lib
```

このライブラリには、やねうら王の `Position`、合法手生成、`PackedSfen` packer、`Move16` 変換など、BookMinerCpp が必要とする処理が含まれます。
BookMinerCpp はこのライブラリをリンクして、`startpos moves ...` の展開や合法手チェックを行います。

## YO-MATERIAL.exe

BookMinerCpp は peta shock 化のために `YO-MATERIAL.exe` を呼び出します。
配置場所は次です。

```text
BookMiner/YO-MATERIAL.exe
```

`YO-MATERIAL.exe` は、評価関数ファイルを必要としない MATERIAL 版やねうら王です。
peta shock 化だけに使うので、探索用エンジンとは別です。

Python版 BookMiner と同じ実行ファイルを使います。
BookMinerCpp から見た相対パスでは `../BookMiner/YO-MATERIAL.exe` です。
`YO-MATERIAL.exe` は同梱していません。自分で MATERIAL 版のやねうら王をビルドするか、やねうら王News Letterで頒布されている最新の MATERIAL 版を入手してください。
入手した実行ファイルは `YO-MATERIAL.exe` という名前に変更して、`BookMiner/YO-MATERIAL.exe` に配置します。

## 設定ファイル

`settings/` には sample ファイルを置きます。
実際に使うときは、sample をコピーして実設定ファイルを作ります。

```text
settings/engine_settings-sample.json5
settings/book_miner_settings-sample.json5
```

コピー後:

```text
settings/engine_settings.json5
settings/book_miner_settings.json5
```

Linux / MSYS2:

```bash
cp settings/engine_settings-sample.json5 settings/engine_settings.json5
cp settings/book_miner_settings-sample.json5 settings/book_miner_settings.json5
```

PowerShell:

```powershell
Copy-Item .\settings\engine_settings-sample.json5 .\settings\engine_settings.json5
Copy-Item .\settings\book_miner_settings-sample.json5 .\settings\book_miner_settings.json5
```

`engine_settings.json5` と `book_miner_settings.json5` は git 管理しない想定です。

## engine_settings.json5

探索用エンジンを指定します。
基本は Python 版 BookMiner と同じです。

```json5
[
  {
    path: "engines/suisho11/YaneuraOuV940AVX2.exe",
    name: "suisho11",
    nodes: 1000000,
    multi: 1,

    // C++版では省略可能。
    // 省略時は BookMiner.py と同じ既定値を使う。
    multipv: 4,
    multipv_delta: 100,
  },
]
```

`multipv` と `multipv_delta` は C++版でも設定できます。
省略した場合は Python 版に合わせた既定値を使います。

## book_miner_settings.json5

BookMinerCpp 本体の動作設定です。

```json5
{
  auto_save_interval_seconds: 10800,
  max_book_ply: 200,
  peta_next_start_sfens_path: "book/peta_start_sfens.txt",
}
```

意味は Python 版 BookMiner と同じです。

- `auto_save_interval_seconds`: 定期自動保存の間隔。単位は秒。
- `max_book_ply`: この手数に到達したら、それ以上深く掘らない。
- `peta_next_start_sfens_path`: `pn` / `pnf` コマンドの開始局面集合。

## 起動確認

CLI として直接起動する場合:

```bash
cd YaneuraOu-ScriptCollection/BookMinerCpp
./BookMinerCpp.exe
```

GUI から C++版を使う場合:

```bash
cd YaneuraOu-ScriptCollection/BookMiner
python3 BookMiner-gui.py --cpp
```

GUI から使う場合、`BookMiner-gui.py` は内部的に次を起動します。

```text
../BookMinerCpp/BookMinerCpp.exe --from_gui
```
