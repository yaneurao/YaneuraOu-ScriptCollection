# 1. 用語説明

この章では、BookMiner の説明で使う用語を先に定義します。

## 将棋エンジン

将棋の局面を受け取り、次に指す手や局面の評価を返すプログラムです。

BookMiner は将棋エンジンそのものではありません。BookMiner は外部の将棋エンジンを起動し、そのエンジンに局面を思考させます。

## USI

USI は、将棋エンジンと GUI やツールが通信するためのプロトコルです。

- [USIプロトコルとは](https://shogidokoro2.stars.ne.jp/usi.html)

BookMiner は USI コマンドを使って将棋エンジンに局面を渡し、探索結果を受け取ります。

## 探索用のエンジン

探索用のエンジンは、USI プロトコルで操作できる将棋エンジンです。(やねうら王など。)

なるべく強いエンジンを使うほうが良いです。

## BookMiner

BookMiner は、多くの局面を USI エンジンに思考させて、やねうら王で使える大規模な定跡ファイルを作るための Python スクリプトです。

## 評価値

評価値は、エンジンが局面や指し手の良し悪しを数値で表したものです。

正の値は手番側が良い、負の値は手番側が悪い、という扱いで使います。

## eval limit

eval limit は、局面を掘る処理を止めるための評価値の閾値です。

例えば eval limit が `400` のとき、評価値の絶対値が `400` 以上になった対局行は、そこで処理を打ち切ります。

## 局面を掘る

このチュートリアルでは、「局面を掘る」とは、その局面をエンジンに思考させ、候補手と評価値を定跡 DB に保存することを意味します。

## 定跡

ここでいう定跡とは、局面ごとに「この局面ではどの指し手を選ぶべきか」「その指し手の評価値はいくつか」を保存したデータです。

BookMiner は、この定跡データを増やしていきます。

## 定跡 DB

定跡 DB は、定跡をファイルに保存したものです。

BookMiner では主に次のファイルが出てきます。

- `book/backup/book_miner-....db` : BookMiner が読み書きする通常定跡 DB。
- `book/backup/peta_book-....db` : peta shock 化の結果として作られる定跡 DB。

## 通常定跡 DB

変換前の定跡 DB です。

ファイル名は次のようになります。

```text
book/backup/book_miner-20260607071000_12345.db
```

`20260607071000` の部分は書き出した時刻、`_12345` の部分は書き出された局面数です。


## やねうら王の標準定跡フォーマット

やねうら王が読み込めるテキスト形式の定跡ファイル形式です。

- [将棋ソフト用の標準定跡ファイルフォーマットの提案](https://yaneuraou.yaneu.com/2016/02/05/standard-shogi-book-format/)

BookMiner の `book/backup/book_miner-....db` と `book/backup/peta_book-....db` は、この形式で保存されます。

## 評価関数

将棋エンジンが局面の良し悪しを数値化するために使う仕組みです。

やねうら王系エンジンでは、評価関数ファイルや評価関数の種類に応じて `EvalDir` や `FV_SCALE` などの設定が必要になることがあります。


## peta shock 化

peta shock 化は、やねうら王の `makebook peta_shock` コマンドです。

これは、通常定跡 DB を後ろから解析し、定跡として使いやすい DB に変換する処理です。もう少し専門的に言うと、定跡ツリーでmin-max探索した結果と同じ結果にした定跡DBを作ります。


## MATERIAL 版

駒得などの簡易な評価を使う、評価関数ファイルを必要としないやねうら王のビルドです。
peta shock化を行うために用います。

MATERIAL版を用いるのは、フットプリント(使用メモリ)が少ないためです。


## YO-MATERIAL.exe

`YO-MATERIAL.exe` は、BookMiner が peta shock 化のために起動する MATERIAL 版やねうら王です。

探索用エンジンとは別に、`BookMiner.py` と同じフォルダに置きます。

## `position` コマンド

USI でエンジンに局面を指定するためのコマンドです。USIプロトコルの`position`コマンドの説明を参照してください。

- [USIプロトコルとは](https://shogidokoro2.stars.ne.jp/usi.html)

BookMiner の入力ファイルでは、`position` という単語自体は省略し、次のような形で局面列を書きます。

```text
startpos moves 7g7f 3c3d 2g2f
```

## `startpos moves ...`

平手初期局面から、指定した指し手を順に進めた局面列を表す書き方です。

```text
startpos moves 7g7f 3c3d 2g2f
```

これは「初期局面から `7g7f`、`3c3d`、`2g2f` と指した」という意味です。

BookMiner の入力ファイルでは、1 行を 1 対局としてこの形式で書きます。


## SFEN

SFEN は、将棋の任意の局面を 1 行の文字列で表す形式です。詳しくは、USIプロトコルの説明をご覧ください。

- [USIプロトコルとは](https://shogidokoro2.stars.ne.jp/usi.html)

`startpos moves ...` は初期局面からの指し手列で局面を表します。一方、SFEN はある時点の盤面、手番、持ち駒などを直接表します。

通常の BookMiner 運用では、KifManager で作った `startpos moves ...` 形式の入力を使えば十分です。

## leaf 局面(leaf node)

leaf 局面(leaf node)とは、定跡ツリーの末端にある局面のことです。

BookMiner では、すでに掘った定跡ツリーの末端を leaf と呼びます。
leaf 自体は定跡ツリー上の局面なので、通常はすでに評価値を持っています。
BookMiner は、この leaf から外へ定跡ツリーを伸ばすための局面を探します。

## PV line

PV line(Principal Variation line)とは、エンジンが最善だと判断した指し手(最善手)を辿った読み筋(最善手順)のことです。

BookMiner は、棋譜の末端まで到達したあと、PV line に沿って追加で数手分掘ることがあります。

## root

次に掘る局面を探すときの開始局面です。

## best move

ある局面で、エンジンまたは定跡 DB が最善と判断した指し手です。


## ply

初期局面から数えた手数です。

## peta_next

peta shock 化した定跡 DB から、leaf の先へ定跡ツリーを伸ばすための局面を求める処理です。

## KifManager

KifManager は、棋譜ファイルから BookMiner 用の `startpos moves ...` 形式の入力ファイルを作るためのツールです。

BookMiner で最初に掘る局面集合は、通常 [YaneuraOu-ScriptCollection/KifManager](../../KifManager/README.md) で作ります。

KifManagerでは、floodgate、電竜戦、WCSCなどの棋譜や、任意のCSA,KIF形式のファイルから条件で絞った棋譜を`startpos moves ...`形式で抽出できます。


## engine_options.txt

やねうら王系エンジンの設定ファイルです。

やねうら王エンジンの実行ファイルと同じフォルダに置くと、`isready` 時に読み込まれます。
