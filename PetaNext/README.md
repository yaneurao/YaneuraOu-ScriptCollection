# PetaNext

ペタショック化されたやねうら王形式の定跡ファイルから、**次に思考エンジンで掘ると良い leaf node の sfen** を書き出すスクリプト。

これは 2025 年 5 月までやねうら王本体に内蔵されていた定跡コマンドを、カスタマイズしやすくするために Python に移植したもの。

## 何をするか

ペタショック化された定跡 (`peta_book.db` 形式) を読み、root 局面から BFS で展開して、定跡ツリーの leaf に当たる sfen 一覧を書き出す。

展開規則:

- 「**定跡側**手番」 (例: 先手定跡なら先手番) では、bestmove だけを辿る。
- 「**非定跡側**手番」では、bestmove だけでなく、`root の bestmove の評価値 - peta_eval_diff` **以上** の評価値を持つ指し手すべてを辿る（下限のみ。bestmove より上は元々存在しないので無制限）。
- peta_book に登録されていない局面に出たら、それを leaf として記録する。

`root の bestmove からの絶対基準` で評価値の下限を制限することで、BFS 深さ方向に累積で評価値が下がり続けて、現実的でない leaf に到達することを防いでいる。

書き出された leaf sfen を、思考エンジン側 (例: BookMiner などのワーカー) に流し込んで掘らせるのが想定ワークフロー。

ペタショック化自体 (= 定跡ツリーを min-max で内部ノードの評価値を書き換える処理) は本スクリプトでは行わない。やねうら王本体の `makebook peta_shock` コマンドを使う。

参考:
- [makebook peta_shock - やねうら王Wiki 定跡の作成](https://github.com/yaneurao/YaneuraOu/wiki/%E5%AE%9A%E8%B7%A1%E3%81%AE%E4%BD%9C%E6%88%90#makebook-peta_shock)

## 使い方

```
python peta_next.py --peta-book peta_book.db --peta-eval-diff 100
```

主なオプション:

| オプション | デフォルト | 説明 |
|---|---|---|
| `--peta-book` (必須) | — | ペタショック化済みのやねうら王定跡ファイル |
| `--root-sfen` | — (未指定なら `startpos` のみ) | 開始局面ファイル。**指定しなかった場合は `startpos`（平手の開始局面）のみを root として動作する** |
| `--out-dir` | `.` | 出力ディレクトリ |
| `--peta-eval-diff` (必須) | — | BookEvalDiff (cp)。root の bestmove eval からこの幅まで非手番側の指し手を辿る |
| `--max-step` | `9999` | BFS の最大深さ |
| `--black-only` | off | 先手定跡 (turn=1) のみ出力 |
| `--white-only` | off | 後手定跡 (turn=0) のみ出力 |

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

- `think_sfens-black.txt` — 先手定跡用 leaf sfen 一覧（`--white-only` 指定時は出力されない）
- `think_sfens-white.txt` — 後手定跡用 leaf sfen 一覧（`--black-only` 指定時は出力されない）
- `think_sfens.txt` — 上記 2 ファイルを 1 行ずつ交互にマージしたもの（両方出力した時のみ作成）

各行は ply 付き SFEN (`<board> <turn> <hand> <ply>`)。

## 派生スクリプトを書く時の参考

延長アルゴリズムの考え方の整理、思考側の運用パラメータ (基準ノード数、MultiPV 動的拡張) などは、やねうら王プロジェクト側のドキュメントを参照。
