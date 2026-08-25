# ThinkSfensRecoder

将棋所、ShogiGUI、ShogiHome などの検討画面で動かした局面を、BookMinerで掘りたいことがあります。
このツールは、そのために将棋GUIと実エンジンの間に入るproxyとして動作し、GUIから送られてきた局面を `think_sfens.txt` に記録します。

実エンジンとの入出力はすべてそのまま通します。GUIから来た `position ...` コマンドだけを拾い、BookMiner がそのまま読める `startpos moves ...` / `sfen ... moves ...` 形式で保存します。

デフォルトの保存先は、起動時のカレントフォルダにある `think_sfens.txt` です。PyInstallerで実行ファイル化した場合でも、実行ファイルの配置場所ではなくカレントフォルダへ保存します。

## 使い方

```bat
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe
```

出力先を変える場合:

```bat
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe --output C:\shogi\YaneuraOu-ScriptCollection\BookMiner\book\think_sfens.txt
```

同じ局面はデフォルトで重複記録しません。重複も記録したい場合は `--no-dedupe` を指定します。

将棋GUI上で表示されるエンジン名を変えたい場合は、`--engine-name` を指定します。これは実エンジンが `usi` コマンドに応答するときの `id name ...` 行だけを置き換えます。

```bat
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe --engine-name ThinkSfensRecoder
```

## 将棋GUIへの登録例

Python を直接呼ぶ `.bat` を作り、その `.bat` を将棋GUIにエンジンとして登録します。BookMinerの `enqueue` でそのまま使う場合は、カレントフォルダを `BookMiner\book` にしてから起動します。

```bat
@echo off
cd /d C:\shogi\YaneuraOu-ScriptCollection\BookMiner\book
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe
```

このツール自体は `cshogi` を使っていません。PyInstaller で実行ファイル化しやすいよう、Python 標準ライブラリだけで動作します。
