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

例えば eval limit が `400` のとき、BookMiner は評価値の絶対値が `400` を超える枝を、定跡木の外へ延長しません。

これは、棋譜を無条件に末端まで辿るという意味ではありません。
棋譜の指し手を順に再生している途中でも、その手が定跡木の外へ出る枝で、かつ評価値が eval limit を超えていれば、そこで処理を打ち切ります。

## eval_refutation_margin

`eval_refutation_margin` は、peta shock 後に反駁候補を延長したときに消えうる評価値インパクトの閾値です。

```text
peta shock後の反駁候補手評価値 - peta shock後の旧best手評価値 >= eval_refutation_margin
```

を満たすものだけを `peta_refutation` の抽出対象にします。

## 局面を掘る

このチュートリアルでは、「局面を掘る」とは、その局面をエンジンに思考させ、候補手と評価値を定跡 DB に保存することを意味します。

## 定跡

ここでいう定跡とは、局面ごとに「この局面ではどの指し手を選ぶべきか」「その指し手の評価値はいくつか」を保存したデータです。

BookMiner は、この定跡データを増やしていきます。

定跡は、1本のリストではなく、局面から指し手で枝分かれする木として考えると理解しやすいです。

![定跡木と leaf からの延長](assets/book-tree-leaf-extension.svg)

## 定跡 DB

定跡 DB は、定跡をファイルに保存したものです。

BookMiner では主に次のファイルが出てきます。

- `book/backup/book_miner-....ybb` : BookMiner が読み書きする通常定跡 DB。既存の `.db` 形式も読み込めます。
- `book/backup/peta_book-....ybb` : peta shock 化の結果として作られる定跡 DB。peta shock の出力拡張子は `.ybb` 固定です。

## 通常定跡 DB

変換前の定跡 DB です。

ファイル名は次のようになります。

```text
book/backup/book_miner-20260607071000_12345.ybb
```

`20260607071000` の部分は書き出した時刻、`_12345` の部分は書き出された局面数です。


## やねうら王の定跡フォーマット

やねうら王が読み込める定跡ファイル形式です。BookMiner が新しく書き出す通常定跡と peta book は `.ybb` 形式です。既存の `.db` テキスト形式も読み込めます。

- [将棋ソフト用の標準定跡ファイルフォーマットの提案](https://yaneuraou.yaneu.com/2016/02/05/standard-shogi-book-format/)
- [定跡の作成 - やねうら王Wiki](https://github.com/yaneurao/YaneuraOu/wiki/%E5%AE%9A%E8%B7%A1%E3%81%AE%E4%BD%9C%E6%88%90)

既存の `.db` 形式は、拡張子が `.db` であっても、実体はテキストファイルです。先頭にはフォーマット識別用のヘッダーがあり、そのあと `sfen ...` で始まる局面ブロックが並びます。各局面ブロックには、その局面で選べる指し手、相手の応手、評価値などが書かれます。

既存または外部作成の `.db` テキスト形式には、次のような特徴があります。

- 先頭に `#YANEURAOU-DB2016 1.00` を書きます。
- 2行目に `# NOE:<局面数>` を書きます。NOE は Num Of Entries、つまりDB上の局面数です。
- `sfen` 文字列で sort した順に局面を書き出します。
- 評価値のある指し手だけを書き出します。
- 各局面の指し手は、手番側から見て評価値の良い順に並べます。
- 相手応手は `none` として書き出します。

通常の BookMiner 運用では、これらをユーザーが手で調整する必要はありません。
ただし、外部の定跡 DB を BookMiner に持ち込む場合は、やねうら王が読める形式であること、また `makebook peta_shock` に渡せるよう `sfen` 順に sort されていることを確認してください。

## 評価関数

将棋エンジンが局面の良し悪しを数値化するために使う仕組みです。

やねうら王系エンジンでは、評価関数ファイルや評価関数の種類に応じて `EvalDir` や `FV_SCALE` などの設定が必要になることがあります。


## peta shock 化

peta shock 化は、やねうら王の `makebook peta_shock` コマンドです。

通常定跡 DB を後ろから解析し、leaf 側の評価値を min-max で root 側へ伝播させた peta_book を作ります。
BookMiner では、次に掘る leaf を探す `peta_next`、反駁された leaf を探す `peta_next_refutation`、反駁候補を探す `peta_refutation`、depth差で延長候補を探す `peta_depth_gap`、負け棋譜周辺を掘る `peta_unsolved`、過去配布定跡への対策候補を掘る `peta_opponent`、対局用定跡の作成に使います。

詳しくは [10. peta shock 化](10-peta-shock.md) を参照してください。

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

図のように、途中の内部ノードは単に通過する局面です。
BookMiner が `eval limit` で延長するかどうかを決めるのは、leaf から外へ出る枝です。

## PV line

PV line(Principal Variation line)とは、エンジンが最善だと判断した指し手(最善手)を辿った読み筋(最善手順)のことです。

BookMiner は、棋譜の指し手を eval limit で止まらずに末端まで辿れた場合、PV line に沿って追加で数手分掘ることがあります。
`peta_depth_gap` では、条件を満たした候補手を指したあと、peta_book 上の best PV を leaf まで辿った局面を書き出します。

## root

次に掘る局面を探すときの開始局面です。

## best move

ある局面で、エンジンまたは定跡 DB が最善と判断した指し手です。


## ply

初期局面から数えた手数です。

## peta_next

peta shock 化した定跡 DB から、leaf の先へ定跡ツリーを伸ばすための局面を求める処理です。

## peta_next_refutation

`peta_next` の leaf のうち、定跡から抜ける最後の1手が peta shock 前の通常bookでは best ではなかったものだけを抽出する処理です。

BookMiner の CLI では `pnf eval_diff [eval_refutation_margin] [max_step] [max_book_ply] [book_extend_ply]`、GUI では `peta next refu.` ボタンに対応します。抽出結果は `book/think_sfens.txt` に書き出されます。

## 反駁

peta shock 化によって、peta shock 前は2番手以下だった指し手が best に入れ替わることです。

反駁された指し手が depth 0 の場合、その先はまだ十分に延長されていない可能性があります。この評価値が root 側へ伝播するとノイズになり得るため、`peta_refutation` で追加探索候補として抽出します。

## peta_refutation

peta shock 後に best になっている depth 0 の指し手のうち、peta shock 前は2番手以下で、peta shock後の旧best手との差が `eval_refutation_margin` 以上あるものを抽出する処理です。

BookMiner の CLI では `pf [eval_refutation_margin] [eval_limit] [max_book_ply] [book_extend_ply]`、GUI では `peta refutation` ボタンに対応します。抽出結果は `book/think_sfens.txt` に書き出されます。GUIでは同じ行の `eval_limit` を使い、enqueue 時に retire することが確定している候補を事前に除外します。

## peta_depth_gap

peta shock 後に、best以外の登録済み指し手が best より浅く、depth差ぶん延長すると best を逆転しうる場合に抽出する処理です。

BookMiner の CLI では `pd [eval_per_ply] [max_book_ply] [book_extend_ply]`、GUI では `peta depth_gap` ボタンに対応します。抽出結果は、その候補手のPV leafとして `book/think_sfens.txt` に書き出されます。

## peta_unsolved

`book/think_unsolved_sfens.txt` にある棋譜の各prefix局面から、peta_book 上の best PV を leaf まで辿り、次に掘る局面として `book/think_sfens.txt` に書き出す処理です。

BookMiner の CLI では `pu [eval_diff] [max_step] [max_book_ply] [book_extend_ply]`、GUI では `peta unsolved` ボタンに対応します。`None` を指定するとデフォルト値を使います。書き出し後の enqueue は手動で実行します。

## peta_opponent

`book/book_opponent/` に置いた過去配布定跡などを仮想敵とし、現在読み込んでいる peta_book と best 進行を辿って、相手定跡が切れる周辺の leaf を `book/think_sfens.txt` に書き出す処理です。

BookMiner の CLI では `po [eval_diff] [max_step] [max_book_ply] [book_extend_ply]`、GUI では `peta opponent` ボタンに対応します。`book_extend_ply` を指定すると、書き出し行に `book_extend_ply=...` が付き、その行だけ enqueue 時の best line 延長手数を上書きします。

## KifManager

KifManager は、棋譜ファイルから BookMiner 用の `startpos moves ...` 形式の入力ファイルを作るためのツールです。

BookMiner で最初に掘る局面集合は、通常 [YaneuraOu-ScriptCollection/KifManager](../../KifManager/README.md) で作ります。

KifManagerでは、floodgate、電竜戦、WCSCなどの棋譜や、任意のCSA,KIF形式のファイルから条件で絞った棋譜を`startpos moves ...`形式で抽出できます。


## engine_options.txt

やねうら王系エンジンの設定ファイルです。

やねうら王エンジンの実行ファイルと同じフォルダに置くと、`isready` 時に読み込まれます。
