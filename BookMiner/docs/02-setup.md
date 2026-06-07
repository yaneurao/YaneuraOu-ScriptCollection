# 2. セットアップ

この章では、BookMiner を起動するまでに必要な準備を説明します。用語は [1. 用語説明](01-terms.md) で説明しています。

## 必要なもの

- Python 3
- `cshogi`
- 探索用エンジン
- `BookMiner.py` と同じフォルダに置いた `YO-MATERIAL.exe`

## フォルダ構成

BookMiner は次の場所で実行する想定です。

```text
YaneuraOu-ScriptCollection/
  CommonLib/
  BookMiner/
    BookMiner.py
    YO-MATERIAL.exe
    README.md
    docs/
    settings/
      engine_settings.json
```

BookMiner は `../CommonLib/YaneShogiLib.py` を使います。`BookMiner/` だけを別フォルダに移動すると動きません。

## Python のインストール

Windows では python.org から Python 3 をインストールし、`py` コマンドが使える状態にしてください。

インストール後、次のように確認します。

```powershell
py --version
```

Linux/macOS では、環境に合わせて Python 3 を用意してください。

```bash
python3 --version
```

## cshogi などのインストール

BookMiner は `cshogi` を使います。

Windows:

```powershell
py -m pip install cshogi
```

Linux/macOS:

```bash
python3 -m pip install cshogi
```

## settings/engine_settings.json の書き方

`settings/engine_settings.json` には、BookMiner が局面を思考させる探索用エンジンを書きます。

例:

```json
[
    {
        "path": "engines/suisho11/YaneuraOuV940AVX2.exe",
        "name": "suisho11",
        "nodes": 1000000,
        "multi": 1
    }
]
```

各項目の意味は次の通りです。

- `path` : USI エンジンの実行ファイル、または `ssh ...` で始まる起動コマンド。
- `name` : ログ表示用の名前。
- `nodes` : 1 局面あたりの探索ノード数。
- `multi` : 同じ設定のエンジンを何プロセス起動するか。

最初は `multi` を `1` にして、エンジンが `readyok` を返すことを確認してください。

複数エンジンを使う場合は、配列に複数の設定を書きます。

```json
[
    {
        "path": "engines/local/YaneuraOuV940AVX2.exe",
        "name": "local",
        "nodes": 1000000,
        "multi": 2
    },
    {
        "path": "engines/local2/YaneuraOuV940AVX2.exe",
        "name": "local2",
        "nodes": 1000000,
        "multi": 2
    }
]
```

## SSH 経由で複数 PC を使う方法

`path` が `ssh` で始まる場合、BookMiner はその文字列を SSH コマンドとして起動します。

例:

```json
[
    {
        "path": "ssh worker1 /home/user/engines/suisho11/run-bookminer-engine.sh",
        "name": "worker1",
        "nodes": 1000000,
        "multi": 4
    }
]
```

リモート側では、シェルスクリプトでエンジンのあるフォルダへ移動してから実行するのが安全です。

```sh
#!/bin/sh
cd /home/user/engines/suisho11 || exit 1
exec ./YaneuraOuV940AVX2
```

BookMiner は SSH のパスワード入力を処理しません。事前に公開鍵認証でログインできるようにしてください。

また、SSH 経由の `path` は内部で空白区切りに分割されます。パスに空白を含めない構成にしてください。

## やねうら王エンジンの設定方法

やねうら王系エンジンでは、エンジン実行ファイルと同じフォルダに `engine_options.txt` を置くと、`isready` 時に読み込まれます。

BookMiner から起動する探索用エンジンでは、例えば次のようにします。

```text
Threads 1
USI_Hash 1024
BookFile no_book
PvInterval 10000000
NetworkDelay 0
NetworkDelay2 0
MinimumThinkingTime 1000
```

NNUE 系エンジンを使う場合は、評価関数に合わせて `EvalDir` や `FV_SCALE` も設定してください。

```text
EvalDir eval
FV_SCALE 40
```

注意点:

- `BookFile` は `no_book` にします。BookMiner が局面を思考させるとき、エンジン自身の定跡は使いません。
- `Threads` は基本的に `1` にし、並列化は `engine_settings.json` の `multi` で行います。
- `USI_Hash` は PC のメモリに合わせて調整します。
- `MultiPV` は BookMiner 側が探索中に指定するため、`engine_options.txt` で固定しないでください。

## 起動

`YaneuraOu-ScriptCollection/BookMiner/` をカレントフォルダにして起動します。

Windows:

```powershell
cd YaneuraOu-ScriptCollection\BookMiner
py BookMiner.py
```

Linux/macOS:

```bash
cd YaneuraOu-ScriptCollection/BookMiner
python3 BookMiner.py
```

起動すると、エンジン設定を読み込み、ログファイルが `log/` に作られます。

## 主な入出力

- `book/think_sfens.txt` : 掘る棋譜。1 行が 1 対局です。
- `book/book_miner.db` : BookMiner の作業用定跡 DB。
- `book/backup/` : `w` コマンドや自動バックアップで作られるバックアップ。
- `book/peta_book.db` : peta shock 化された定跡 DB。
- `log/` : 実行ログ。
