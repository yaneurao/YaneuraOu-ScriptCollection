# 5. BookMinerCpp 仕様

この章は BookMinerCpp 固有の仕様をまとめたものです。
BookMiner のアルゴリズム上の意味や操作手順は、Python 版 BookMiner のチュートリアルを参照してください。

## 実行モデル

BookMinerCpp は GUI ではありません。
stdin/stdout ベースの CLI として動作します。

GUI から使う場合は、Python 版の `BookMiner-gui.py --cpp` が次を起動します。

```text
../BookMinerCpp/BookMinerCpp.exe --from_gui
```

`--from_gui` が指定されている場合、プロンプト表示は抑制し、GUI が解釈するタグ付きログを出力します。

## コマンド互換

主要コマンドは Python 版と同じです。

```text
q                 保存して終了
!                 保存せず終了
w [ply_limit]     通常DBを手動保存
p                 通常DB保存、peta_shock、peta book 読み込み
r [path]          指定 peta book、または最新 peta book を読み込み
n eval_diff [max_step]
t [path]
e eval_limit
h
```

`t` の `path` 省略時は `book/think_sfens.txt` を読みます。

## 起動時の通常DB選択

起動時に `book/backup/` から通常DBを探します。
対象は次です。

```text
book_miner-YYYYMMDDHHMMSS_N.db
book_miner-YYYYMMDDHHMMSS_N.ybb
```

`.db` と `.ybb` は同じ候補集合として扱い、ファイル名文字列の辞書順で最後のものを読みます。
通常の命名規則では、これはタイムスタンプが最も新しいファイルです。

次は対象外です。

```text
book_miner-YYYYMMDDHHMMSS_N_ply100.db
book_miner-YYYYMMDDHHMMSS_N_ply100.ybb
*.tmp
```

`book/backup/book_miner.db` は、タイムスタンプ付き通常DBが存在しない場合だけ読む移行用 fallback です。

## 通常DB保存

通常保存は `.ybb` です。

```text
book/backup/book_miner-YYYYMMDDHHMMSS_N.ybb
```

`N` は保存対象局面数です。

保存時は一度 tmp file に書きます。

```text
book_miner-....ybb.tmp
```

書き終えてから正式名へ rename します。

## peta book

`p` または外部の `makebook peta_shock` によって作られる peta book は、現時点では従来のやねうら王標準定跡 `.db` です。

```text
book/backup/peta_book-YYYYMMDDHHMMSS_N.db
```

BookMinerCpp は `p` コマンドでの peta shock 化を自前実装せず、`YO-MATERIAL.exe` に次のようなコマンドを送ります。

```text
setoption name BookDir value book
setoption name BookFile value no_book
setoption name FlippedBook value true
setoption name USI_Hash value 1
makebook peta_shock backup/book_miner-....ybb backup/peta_book-....db.tmp
quit
```

成功後に `.tmp` を `.db` へ置き換えます。

`r` コマンドは peta shock 化を行わず、既に存在する `peta_book-....db` を読み込みます。path 省略時は `book/backup/` にある最新の peta book を選びます。

## 内部データモデル

BookMinerCpp の内部DBは `BookStore` が保持します。

局面 key:

```text
PackedSfen bytes[32]
```

局面情報:

```cpp
struct PositionInfo {
    uint16_t ply;
    std::vector<MoveInfo> moves;
};
```

指し手情報:

```cpp
struct MoveInfo {
    uint16_t move16;
    int16_t eval;
};
```

`PositionInfo` は SFEN文字列を保持しません。
SFEN文字列が必要なのは、従来の `.db` を書き出すときだけです。

## PackedSfen

`PackedSfen` は、やねうら王の `Position::sfen_pack()` と同じ32 byte表現です。

BookMinerCpp 本体は `YaneuraOu::PackedSfen` 型を直接公開せず、`std::array<uint8_t, 32>` として扱います。
変換は `sfen_position.cpp` の adapter を通します。

packed sfen の flip は、やねうら王側の `PackedSfen::flip()` / `PackedSfen::flipped()` を使います。
これは SFEN文字列や `Position::set()` を経由せず、packed sfen を raw board / hand / turn に展開して反転し、再 pack します。

## LSM-tree

`BookStore` は、1個の巨大な `unordered_map` だけで定跡DBを保持しません。
memtable と sorted run 群で保持します。

```text
BookStore
  memtable : unordered_map<PackedSfen, PositionInfo>
  runs     : vector<Run>
  searching: unordered_set<PackedSfen>
```

run は次の配列です。

```cpp
struct BookEntry {
    PackedSfen key;
    PositionInfo position;
};

using Run = std::vector<BookEntry>;
```

各 run は `PackedSfen.bytes` の辞書順に sort されています。

### 追加

新規局面は memtable に入ります。
既存局面が memtable または run にある場合は、その場で `moves` を merge します。

同一局面または flip 同一局面を複数 worker が同時に探索しないよう、`searching` set で lease 管理します。

### flush

memtable が一定件数に達すると、memtable を `PackedSfen` 順に sort して run にします。

現在の閾値:

```text
65536 entries
```

### compaction

同じサイズ帯の run が2本ある場合、古い run と新しい run を merge して1本にします。
merge 後の run も `PackedSfen` 順です。

BookMinerCpp の現在のアルゴリズムでは、同一局面が複数 run に重複して存在しない前提です。
新規局面追加前に既存 run を検索し、存在する場合はその場で更新します。

### `.ybb` 書き出し

`.ybb` 保存時は、run 群と一時的に sort した memtable run を n-way merge しながら書き出します。
これにより、保存のためだけに全局面を別の巨大配列へ詰め替えることを避けます。

### `.db` 書き出し

やねうら王標準定跡 `.db` は SFEN文字列順である必要があります。
そのため `.db` 書き出し時だけ、`PackedSfen` から SFEN文字列を復元して sort します。

通常バックアップは `.ybb` なので、この経路は主に変換やデバッグ用です。

## `.ybb` フォーマット

ファイル名の例:

```text
*.ybb
```

index 領域:

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

moves 領域:

```text
flags bit0 = 0:
move16 uint16
eval   int16

flags bit0 = 1:
move16 uint16
eval   int16
depth  uint16
```

`move16` は cshogi の内部 `move16` ではなく、やねうら王本体の `Move16` です。
cshogi で扱う場合は PSV形式の move16 がこれと同じbit配置なので、書き出し時は `cshogi.move16_to_psv()`、読み戻し時は `cshogi.move16_from_psv()` を使います。
cshogi の内部 `move16` をそのまま保存してはいけません。

BookMinerCpp が保存する `.ybb` は `flags bit0 = 0` です。
BookMinerCpp は作業DBでは `depth` を使わないため、depth 付き `.ybb` を読み込んだ場合も値は読み飛ばします。

数値は little endian です。
index record は `packed_sfen[32]` の辞書順で sort します。
.ybb では、index 領域の直後から moves 領域が始まります。
index record の `moves_offset` は moves 領域先頭からの相対位置です。

## やねうら王側の `.ybb` 対応

やねうら王本体にも `.ybb` reader を追加しています。

- 通常の `BookFile=user_book.ybb`
- `BookOnTheFly=true` での二分探索 probe
- `FlippedBook=true` 時の packed sfen 直接 flip probe
- `makebook peta_shock` の入力

`BookOnTheFly=true` の場合、index 領域を二分探索し、hit した局面の moves だけを moves 領域から読みます。

## 進捗タグ

GUI が拾う主なタグは Python 版と揃えます。

```text
[StartupStage]
[CommandReady]
[EngineInitStart]
[EngineInitProgress]
[EngineReadyProgress]
[EngineInitDone]
[BackupServiceStarted]
[BackupNext]
[BackupStart]
[BackupDone]
[BookReadStart]
[BookReadProgress]
[BookReadDone]
[BookWriteStart]
[BookWriteProgress]
[BookWriteDone]
[TaskQueueStart]
[TaskQueueProgress]
[TaskQueueDone]
[MiningProgress]
[PetaCommandDone]
[PetaReadDone]
[PetaNextDone]
```

タグの意味は Python 版 GUI チュートリアルを参照してください。
