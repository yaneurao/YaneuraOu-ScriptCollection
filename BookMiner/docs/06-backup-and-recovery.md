# 6. バックアップと復旧

この章では、BookMiner の作業用定跡 DB の保存、定期自動バックアップ、破損時の復旧方法を説明します。

## 作業用 DB

BookMiner の作業用 DB は次のファイルです。

```text
book/book_miner.db
```

BookMiner は起動時にこのファイルを読み込みます。終了時に `q` コマンドを使うと、現在の定跡 DB がこのファイルへ保存されます。

`!` コマンドで終了した場合は、終了時の保存は行われません。

## バックアップファイル

バックアップは次のフォルダに書き出されます。

```text
book/backup/
```

ファイル名は次のようになります。

```text
book/backup/book_miner-20260607103251_14505901.db
```

`20260607103251` の部分は書き出した時刻、`14505901` の部分は書き出した局面数です。

`book/book_miner.db` と `book/backup/book_miner-....db` は同じやねうら王標準定跡フォーマットです。

## 手動バックアップ

手動でバックアップを書き出すには `w` コマンドを使います。

```text
w
```

このコマンドは、現在の定跡 DB を `book/backup/` に書き出します。

`p` コマンドも、最初に現在の定跡 DB を `book/backup/` に書き出します。そのあと、書き出したバックアップを peta shock 化して `book/peta_book.db` として読み込みます。通常の周回作業では `p` を使うと、書き出し完了前に `r` を実行してしまう事故を避けやすくなります。

手数制限を付けた書き出しもできます。

```text
w 100
```

この場合、ファイル名に `_ply100` が付きます。これは一部だけを書き出したファイルなので、復旧用の `book/book_miner.db` として使わないでください。

## 定期自動バックアップ

BookMiner は起動後、一定時間ごとに自動でバックアップを書き出します。

デフォルトでは 3 時間ごとです。

```json
{
    "auto_save_interval_seconds": 10800
}
```

この値は [settings/book_miner_settings.json](../settings/book_miner_settings.json) の `auto_save_interval_seconds` で変更します。単位は秒です。

例えば 1 時間ごとにするなら次のようにします。

```json
{
    "auto_save_interval_seconds": 3600
}
```

30 分ごとなら次のようにします。

```json
{
    "auto_save_interval_seconds": 1800
}
```

定跡 DB が大きい場合、バックアップの書き出しには時間がかかります。短すぎる間隔にすると、探索中の負荷が増えます。

## 破損時の復旧

`book/book_miner.db` が破損して BookMiner.py が正常に起動しなくなった場合は、`book/backup/` のバックアップから復旧できます。

手順:

1. BookMiner.py を終了する。
2. `book/book_miner.db` を別名に退避する。
3. `book/backup/` から最新の通常バックアップを選ぶ。
4. そのファイルを `book/book_miner.db` へコピーする。
5. BookMiner.py を起動する。

例:

```text
book/backup/book_miner-20260607103251_14505901.db
```

このファイルをコピーして、次の名前にします。

```text
book/book_miner.db
```

注意点:

- `_ply100` のような `_plyN` 付きのバックアップは、手数制限つきの部分書き出しです。復旧用には使わないでください。
- コピー元のバックアップは残しておくほうが安全です。
- `*.db.tmp` は書き出し途中の一時ファイルです。復旧用には使わないでください。

## 書き出し途中の安全性

BookMiner の DB 書き出しは、まず `*.db.tmp` に出力し、書き出しが完了してから `.db` に置き換えます。

このため、書き出し途中で異常終了しても、完成済みの `.db` を壊しにくい作りになっています。
