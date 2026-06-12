# 6. BookMinerCpp 設計メモ

BookMinerCpp は、`BookMiner.py` と同じ運用を C++ で再実装するためのプロジェクトです。

## 採用方針

- GUIは作らない。既存の `BookMiner-gui.py` を使う。
- C++版は `BookMiner.py` と同じ stdin/stdout ベースのCLIとして動作する。
- `BookMiner-gui.py` から使えるよう、`--from_gui`、進捗タグ、コマンド体系を Python版と揃える。
- KifManager は既存の Python 版を使う。C++側では直接扱わない。
- やねうら王のソースコードは流用してよい。
- やねうら王の `Position` / 指し手生成 / 合法手判定に必要な部分は `yaneuraou_core.lib` としてビルドし、BookMinerCpp本体からリンクする。
- 定跡DBの key は SFEN 文字列ではなく、やねうら王側の packed sfen 変換を使う。
- 指し手は `Move16` 相当の 2 byte 値で保持する。
- 評価値は `int16_t` で保持する。BookMiner.py 側と同様、mate score 変換後に clamp する前提。
- 定跡DBは LSM-tree 風に保持する。新規局面は memtable に入り、一定件数で `PackedSfen` 順の sorted run へ flush する。
- `.ybb` 書き出し時は sorted run 群を n-way merge しながら出力する。

## 現在の実装範囲

`source/` には、CLI REPL、探索 worker、C++版定跡DB、やねうら王 core adapter を置いています。

現時点では以下を実装しています。

- `--from_gui`
- 起動時の `book/backup/book_miner-*.ybb` 読み込み
- 互換用の `book/backup/book_miner-*.db` 読み込み
- やねうら王標準定跡フォーマットの読み込み
- やねうら王 バイナリ定跡DB (`.ybb`) の読み込み
- `w` / `q` による `book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb` 書き出し
- `*.ybb.tmp` への一時書き出し後に正式名へ置換する安全な保存
- `r` による既存 `peta_book-....db` / `peta_book-....ybb` の読み込み
- `p` による通常DB書き出し、peta shock 化、peta book 読み込みの一括実行
- 定期自動保存サービス
- `settings/engine_settings.json5` の読み込み
- USIエンジンの起動、`usi` / `isready` 待ち
- `go nodes` の `info score ... pv ...` パース
- `t` による `think_sfens.txt` 読み込み
- worker thread queue による複数USIエンジンでの探索
- `startpos moves ...` のやねうら王 `Position` による展開
- `Position::to_move()`、`pseudo_legal_s<true>()`、`legal()` による合法手チェック
- `MoveList<LEGAL>` による合法手生成
- 棋譜上の局面、定跡木から外へ出る枝の `eval_limit` 判定、leaf からの best line 延長
- `n` による peta_next
- `book/peta_start_sfens.txt` などの開始局面集合ファイル読み込み
- `book/think_sfens-black.txt` / `book/think_sfens-white.txt` / `book/think_sfens.txt` の書き出し
- `Move16` 相当の 2 byte 指し手表現
- `int16_t` 評価値
- `PositionInfo` から常時保持するSFEN文字列を撤去し、局面keyは `PackedSfen`、手数は `uint16_t ply` として保持
- BookStore 内部は memtable と sorted run 群で保持
- やねうら王標準定跡フォーマットへの書き出し時だけ `PackedSfen` からSFEN文字列を復元してsort
- `.ybb` への書き出し時は sorted run 群を `PackedSfen` の32 byte順で n-way merge し、固定長index領域と可変長moves領域を `.ybb` に保存
- packed sfen の直接 flip
- BookMiner-gui.py が拾うための book read/write 進捗タグ

## GUI連携

C++版も `BookMiner.py` と同じ stdin/stdout ベースのCLIとして動作します。
既存の `BookMiner-gui.py` は、通常はPython版を起動しますが、`--cpp` を指定すると `../BookMinerCpp/BookMinerCpp.exe --from_gui` を起動します。

```bash
python BookMiner-gui.py --cpp
```

GUIが解釈する進捗タグはPython版と揃えます。
自動保存については `[BackupServiceStarted]`、`[BackupNext]`、`[BackupStart]`、`[BackupDone]` を出力します。

## 自動保存

`settings/book_miner_settings.json5` の `auto_save_interval_seconds` に従って、定期的に `book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb` を書き出します。
手動保存、`p` コマンド、自動保存が同時に走る可能性があるため、保存処理はC++側で直列化します。

## worker queue

`t [path]` は `startpos moves ...` 形式の各行を `TaskQueue` に積み、起動済みUSIエンジンごとに1本のworker threadが処理します。
workerは `TaskQueueProgress` を、前回出力から約10秒以上経過したとき、または残りtask数が0になったときに出します。

進捗の `done` は「workerがtaskを取り出した数」です。
そのため `[TaskQueueDone]` が出たあとも、最後に取り出したtaskの探索ログや `[MiningProgress]` が少し続くことがあります。

定跡DBは `BookStore` が保持し、workerは局面情報をcopyで読み、探索結果をmergeで書き込みます。
同一局面またはflip同一局面を複数workerが同時に探索しないよう、`BookStore` 内で探索中局面をleaseとして管理します。

## 定跡DBのLSM-tree構造

`BookStore` は定跡DBを1個の巨大な `unordered_map` ではなく、memtable と sorted run 群で保持します。

- memtable: 新規局面を受け取る mutable な `unordered_map<PackedSfen, PositionInfo>`
- sorted run: `PackedSfen` の32 byte辞書順にsort済みの `BookEntry` 配列
- searching set: worker間で同一局面またはflip同一局面を二重探索しないための集合

局面を追加するときは、まず memtable と run 群を検索します。
既存局面ならその場で指し手情報をmergeし、新規局面なら memtable に追加します。
memtable が一定件数に達すると、`PackedSfen` 順にsortした run として flush します。
同じサイズ帯の run が2本できた場合は、古いrunと新しいrunをmergeして1本に compact します。

`.ybb` 書き出しでは、run 群と一時的にsortした memtable run を n-way merge しながら、index 領域と moves 領域へ直接書き出します。
これにより、`.ybb` 保存時に全局面を別の巨大な配列へ詰め替える必要がありません。

やねうら王標準定跡 `.db` はSFEN文字列順で出力する必要があるため、書き出し時だけ `PackedSfen` からSFEN文字列を復元し、その文字列でsortします。
通常運用のバックアップは `.ybb` なので、このSFEN文字列復元は手動で `.db` を出す場合だけ発生します。

## peta shock 化

C++版は peta shock 化のアルゴリズムを再実装しません。
Python版と同様に、`YO-MATERIAL.exe` を子プロセスとして起動し、次のコマンドを送ります。

```text
setoption name BookDir value book
setoption name BookFile value no_book
setoption name FlippedBook value true
setoption name USI_Hash value 1
makebook peta_shock backup/book_miner-....ybb backup/peta_book-....ybb.tmp
quit
```

生成先は一度 `*.tmp` とし、成功後に正式名へ置換します。
正式な `YO-MATERIAL.exe` の配置場所は `BookMinerCpp/YO-MATERIAL.exe` です。
開発時のみ `../BookMiner/YO-MATERIAL.exe` もフォールバックとして探します。

やねうら王側にも `.ybb` reader / writer を追加し、通常の `BookFile=user_book.ybb` と、`makebook peta_shock` の入力・出力の両方で使えるようにします。
peta shock 後の出力形式は入力形式に合わせます。

## peta_next

`n peta_eval_diff [max_step]` は、メモリに読み込まれている peta book を辿り、次に掘る局面を `book/think_sfens.txt` に書き出します。
Python版と同様に、black側とwhite側を個別に辿ってから、次の3ファイルを書き出します。

```text
book/think_sfens-black.txt
book/think_sfens-white.txt
book/think_sfens.txt
```

開始局面集合は `settings/book_miner_settings.json5` の `peta_next_start_sfens_path` で指定します。
このファイルが存在しない場合は `startpos` から辿ります。
開始局面集合ファイルの各行は `startpos moves ...` など、USIの `position` コマンドから `position ` を除いた形式です。

定跡は `FlippedBook` 前提で先手番局面だけが入っていることがあるため、peta_next中の局面検索では、元局面の `PackedSfen` と、それを直接 flip した `PackedSfen` の両方を調べます。
flip hitした場合の指し手は、やねうら王の `flip_move(Move16)` で元局面側の指し手に戻してから辿ります。

## やねうら王 core library

`source/Makefile` は、BookMinerCpp本体とは別に次の静的ライブラリを作ります。

```text
source/build/yaneuraou/yaneuraou_core.lib
```

このライブラリには、やねうら王の `Position`、`MoveList<LEGAL>`、`pseudo_legal_s<true>()`、`legal()`、SFEN packer、駒割り評価に必要なソースと、BookMinerCpp用の薄いadapterを含めます。
BookMinerCpp本体側は、この `.lib` をリンクして `startpos moves ...` の展開、合法手チェック、合法手生成を行います。

## PackedSfen について

`BookStore` の key 型は `PackedSfen` にしています。
現在は adapter 側でやねうら王の `Position::sfen_pack()` を呼び、その32 byteをそのまま key にしています。
BookMinerCpp本体側は `YaneuraOu::PackedSfen` の型を直接見ず、32 byte配列として扱います。
これにより、BookMinerCpp本体とやねうら王core libraryの境界を保ちながら、key表現はやねうら王と一致します。

packed sfen の flip は、やねうら王側の `PackedSfen::flip()` / `PackedSfen::flipped()` を adapter 経由で呼びます。
この処理はSFEN文字列や `Position::set()` を経由せず、packed sfen を raw board / hand / turn に展開して反転し、再 pack します。

`PositionInfo` はSFEN文字列を常時保持しません。
通常時は BookStore の memtable / sorted run にある `PackedSfen` key が局面を表し、`PositionInfo` は `ply` と `moves` だけを保持します。
やねうら王標準定跡フォーマットへ書き出すときだけ、adapter側で `Position::sfen_unpack()` を呼んでSFEN文字列を復元し、その文字列でsortしてから出力します。
`PackedSfen` には手数が含まれないため、`ply` は `PositionInfo` 側に `uint16_t` として保持します。

## やねうら王 バイナリ定跡DB (`.ybb`)

ファイル名の例:

```text
user_book.ybb
```

index 領域は次の形式です。

```text
magic[16] = "YANE-BINBOOK-V1\0"
record_count uint64
flags uint64
records[record_count]:
  packed_sfen[32]
  moves_offset uint64
  ply uint16
  move_count uint16
```

moves 領域は `flags` によって 4 byte または 6 byte record の連続です。

```text
flags bit0 = 0:
move16 uint16
eval int16

flags bit0 = 1:
move16 uint16
eval int16
depth uint16
```

BookMinerCpp は `flags bit0 = 0` の depth なし `.ybb` を書きます。
depth 付き `.ybb` を読み込む場合も、BookMinerCpp 内部では depth を使わないので読み飛ばします。

`move16` は cshogi の内部 `move16` ではなく、やねうら王本体の `Move16` です。
cshogi で扱う場合は PSV形式の move16 がこれと同じbit配置なので、書き出し時は `cshogi.move16_to_psv()`、読み戻し時は `cshogi.move16_from_psv()` を使います。
cshogi の内部 `move16` をそのまま保存してはいけません。

index record は `packed_sfen[32]` の辞書順でsortします。
.ybb では index 領域の直後から moves 領域が始まり、index record の `moves_offset` は moves 領域先頭からの相対位置です。
そのため、on-the-fly probe では index 領域を二分探索し、moves 領域の `moves_offset` へ seek して指し手列だけを読みます。

従来の `.db` との相互変換は `../makebook/convert_db_to_ybb.py` と `../makebook/convert_ybb_to_db.py` で行います。
これらのスクリプトは `cshogi.Board.to_psfen()` / `set_psfen()` を使って PackedSfen を変換し、一時runを使って外部sortします。
全局面をオンメモリに載せないため、巨大な定跡でも少ないメモリで変換できます。

`startpos moves ...` の展開は、やねうら王の `Position` を使います。
USI指し手は `Position::to_move()` で `Move` に変換し、`pseudo_legal_s<true>() && legal()` を満たす場合だけ `do_move()` します。
合法手生成が必要な箇所では `MoveList<LEGAL>` を使います。

## ディレクトリ

```text
BookMinerCpp/
  settings/              実設定と sample 設定
  book/                  通常DB、peta DB、think_sfens.txt
  docs/                  C++版の設計メモ
  log/                   ログ
  source/                C++ source と Makefile
  BookMinerCpp.exe       source/Makefile の出力先
  README.md
```
