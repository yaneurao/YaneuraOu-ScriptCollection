# 1. BookMinerCpp の位置づけ

BookMinerCpp は、Python 版 `BookMiner.py` を C++ で再実装するためのものです。

BookMiner の目的、用語、基本操作は Python 版と同じです。
次の内容は Python 版のチュートリアルを参照してください。

- 用語説明: [BookMiner/docs/01-terms.md](../../BookMiner/docs/01-terms.md)
- USI と `startpos moves ...`: [BookMiner/docs/03-usi.md](../../BookMiner/docs/03-usi.md)
- 定跡を掘る手順: [BookMiner/docs/04-basics.md](../../BookMiner/docs/04-basics.md)
- 主要コマンド: [BookMiner/docs/05-commands.md](../../BookMiner/docs/05-commands.md)
- GUI 操作: [BookMiner/docs/08-gui.md](../../BookMiner/docs/08-gui.md)
- 既存定跡から掘る手順: [BookMiner/docs/09-import-existing-book.md](../../BookMiner/docs/09-import-existing-book.md)

この BookMinerCpp チュートリアルでは、C++版だけに関係する内容を説明します。

## Python 版との関係

BookMinerCpp は、`BookMiner.py` と同じ stdin/stdout ベースの CLI として動作します。
GUI は C++ で作り直しません。
既存の `BookMiner-gui.py` から C++版を起動します。

```bash
cd ../BookMiner
python3 BookMiner-gui.py --cpp
```

GUI から見ると、BookMinerCpp は `BookMiner.py --from_gui` の代わりに起動される子プロセスです。
そのため、GUI のボタン、ログ欄、progress bar、`peta_shock`、`peta_read`、`peta next`、`peta refutation`、`peta depth gap`、`peta unsolved`、`peta opponent`、`enqueue` の考え方は Python 版と同じです。

## C++版で変わること

主な違いは内部実装とバックアップ形式です。

- 定跡DBの key は SFEN文字列ではなく、やねうら王の `PackedSfen` 相当の32 byteです。
- 指し手は `Move16` 相当の2 byteで保持します。
- 評価値は `int16_t` で保持します。
- 内部DBは memtable と sorted run 群による LSM-tree 風の構造です。
- 通常バックアップは `.db` ではなく `.ybb` で保存します。
- peta shock 化は C++ で再実装せず、`YO-MATERIAL.exe` の `makebook peta_shock` を呼び出します。
- `.db` と `.ybb` の相互変換は `YaneuraOu-ScriptCollection/makebook/` のスクリプトを使います。

## C++版で変わらないこと

次は Python 版と同じです。

- `think_sfens.txt` は `startpos moves ...` 形式です。行末に `book_extend_ply=...` メタ情報が付く場合があります。
- `p` は「DB保存、peta shock 化、peta book 読み込み」をまとめて行います。
- `pn` は peta book から次に掘る局面を `book/think_sfens.txt` に書き出します。
- `pr` / `pdg` / `pu` / `po` は条件を変えて次に掘る局面を `book/think_sfens.txt` に書き出します。
- `e` は `book/think_sfens.txt` を読み、探索キューへ積みます。
- `eval_limit`、`eval_diff`、`game_ply_limit` の意味は Python 版と同じです。
- KifManager は Python 版を使います。

## フォルダ構成

```text
BookMinerCpp/
  BookMinerCpp.exe
  README.md
  docs/
  settings/
    engine_settings-sample.json5
    book_miner_settings-sample.json5
    engine_settings.json5
    book_miner_settings.json5
  book/
    backup/
  log/
  source/
    Makefile
```

`settings/*.json5` の実設定ファイルはユーザーごとの環境に依存します。
Git管理するのは `*-sample.json5` だけにし、実際に使うファイルは sample をコピーして作ります。
