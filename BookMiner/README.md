# BookMiner

BookMiner は、将棋AIによって、大規模定跡を自動生成するためのスクリプトです。

使い方は次のチュートリアルを順に読んでください。

- [1. 用語説明](docs/01-terms.md)
- [2. セットアップ](docs/02-setup.md)
- [3. 定跡を掘るための基礎](docs/03-basics.md)
- [4. BookMiner.py の主要コマンド](docs/04-commands.md)
- [5. 生成された定跡をやねうら王で使うには](docs/05-use-with-yaneuraou.md)
- [6. バックアップと復旧](docs/06-backup-and-recovery.md)
- [7. GUI で操作する](docs/07-gui.md)
- [8. 既存のやねうら王定跡から掘り始める](docs/08-import-existing-book.md)

GUI で操作したい場合は、次のように起動します。

```bash
python3 BookMiner-gui.py
```

GUI は `BookMiner.py` を子プロセスとして起動し、既存のコマンドを送信する wrapper です。
