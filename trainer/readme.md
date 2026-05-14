# trainer.py

`trainer.py` は、`C:\shogi\DeepLearningShogi` の `dlshogi.train` または `dlshogi.ptl` を、教師ファイル1個ずつ呼び出して学習を進めるためのラッパーです。

既定では `C:\shogi\teacher\yane-distill` にある `*.hcpe` / `*.hcpe3` をすべて使い、`exp___i20x256` を bfloat16 で学習します。

## 既定値

```txt
dlshogi_dir  = C:\shogi\DeepLearningShogi
train_dir    = C:\shogi\teacher\yane-distill
model_root   = C:\shogi\model
network      = exp___i20x256
model folder = C:\shogi\model\exp___i20x256
batchsize    = 1024
lr           = 0.03
eta_min      = 1e-5
amp_dtype    = bfloat16
val_lambda   = 1.0
start_index  = 1
use_swa      = True
use_compile  = False
```

`--network` の文字列はフォルダ名にそのまま使います。`exp___i20x256` を `exp_i20x256` に直すような置換はしません。

## 基本の使い方

1周目を最初から実行します。

```powershell
cd C:\shogi\learner
python .\trainer.py
```

既定の backend は従来の `dlshogi.train` です。checkpoint は `checkpoint-0001.pth` のように出ます。

PyTorch Lightning 版で学習する場合は `--backend ptl` を付けます。

```powershell
python .\trainer.py --backend ptl
```

PTL 版の checkpoint は `checkpoint-0001.ckpt` のように出ます。PTL 版では accuracy / loss / lr が、各 checkpoint 番号ごとに以下のようなCSVへ書き出されます。

```txt
C:\shogi\model\exp___i20x256_round2\metrics\train-0022\metrics.csv
```

## PTL とは

PTL は PyTorch Lightning のことです。PyTorch そのものとは別のライブラリで、モデルの計算部分は PyTorch のまま使い、学習ループ、checkpoint保存、ログ出力、AMP、GPU設定、分散学習などの周辺処理を整理して扱うための枠組みです。

通常の PyTorch では、以下のような処理を学習スクリプト側で細かく書きます。

- データを読む
- forward / loss計算をする
- backward する
- optimizer を進める
- scheduler を進める
- validation する
- checkpoint を保存する
- 中断から再開する
- loss や accuracy をログに出す

PyTorch Lightning では、この「学習の進行管理」を Lightning 側に任せます。dlshogi の `ptl.py` は、この Lightning 形式で書かれた学習スクリプトです。

### PTL のメリット

このラッパーで PTL を使う主なメリットは以下です。

- `metrics.csv` に train / validation の loss や accuracy を残しやすい
- checkpoint が Lightning 形式になり、学習状態をまとめて保存しやすい
- AMP や bfloat16 の指定が整理される
- `torch.compile` との組み合わせを設定ファイル経由で扱いやすい
- 将来的に複数GPUや分散学習へ広げやすい
- 学習ループの細かい処理を Lightning 側に寄せられるので、設定変更に強い

一方で、PTL 版の checkpoint は `.ckpt` になり、従来の `train.py` 版の `.pth` とは形式が違います。このラッパーでは、train.py 版で1周完了した `.pth` をモデル初期値として読み、2周目から PTL 版へ移れるようにしています。

### train.py 版と PTL 版の使い分け

従来の `train.py` backend は、dlshogi の古くからある学習方式です。ログ形式や checkpoint 形式がこれまでの運用と近く、単純に動かすにはわかりやすいです。

PTL backend は、ログや checkpoint 管理をきれいにしたい場合、`torch.compile` を試したい場合、今後の拡張性を重視したい場合に向いています。

このラッパーでは、既定は従来の `train.py` backend のままです。PTL を使いたいときだけ `--backend ptl` を付けます。

## 途中中断からの再開

基本的には同じコマンドをもう一度実行するだけです。

```powershell
python .\trainer.py
```

最新の周が途中なら、その周の次の未完了教師ファイルから再開します。すでに checkpoint がある教師ファイルは `already done` として飛ばします。

最新の周が完了していれば、自動で次の round フォルダを作り、lr を `0.03` から再スタートします。例えば1周目が `C:\shogi\model\exp___i20x256` で完了していれば、2周目は自動で以下になります。

```txt
C:\shogi\model\exp___i20x256_round2
```

## まとめて複数 round 回す

`--rounds N` を指定すると、`trainer.py` を `N` 回連続で呼ぶのと同じ動きをします。1回の起動で round を `N` 周進めるので、毎回手で再実行する必要がありません。

例えば round3 まで完了している状態から、追加で3周回したい場合:

```powershell
python .\trainer.py --rounds 3
```

これで round4 → round5 → round6 の3周分が順に走ります。途中の round が中断していた場合は、最初の round でその続きから再開し、残りを新しい round フォルダで進めます。

`--rounds` を省略した場合の挙動は従来と同じ（1周だけ実行）です。

注意点:

- `--out_dir` と `--resume_checkpoint` は **最初の round にだけ** 適用されます。2周目以降は `--model_root` と `--network` から自動検出した出力先 (`..._round{N+1}`) へ進みます
- 途中で失敗した場合は、同じコマンドをもう一度実行すれば未完了の round / 教師ファイルから再開します

## train.py 版の1周目から PTL 版の2周目へ移る

`C:\shogi\model\exp___i20x256` に train.py 版の1周目 checkpoint があり、全教師ファイル分が完了している状態で以下を実行します。

```powershell
python .\trainer.py --backend ptl
```

この場合、最新の `.pth` checkpoint をモデル初期値として使い、optimizer / scheduler は初期化して、`C:\shogi\model\exp___i20x256_round2` から始めます。

## 手動で checkpoint を指定する

特定 checkpoint から新しい周を始めたい場合は `--resume_checkpoint` を使います。

```powershell
python .\trainer.py ^
  --resume_checkpoint C:\shogi\model\exp___i20x256\checkpoint-0021.pth ^
  --reset_optimizer ^
  --reset_scheduler
```

既存の出力先を手動指定したい場合だけ `--out_dir` を使います。

```powershell
python .\trainer.py ^
  --out_dir C:\shogi\model\exp___i20x256_test ^
  --resume_checkpoint C:\shogi\model\exp___i20x256\checkpoint-0021.pth ^
  --reset_optimizer ^
  --reset_scheduler
```

## --start_index

`--start_index` は、その周の教師ファイルを何番目から実行するかを指定します。番号は、ソート済み教師ファイル一覧の1始まりです。

例えば `--start_index 5` なら、1から4番目の教師ファイルはこの実行では飛ばし、5番目の教師ファイルから始めます。

```powershell
python .\trainer.py --start_index 5
```

checkpoint や log の番号は詰めません。`--start_index 5` なら `train-0005.log` と `checkpoint-0005.pth` / `checkpoint-0005.ckpt` から使います。

通常の途中中断からの再開では `--start_index` は不要です。

## ログをCSVで確認する

ログ抽出も `trainer.py` だけでできます。

```powershell
python .\trainer.py --network exp___i20x256 --show_log
```

`--show_log` は学習を開始せず、`--network` から決まるモデルフォルダの `train-*.log` を読みます。同じ親フォルダに `_round2`, `_round3` などがあれば、それらもまとめて読みます。

CSVは標準出力に出し、同じ内容をカレントディレクトリの `<network>.csv` に保存します。最新ログの末尾3行も標準出力の最後にコメントとして表示します。

出力先フォルダを直接指定する場合:

```powershell
python .\trainer.py --out_dir C:\shogi\model\exp___i20x256_round2 --show_log
```

## SWA

SWA は既定で ON です。最後に書き出される `model-*` は SWA 済みになります。checkpoint にも SWA/EMA 側の状態が保存されます。

SWA を使わない場合だけ `--no_swa` を付けます。

```powershell
python .\trainer.py --no_swa
```

注意: 従来の `train.py` backend では教師1ファイルごとに別プロセスで `dlshogi.train` を呼びます。これは dlshogi 側の内部キャッシュや logging 設定が前回呼び出しから残らないようにするためです。`train.py` が最後に行う SWA の BatchNorm 更新は、最後の教師ファイルだけを使います。SWA の平均重み自体は学習全体で更新されますが、BN 更新を全教師で厳密に行いたい場合は追加の最終エクスポート処理が必要です。

## torch.compile

`torch.compile` は、PyTorch 2.x に入っている高速化機能です。通常の PyTorch は、モデルの各演算を順番に GPU へ投げます。`torch.compile` を使うと、PyTorch がモデルの計算グラフを解析し、複数の演算をまとめたり、GPU向けの効率のよいコードに変換したりします。

dlshogi の `exp___i20x256` のような ResNet + Transformer 系モデルでは、畳み込み、行列積、活性化関数、attention などの演算が大量に出ます。`torch.compile` がうまく効くと、GPUのカーネル起動回数やメモリアクセスが減り、学習 step あたりの時間が短くなる可能性があります。

ただし、最初の step はコンパイル処理が入るため遅くなります。速度を見るときは、開始直後ではなく、コンパイル後に数百 step 以上進んでから比較します。

`torch.compile` を使う場合は `--use_compile` を付けます。

```powershell
python .\trainer.py --backend ptl --use_compile
```

### backend とは

`torch.compile` は「モデルをコンパイルする入口」で、実際にどの方式で変換するかは backend が担当します。

代表的には以下があります。

- `aot_eager`: 互換性重視。Windowsでも動きやすいが、高速化は控えめになりやすい。
- `inductor`: PyTorch 標準の本命 backend。GPU向けに最適化されたコードを生成しやすく、大きな高速化が期待できる。

dlshogi は Windows で backend 未指定の場合、自動的に `aot_eager` を使います。これは安全寄りの設定です。一方で、速度を狙うなら `inductor` を試す価値があります。

### inductor と triton-windows

`inductor` は GPU向けの高速なカーネルを生成するときに Triton を使います。Linux版の PyTorch では Triton が自然に使えることが多いですが、Windowsでは追加で `triton-windows` が必要です。

つまり Windows で高速化を狙う場合の関係はこうです。

```txt
trainer.py
  -> torch.compile
      -> backend=inductor
          -> triton-windows
              -> NVIDIA GPU 向けの高速な処理
```

`triton-windows` を入れて `inductor` を使いたい場合は、backend を明示的に指定します。

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor
```

Windows では、Inductor の既定キャッシュ `%TEMP%\torchinductor_<ユーザー名>` で `FileExistsError` が出ることがあります。このラッパーは `--compile_backend inductor` のときだけ、各学習プロセスに専用の cache dir を渡して衝突を避けます。

```txt
C:\shogi\model\<network>\_ti\<短いID>\i
C:\shogi\model\<network>\_ti\<短いID>\t
```

`i` は Inductor 用、`t` は Triton 用です。モデルフォルダ配下に置きます。学習プロセスごとに分けるので、同じモデルフォルダ内でもキャッシュ競合しにくくします。このフォルダはコンパイルキャッシュなので、学習が止まっているときなら削除しても構いません。

さらに短い場所へ置きたい場合は、実行前に `DLSHOGI_INDUCTOR_CACHE_ROOT` を指定できます。

```powershell
$env:DLSHOGI_INDUCTOR_CACHE_ROOT = "D:\tmp\dlshogi-inductor-cache"
python .\trainer.py --network exp_i_a20x256 --use_compile --compile_backend inductor --compile_mode reduce-overhead
```

また、PyTorch 2.5 + Windows では Inductor の `shape_padding` 最適化でも同じ種類の `FileExistsError` が出ることがあります。このラッパーは `--compile_backend inductor` のときだけ、子プロセスに以下を渡して `shape_padding` を無効化します。

```txt
TORCHINDUCTOR_SHAPE_PADDING=0
TORCHINDUCTOR_COMPILE_THREADS=1
DLSHOGI_PATCH_TRITON_CACHE=1
```

`TORCHINDUCTOR_SHAPE_PADDING=0` は、行列積を Tensor Core に合わせてパディングする最適化を切る設定です。速度面では少し損をする可能性がありますが、Windowsでまず安定して `inductor` を通すための回避策です。

`TORCHINDUCTOR_COMPILE_THREADS=1` は、Inductor のコンパイルを並列化しない設定です。Windows の Triton キャッシュ作成で競合しにくくするために指定しています。

`DLSHOGI_PATCH_TRITON_CACHE=1` は、このラッパーの子プロセス起動時に Triton cache の書き込み処理へ小さな互換性パッチを入れるための設定です。Triton の `FileNotFoundError` 対策です。

### Triton の生成DLLがブロックされる場合

Windows の設定によっては、`inductor` / `triton-windows` が実行時に生成する `__triton_launcher` というDLLがブロックされることがあります。

代表的なエラー:

```txt
ImportError: DLL load failed while importing __triton_launcher:
アプリケーション制御ポリシーによってこのファイルがブロックされました。
```

これは dlshogi の checkpoint や教師データの問題ではありません。Triton が GPU kernel を呼び出すために一時的な拡張モジュールを生成し、それを Python が読み込もうとしたところで、Windows Defender Application Control、Smart App Control、AppLocker、会社/組織のセキュリティポリシーなどに止められています。

SmartScreen の「アプリとファイルの確認」で止められている場合は、Windows の設定から無効化できます。

```txt
「設定」（Windowsキー + I）を開きます。
「プライバシーとセキュリティ」 > 「Windows セキュリティ」を選択します。
「アプリとブラウザーのコントロール」をクリックします。
「評価ベースの保護設定」をクリックします。
「アプリとファイルの確認」を「オフ」にします。
```

この設定をオフにすると、未評価のアプリやファイルに対する Windows の確認が弱くなります。学習後に戻すかどうかは運用方針に合わせて判断してください。

SmartScreen 以外のポリシーで止められている場合、スクリプト側だけで完全に回避するのは難しいです。対処は以下のどれかです。

- `--compile_backend inductor` をやめる
- `--use_compile` だけにして、Windows既定の `aot_eager` を使う
- セキュリティポリシーで Triton cache 配下の生成DLLを許可する
- WSL2 / Linux で学習する

安全側で動かすコマンド:

```powershell
python .\trainer.py --network exp_i_a20x256 --use_compile
```

または compile なし:

```powershell
python .\trainer.py --network exp_i_a20x256
```

### fullgraph

`--compile_fullgraph` は、モデル全体を1つのグラフとしてコンパイルする指定です。うまく通ると速くなる可能性がありますが、途中に未対応の処理があるとエラーになりやすくなります。

まずは `--compile_fullgraph` なしで動作確認し、問題なければ次に試すのが安全です。

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor --compile_fullgraph
```

### compile_mode

`--compile_mode` は、コンパイル時の最適化方針です。最初は無指定、または `reduce-overhead` から試すのが無難です。

`max-autotune` 系は、コンパイル時間が長くなったり、環境によっては失敗したりすることがあるため、最初の本番投入では避けるのが安全です。

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor --compile_mode reduce-overhead
```

### おすすめの試し方

まず通常の PTL 学習が動くことを確認します。

```powershell
python .\trainer.py --backend ptl
```

次に `torch.compile` を有効にします。

```powershell
python .\trainer.py --backend ptl --use_compile
```

`triton-windows` を入れているなら、`inductor` を試します。

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor
```

最後に、余力があれば `fullgraph` を試します。

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor --compile_fullgraph
```

速度比較は `train-*.log` の step/sec や、PTL の進捗表示を見て判断します。最初の数 step はコンパイル時間を含むので、比較対象から外します。

## Windows 環境構築

以下は `C:\shogi\DeepLearningShogi` を git clone し、`C:\shogi\learner` に `trainer.py` を置いて使う構成例です。

### 1. 必要なもの

- Windows 11 64bit
- NVIDIA GPU と新しめの NVIDIA Driver
- Visual Studio 2022 Build Tools
- Python 3.11 64bit
- Git

Visual Studio Build Tools では、少なくとも以下を入れます。

- Desktop development with C++
- MSVC v143
- Windows 10/11 SDK

入れたあと、新しい PowerShell を開いて Python を確認します。

```powershell
python --version
python -c "import platform; print(platform.architecture())"
```

複数の Python が入っている場合は、どれが使われているか確認します。

```powershell
Get-Command -All python
where.exe python
```

`cl` が見つからない場合は、スタートメニューの `Developer PowerShell for VS 2022` から実行するか、通常の PowerShell で Visual Studio の開発環境を読み込みます。パスはインストール先に合わせてください。

```powershell
&"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\Launch-VsDevShell.ps1" -Arch amd64
cl
```

### 2. DeepLearningShogi を取得する

```powershell
New-Item -ItemType Directory -Force C:\shogi
cd C:\shogi
git clone https://github.com/TadaoYamaoka/DeepLearningShogi.git
cd C:\shogi\DeepLearningShogi
git pull --ff-only
```

すでに clone 済みなら `git pull --ff-only` だけでよいです。

`dubious ownership` などのエラーで git が止まる場合は、Git を実行するユーザーで以下を一度だけ実行します。

```powershell
git config --global --add safe.directory C:/shogi/DeepLearningShogi
```

### 3. Python パッケージを入れる

まず pip 周りを更新します。

```powershell
python -m pip install -U pip setuptools wheel
python -m pip install -U cython numpy pyyaml
```

CUDA 12.4 版 PyTorch の例です。使用する CUDA と PyTorch の組み合わせに合わせて、公式サイトで表示されるインストールコマンドを選んでください。

```powershell
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

PyTorch Lightning 版を使う場合:

```powershell
python -m pip install "lightning==2.2.0.post0" "jsonargparse[signatures]"
```

確認します。

```powershell
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
python -c "import lightning; print(lightning.__version__)"
```

### 4. 入玉特徴量つきで cppshogi をビルドする

`NYUGYOKU_FEATURES=1` を付けてビルドします。

```powershell
cd C:\shogi\DeepLearningShogi
$env:NYUGYOKU_FEATURES = "1"
python setup.py build_ext --inplace
python -m pip install -e .
```

確認します。`MAX_FEATURES2_NYUGYOKU_NUM` が 0 より大きければ、入玉特徴量つきで import できています。

```powershell
python -c "from dlshogi.common import MAX_FEATURES2_NYUGYOKU_NUM; print(MAX_FEATURES2_NYUGYOKU_NUM)"
python -c "import dlshogi.ptl; print('ptl ok')"
```

うまく反映されないときは、古いビルド生成物を消してから再ビルドします。

```powershell
cd C:\shogi\DeepLearningShogi
Remove-Item -Recurse -Force .\build -ErrorAction SilentlyContinue
$env:NYUGYOKU_FEATURES = "1"
python setup.py build_ext --inplace
python -m pip install -e .
```

### 5. triton-windows を入れて inductor を使う

`torch.compile --compile_backend inductor` を Windows で使いたい場合は `triton-windows` を入れます。

PyTorch 2.5 系では、対応する Triton は 3.1 系です。

```powershell
python -m pip uninstall -y triton
python -m pip install -U "triton-windows>=3.1,<3.2"
```

PyTorch と Triton の目安:

```txt
PyTorch 2.4 -> Triton 3.1
PyTorch 2.5 -> Triton 3.1
PyTorch 2.6 -> Triton 3.2
PyTorch 2.7 -> Triton 3.3
PyTorch 2.8 -> Triton 3.4
PyTorch 2.9 -> Triton 3.5
```

確認します。

```powershell
python -c "import torch, triton; print(torch.__version__, triton.__version__)"
python -c "import torch; f=torch.compile(lambda x: x.sin() + x.cos(), backend='inductor'); x=torch.randn(1024, device='cuda'); print(f(x)[:3])"
```

失敗する場合は、以下を確認します。

- PyTorch と triton-windows のバージョン対応が合っているか
- NVIDIA Driver が古くないか
- Visual C++ Redistributable が古くないか
- `C:\Users\<user>\.triton\cache` や `%TEMP%\torchinductor_<user>` に古いキャッシュが残っていないか

### 6. 学習を実行する

従来の train.py backend:

```powershell
cd C:\shogi\learner
python .\trainer.py
```

PTL backend:

```powershell
python .\trainer.py --backend ptl
```

PTL + torch.compile + inductor:

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor
```

PTL + torch.compile + inductor + fullgraph:

```powershell
python .\trainer.py --backend ptl --use_compile --compile_backend inductor --compile_fullgraph
```

### 7. ログと出力

従来 backend:

```txt
C:\shogi\model\exp___i20x256\train-0001.log
C:\shogi\model\exp___i20x256\checkpoint-0001.pth
```

PTL backend:

```txt
C:\shogi\model\exp___i20x256_round2\train-0001.log
C:\shogi\model\exp___i20x256_round2\checkpoint-0022.ckpt
C:\shogi\model\exp___i20x256_round2\ptl-config-0022.yaml
C:\shogi\model\exp___i20x256_round2\metrics\train-0022\metrics.csv
```

`train-XXXX.log` は標準出力を保存したものです。PTL の accuracy / loss / lr は `metrics.csv` を見ます。

## 参考リンク

- DeepLearningShogi: https://github.com/TadaoYamaoka/DeepLearningShogi
- PyTorch: https://pytorch.org/
- PyTorch Lightning: https://lightning.ai/docs/pytorch/stable/
- triton-windows: https://github.com/triton-lang/triton-windows
- torch.compile: https://docs.pytorch.org/docs/stable/generated/torch.compile.html

## Ubuntu / WSL2 での実行例

Ubuntu や WSL2 では、Windows 固有の `C:\...` パスは使えません。`--dlshogi_dir`, `--train_dir`, `--test_data`, `--model_root` を Linux 側のパスで指定します。

Ubuntu のホームディレクトリ配下にデータを置いている場合:

```bash
python trainer.py \
  --network exp_i_a20x256 \
  --dlshogi_dir /home/USER/shogi/DeepLearningShogi \
  --train_dir /home/USER/shogi/teacher/yane-distill \
  --test_data /home/USER/shogi/teacher/test/test20231010_fg2021_dls5_ryfc20_ev8250k825.hcpe \
  --model_root /home/USER/shogi/model \
  --use_compile \
  --compile_backend inductor \
  --compile_mode reduce-overhead
```

WSL2 から Windows 側の `C:\shogi` を参照する場合:

```bash
python trainer.py \
  --network exp_i_a20x256 \
  --dlshogi_dir /mnt/c/shogi/DeepLearningShogi \
  --train_dir /mnt/c/shogi/teacher/yane-distill \
  --test_data /mnt/c/shogi/teacher/test/test20231010_fg2021_dls5_ryfc20_ev8250k825.hcpe \
  --model_root /mnt/c/shogi/model \
  --use_compile \
  --compile_backend inductor \
  --compile_mode reduce-overhead
```

Linux では `inductor` が本来の想定環境なので、Windows 用の `triton-windows` は不要です。CUDA 版 PyTorch と Triton が正しく入っていれば、`torch.compile --compile_backend inductor` は Windows より素直に動く可能性が高いです。
