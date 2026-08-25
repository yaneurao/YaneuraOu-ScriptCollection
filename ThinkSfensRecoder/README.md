# ThinkSfensRecoder

将棋所、ShogiGUI、ShogiHome などの将棋GUIにエンジンとして登録し、検討中にGUIから送られる局面を `BookMiner/book/think_sfens.txt` に記録するためのツールです。

実エンジンとの間に入り、すべての入出力をそのまま通します。GUIから来た `position ...` コマンドだけを拾い、BookMiner が読める `startpos moves ...` / `sfen ... moves ...` 形式で保存します。

## 使い方

```bat
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe
```

出力先を変える場合:

```bat
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe --output C:\shogi\YaneuraOu-ScriptCollection\BookMiner\book\think_sfens.txt
```

同じ局面はデフォルトで重複記録しません。重複も記録したい場合は `--no-dedupe` を指定します。

## 将棋GUIへの登録例

Python を直接呼ぶ `.bat` を作り、その `.bat` を将棋GUIにエンジンとして登録します。

```bat
@echo off
python C:\shogi\YaneuraOu-ScriptCollection\ThinkSfensRecoder\ThinkSfensRecoder.py --engine-path C:\shogi\YaneuraOu.exe
```

このツール自体は `cshogi` を使っていません。PyInstaller で実行ファイル化しやすいよう、Python 標準ライブラリだけで動作します。
