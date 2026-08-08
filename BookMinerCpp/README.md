# BookMinerCpp

BookMinerCpp は、Python 版 [BookMiner](../BookMiner/README.md) の C++ 実装です。

基本的な考え方、`peta_shock`、`peta_shock_latest`、`peta_read`、`peta next`、`peta refutation`、`peta unsolved`、`peta opponent`、`enqueue`、`eval_limit`、GUI の操作手順は Python 版 BookMiner と同じです。
まず BookMiner のチュートリアルを読んでください。

- [BookMiner チュートリアル](../BookMiner/README.md)

BookMinerCpp 固有の内容は次のドキュメントにまとめています。

- [1. BookMinerCpp の位置づけ](docs/01-overview.md)
- [2. ビルドとセットアップ](docs/02-build-and-setup.md)
- [3. 使い方](docs/03-usage.md)
- [4. `.ybb` と定跡ファイル](docs/04-ybb.md)
- [5. BookMinerCpp 仕様](docs/05-specification.md)
- [6. 設計メモ](docs/06-design.md)

## 概要

BookMinerCpp は GUI を持ちません。
`BookMiner/BookMinerCpp.exe` として配置すると、既存の `BookMiner-gui.py` が自動的に C++版を起動します。
直接起動して CLI として使う場合も、Python 版 BookMiner と同じ `BookMiner/` フォルダで起動します。

```bash
cd ../BookMiner
python3 BookMiner-gui.py
```

BookMinerCpp の通常バックアップは、やねうら王 バイナリ定跡DB (`.ybb`) として保存します。

```text
BookMiner/book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb
```

内部の定跡DBは `PackedSfen` key、`Move16`、`int16_t eval` を使い、memtable と sorted run 群による LSM-tree 風の構造で保持します。
