# GenSfen

`gensfen.py`は、教師局面の生成を行うためのスクリプトです。

USIプロトコル対応のエンジンを用いて教師データの生成を行います。

スクリプト起動後に
- `g`コマンド(`gensfen`)で教師棋譜の生成を開始します。
- `q`コマンド(`quit`)で終了します。このとき教師データは自動保存されます。
- `p`コマンド(`pause`)で生成を一時停止します。(完全に一時停止状態になるのに現在の局面の探索が終了する必要があるので、数分かかることがあります。) スクリプトは終了しません。CPU負荷が一時的に0%近くになります。再度`p`コマンドを実行すると教師生成を再開します。

ここで生成した教師データは、pack形式となります。`kif`フォルダに保存されていきます。

このpack形式は、`pack2hcpe.py`スクリプトによってhcpe形式に変換することができ、dlshogiの学習スクリプトである`train.py`でそのまま用いることができます。

## GenSfenスクリプトの設定

`settings/gensfen-settings.json5`の設定に従って教師を生成します。

思考エンジンのPATHや、教師生成の時の探索ノード数などはここに書きます。

思考エンジンのエンジンオプションは、(やねうら王系であるなら)思考エンジンの実行ファイルと同じフォルダに`engine_options.txt`を配置し、そこに書くことで済ませておいてください。

以下、`engine_options.txt`の例。(やねうら王V9.00の場合)

```C++
option name Threads type spin default 1 min 1 max 128
option name USI_Hash type spin default 128 min 1 max 33554432
option name USI_Ponder type check default false
option name Stochastic_Ponder type check default false
option name NetworkDelay type spin default 120 min 0 max 10000
option name NetworkDelay2 type spin default 1120 min 0 max 10000
option name MinimumThinkingTime type spin default 2000 min 1000 max 100000
option name SlowMover type spin default 100 min 1 max 1000
option name MaxMovesToDraw type spin default 0 min 0 max 100000
option name DepthLimit type spin default 0 min 0 max 2147483647
option name NodesLimit type spin default 0 min 0 max 9223372036854775807
option name EvalDir type string default eval
option name GenerateAllLegalMoves type check default false
option name EnteringKingRule type combo default CSARule27 var NoEnteringKing var CSARule24 var CSARule24H var CSARule27 var CSARule27H var TryRule
option name USI_OwnBook type check default true
option name NarrowBook type check default false
option name BookMoves type spin default 16 min 0 max 10000
option name BookIgnoreRate type spin default 0 min 0 max 100
option name BookFile type combo default no_book var no_book var standard_book.db var yaneura_book1.db var yaneura_book2.db var yaneura_book3.db var yaneura_book4.db var user_book1.db var user_book2.db var user_book3.db var book.bin
option name BookDir type string default book
option name BookEvalDiff type spin default 30 min 0 max 99999
option name BookEvalBlackLimit type spin default 0 min -99999 max 99999
option name BookEvalWhiteLimit type spin default -140 min -99999 max 99999
option name BookDepthLimit type spin default 16 min 0 max 99999
option name BookOnTheFly type check default false
option name ConsiderBookMoveCount type check default false
option name BookPvMoves type spin default 8 min 1 max 246
option name IgnoreBookPly type check default false
option name FlippedBook type check default true
option name DrawValueBlack type spin default -2 min -30000 max 30000
option name DrawValueWhite type spin default -2 min -30000 max 30000
option name PvInterval type spin default 10000000 min 0 max 10000000
option name ResignValue type spin default 99999 min 0 max 99999
option name ConsiderationMode type check default false
option name OutputFailLHPV type check default true
option name FV_SCALE type spin default 36 min 1 max 128
```

⚠ 以下のオプションについて気をつけること。

- `FV_SCALE`は、評価関数ファイルに応じた値にする。
- `BookFile`は、`no_book`を指定し、定跡を用いないようにする。
- `Thread`は、`1`にしてCPUの論理スレッド数だけエンジンを起動する。(このほうが並列化効率が良い)
- `USI_Hash`は、PCの物理メモリが足りる程度に調整。(確保できる範囲でなるべく大きく確保したい)
- `MaxMovesToDraw`は、0を指定。(手数による引き分けの判定はGenSfenのスクリプト側で行うため)
- `NodesLimit`は、指定しても無視される。(USIプロトコルの`go nodes`コマンドを使うが、やねうら王では、`go`コマンドで`nodes`が指定されている時、NodesLimitを無視する)
- `PvInterval`は、大きな値(例えば`10000000`)に設定して、出力されないように抑制したほうがスクリプトの負荷が下がって良い。
- `MultiPV`は、`engine_options.txt`で設定してはならない。やねうら王V9.00以降では、このファイルで設定したオプション値はUSIプロトコルの`setoption`コマンドによって変更できないので、GenSfenのスクリプト側から`MultiPV`を1に変更しようにも変更できない。`MultiPV`は1でなけばレートが下がるので、生成される教師の質が下がる。

## 対局開始局面について

教師生成時の対局開始局面は、`settings/gensfen-settings.json5`に`START_SFENS_PATH`で開始局面を書いたファイルのPATHを指定します。

このファイルは、USIプロトコルの`position`コマンドの文字列で書くことができます。つまり、SFEN形式や`startpos`などが使えます。

例
```C++
// 平手の開始局面
startpos

// 平手の開始局面から76歩を指した局面
startpos moves 7g7f

// 平手の開始局面をSFEN形式で表現したもの。
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL

// 平手の開始局面をSFEN形式で表現したものから76歩を指した局面。
sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL moves 7g7f
```

## 動作に必要なモジュールのインストール

> pip install cshogi json5 tqdm

## NUMAがあるときのエンジン設定について

NUMAがあると、思考エンジンを複数立ち上げたときにそれぞれのNUMAを使ってくれるとは限りません。

そこで、次のようにする方法が考えられます。

NUMA 0で実行させるためのBATファイルを用意します。

- engine0.bat

```bat
cmd.exe /c start /B /WAIT /NODE 0 YO900_AVX2.exe
```

NUMA 1で実行させるためのBATファイルを用意します。

- engine1.bat

```bat
cmd.exe /c start /B /WAIT /NODE 1 YO900_AVX2.exe
```

それぞれのNUMAで思考エンジンを40個ずつ起動したいときは、以下のように`settings/gensfen-settings.json5`に書きます。

```json5
{
    // 対局開始を行う互角局面集のファイルPATH。
    "START_SFENS_PATH": "settings/start_sfens_ply24.txt",

    // 対局の最大手数
    "MAX_GAME_PLY" : 320,

    // 棋譜を生成するときの探索ノード数
    "NODES" : 1000000,

    "ENGINE_SETTING":
    [
        {
            "path":"engines/suisho10/engine0.bat",
            "name":"YO901tune",
            "multi":40
        },
        {
            "path":"engines/suisho10/engine1.bat",
            "name":"YO901tune",
            "multi":40
        },
    ]
}
```

## SSH経由でエンジンを起動するとき

`settings/gensfen-settings.json5`の`"ENGINE_SETTING"`に以下のように書いて、ssh経由で思考エンジンを使うことができます。

```json
    "ENGINE_SETTING":
    [
        {
            "path":"ssh -o ServerAliveInterval=15 9950b suisho10.bat",
            "name":"YO901tune",
            "multi":32
        },
        {
            "path":"ssh -o ServerAliveInterval=15 9950c suisho10.bat",
            "name":"YO901tune",
            "multi":32
        },
    ],
```

# pack2hcpe

`pack2hcpe.py`は、`.pack`形式のファイルを`.hcpe`形式のファイルに変換するスクリプトです。

💡 変換することでファイルサイズは10倍ぐらいに膨らみます。

## pack2hcpeの使い方

> python pack2hcpe.py kif20251110.pack kif20251110.hcpe

また、変換後のファイルPATHは省略できます。

> python pack2hcpe.py kif20251110.pack

この場合、`kif20251110.pack.hcpe`のように、packファイルに拡張子`.hcpe`を付与した名前になります。

## 複数のpackファイルを一括して変換する方法

packファイルはバイナリファイルとみなして結合して一つのファイルにすることができます。

そこで、Windowsのコマンドプロンプトで以下のようにすれば、そのフォルダにあるすべての`.pack`ファイルを一つの`merged.pack`という単一ファイルにまとめることができます。

> copy /B *.pack merged.pack

単一のファイルにしたあとは、前述した`pack2hcpe.py`スクリプトで`.hcpe`形式に変換するとよろしいと思います。

## psv形式に変換する方法

やねうら王系の学習器ではpsv(packed sfen and value)フォーマットが使われていることがあります。

hcpeからpsv形式に変換するには、`dlshogi`の`hcpe_to_psv.py`が使えます。

- [`hcpe_to_psv.py`](https://github.com/TadaoYamaoka/DeepLearningShogi/blob/master/dlshogi/utils/hcpe_to_psv.py)

## やねうら王の評価関数の学習に

やねうら王の評価関数の学習は、やねうら王V9.00からは、やねうら王の学習バージョンである`LEARN版`が廃止になったので、nodchipさんの`nnue-pytorch`などを用いてください。

- 将棋AI用の[nnue-pytorch](https://github.com/nodchip/nnue-pytorch)

TODO : 評価関数の学習上のノウハウについては別途記事にまとめる。

# yanebook2startsfen

`yanebook2startsfen.py`は、やねうら王の定跡ファイルから、開始局面のSFENを書いたテキストフォーマットに変換します。

定跡局面の先端局面(定跡に登録されている局面で、定跡の指し手それぞれを指したあとの局面)を書き出します。

## yanebook2startsfenの使い方

> python yanebook2startsfen user_book1.db start-sfens.txt

また、変換後のファイルPATHは省略できます。省略すると変換元の定跡ファイルPATHに`-startsfens.txt`をつけたものになります。


# pack形式データフォーマット

ここで紹介するpack形式は、ファイルサイズを限りなく小さくする目的でやねうらおが考案しました。

対局棋譜がほぼそのままバイナリーデータとなります。

以下では、

- `:=`は定義。左側の定義が右側に来る。
- `*`は0回以上の繰り返し
- `+`は1回以上の繰り返し
- `:` この右側に説明を書くための記号

を意味するものとします。

また、エンディアンはLE(Little Endian)であるものとします。

💡 比較的BNF記法に近い。BNF記法を参照すること。

pack形式のデータ := 対局棋譜*

対局棋譜 := 開始局面 (指し手 評価値)* 終端記号

開始局面 :=
-    0(1byte) Aperyのhcp形式の局面(32byte) game_ply(2byte) : hcp形式のデータはcshogiで直接扱える
-    1(1byte) : startpos(平手の初期局面)
-    2～(1byte) : 予約。ここは駒落ちなどの初期局面を割り当てるかも。

💡 game_plyはUSIプロトコルのpositionコマンドの末尾にあるもの。平手の開始局面ならば1。

指し手 := AperyのMove16(2byte) : cshogiで直接扱える

評価値 := 符号付き16bit整数(2byte)

終端記号 :=
- 0x0000(2byte) 終局理由(1byte) : 引き分け。
- 0x0081(2byte) 終局理由(1byte) : BLACK(先手側)の勝ち。📝 `0x0081 == (1 + (1 << 7))`
- 0x0102(2byte) 終局理由(1byte) : WHITE(後手側)の勝ち。📝 `0x0102 == (2 + (2 << 7))`

💡 指し手(Move16)としてみたときに0x0000(2byte)は将棋盤の11の駒を11に移動させる指し手なのでこのような指し手は存在しない。0x0081, 0x0102も同様。なので、指し手と区別がつく。

📝 終端記号は、hcpeのgame resultに準拠させてある。

終局理由 :=

- 0(1byte) resign : 投了。
- 1(1byte) draw : 千日手引き分け。
- 2(1byte) max moves : 最大手数による引き分け。
- 3(1byte) interrupt : (システムによる)対局の中断による引き分け。
- 4(1byte) time up : 時間切れ負け。非手番側の勝ち。
- 5(1byte) illegal move : 反則手。非手番側の勝ち。
- 6(1byte) repetition check : 連続王手の千日手による反則負け。非手番側の勝ち。

- 10(1byte) win csa24 : 入玉宣言勝ち。24点法。(31点以上)
- 11(1byte) draw csa24 : 入玉宣言による引き分け。24点法。(24点以上30点以下)
- 12(1byte) win csa27 : 入玉宣言勝ち。27点法。(先手28点、後手27点以上)
- 13(byte) win try_rule : トライルールによる宣言勝ち。

- それら以外(1byte) reserved : 予約


