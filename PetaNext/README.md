# PetaNext

ペタショック化されたやねうら王形式の定跡ファイルから、**次に思考エンジンで掘ると良い leaf node(末端の局面) までの手順**と、その leaf の sfen(局面文字列)を書き出すスクリプト。

次の記事も参考にすること。

- [makebook peta_shock - やねうら王Wiki 定跡の作成](https://github.com/yaneurao/YaneuraOu/wiki/%E5%AE%9A%E8%B7%A1%E3%81%AE%E4%BD%9C%E6%88%90)

ペタショック化がなされた、やねうら王の定跡ファイルに対して、次に定跡を掘っていくと良い局面をリストアップするスクリプトが本スクリプトである。本スクリプトは、2025年5月まで、やねうら王本体内蔵の定跡コマンドであったが、やねうら王本体に内包しておくとカスタマイズがしにくいため、Pythonで書き直すことにした。

# アルゴリズム

例えば先手の定跡を延長する場合、root(平手の開始局面)から、
1. 先手は定跡の指し手を指す
2. 後手は定跡の指し手から(bestmoveの評価値から)BookEvalDiffの範囲の評価値の指し手のいずれかを指す。
と仮定したときの、leaf nodeの集合を延長対象の局面として選ぶ。

しかし、これだと、BookEvalDiff分だけ下がり続けるとかなーり悪い評価値のleaf nodeに到達してしまう。実際はそんな悪いleaf nodeになるような手順を後手は選ばないはずである。

そこで2. は、
> 2a. rootのbestmoveの評価値からBookEvalDiffの範囲の評価値の指し手のいずれかを指す

と制限をする。これにより延長対象のleaf nodeを大幅に減らすことができる。


## 本スクリプトの動作

ペタショック化された定跡 (`peta_book.db` 形式) を読み、root 局面から BFS で展開して、定跡ツリーの leaf に当たる局面までの `startpos moves ...` 形式の手順と、ply付きSFENの両方を書き出す。

展開規則:

- 「**定跡側**手番」 (例: 先手定跡なら先手番) では、bestmove だけを辿る。
- 「**非定跡側**手番」では、bestmove だけでなく、`root の bestmove の評価値 - peta_eval_diff` **以上** の評価値を持つ指し手すべてを辿る（下限のみ。bestmove より上は元々存在しないので無制限）。
- peta_book に登録されていない局面に出たら、それを leaf として記録する。

`root の bestmove からの絶対基準` で評価値の下限を制限することで、BFS 深さ方向に累積で評価値が下がり続けて、現実的でない leaf に到達することを防いでいる。

書き出された leaf までの手順を、思考エンジン側 (例: BookMiner などのワーカー) に流し込んで掘らせるのが想定ワークフロー。局面集合として扱いたい場合や `sfen_to_hcp.py` に渡す場合は、同時に出力される `-sfen` 付きファイルを使う。

ペタショック化自体 (= 定跡ツリーを min-max で内部ノードの評価値を書き換える処理) は本スクリプトでは行わない。やねうら王本体の `makebook peta_shock` コマンドを使う。

参考:
- [makebook peta_shock - やねうら王Wiki 定跡の作成](https://github.com/yaneurao/YaneuraOu/wiki/%E5%AE%9A%E8%B7%A1%E3%81%AE%E4%BD%9C%E6%88%90#makebook-peta_shock)

## インストール

依存パッケージ:

```
pip install cshogi tqdm
```

- `cshogi` — 局面操作・USI 指し手変換
- `tqdm` — 定跡ファイル読み込みの progress bar

## 使い方

カレントディレクトリに `peta_book.db` を置いて:

```
python peta_next.py
```

`--peta-book` の path や `--peta-eval-diff` を変えたり、`--root-sfen` で開始局面を指定する例:

```
python peta_next.py --peta-book some_book.db --peta-eval-diff 50 --root-sfen root_sfen.txt
```

主なオプション:

| オプション | デフォルト | 説明 |
|---|---|---|
| `--peta-book` | `peta_book.db` | ペタショック化済みのやねうら王定跡ファイル |
| `--root-sfen` | — (未指定なら `startpos` のみ) | 開始局面ファイル。**指定しなかった場合は `startpos`（平手の開始局面）のみを root として動作する** |
| `--out-dir` | `.` | 出力ディレクトリ |
| `--peta-eval-diff` | `10` | BookEvalDiff (cp)。root の bestmove eval からこの幅まで非手番側の指し手を辿る (下限のみ) |
| `--max-ply` | `200` | BFS で掘る最大手数 (ply)。これを超える局面は展開しない |
| `--interactive` | off | 対話モードで起動する。peta_book を一度だけ読み込み、`n` コマンドを繰り返し実行できる |
| `--json-lines` | off | 対話モードをJSON Linesで制御する。BookMinerから使うための指定 |
| `--verbose` | off | 対話モードでも root/step ログを表示する |
| `--black-only` | off | 先手定跡 (turn=1) のみ出力 |
| `--white-only` | off | 後手定跡 (turn=0) のみ出力 |

## 対話モード

候補数を見ながら `peta_eval_diff` と `max_ply` を調整したい場合は、対話モードを使う。

```
python peta_next.py --peta-book peta_book.db --interactive
```

起動時にペタショック化済みbookを一度だけ読み込む。その後は `n` コマンドで候補を列挙する。

```
PetaNext> n 200 64
PetaNext> n 100 64
PetaNext> n 100 48
```

`n` は候補数を表示し、直近結果をメモリ上に保持するだけで、ファイルには書き出さない。候補数がよければ `w` で書き出す。

```
PetaNext> w
```

対話モードのコマンド:

| コマンド | 説明 |
|---|---|
| `n [peta_eval_diff] [max_ply]` | 候補を列挙して件数を表示する。省略した値は直前の値を使う |
| `w` | 直近の `n` の結果を `think_sfens*.txt` に書き出す |
| `status` | 読み込み済み局面数、root数、現在の値を表示する |
| `h` | ヘルプを表示する |
| `q` | 終了する |

## 入出力ファイル

### root sfen ファイルの形式

`--root-sfen` で指定するファイル。指定しなかった場合はファイルを読まず、`startpos` (平手の開始局面) のみを root として動作する。

各行が 1 つの root 局面を表す。空行と `#` で始まる行は無視される。

受け付ける形式:

- `startpos`
- `startpos moves 7g7f 3c3d ...`
- `sfen <BOARD> <TURN> <HAND> [<PLY>]`
- `sfen <BOARD> <TURN> <HAND> <PLY> moves ...`
- 行頭に `position ` が付いていてもよい
- 直接 SFEN 文字列のみでもよい

サンプルは [root_sfen.txt](root_sfen.txt)。

### 出力ファイル

- `think_sfens-black.txt` — 先手定跡用 leaf までの `startpos moves ...` / `sfen ... moves ...` 一覧（`--white-only` 指定時は出力されない）
- `think_sfens-white.txt` — 後手定跡用 leaf までの `startpos moves ...` / `sfen ... moves ...` 一覧（`--black-only` 指定時は出力されない）
- `think_sfens.txt` — 上記 2 ファイルを 1 行ずつ交互にマージしたもの（両方出力した時のみ作成）
- `think_sfens-black-sfen.txt` — 先手定跡用 leaf の ply付きSFEN一覧
- `think_sfens-white-sfen.txt` — 後手定跡用 leaf の ply付きSFEN一覧
- `think_sfens-sfen.txt` — 上記 2 つのSFENファイルを 1 行ずつ交互にマージしたもの（両方出力した時のみ作成）

デフォルトの `think_sfens*.txt` は、BookMiner の `t` コマンドに渡しやすい手順形式です。`*-sfen.txt` は各行が ply 付き SFEN (`<board> <turn> <hand> <ply>`) です。

## 派生スクリプトを書く時の参考

延長アルゴリズムの考え方の整理、思考側の運用パラメータ (基準ノード数、MultiPV 動的拡張) などは、やねうら王プロジェクト側のドキュメントを参照。
