# これは何？

SPSAするためのツール類。

- SPSAとはパラメーター自動調整の手法。
- Stockfishのfishtestでも使われている。
- ここでは、やねうらおが自作したSPSAフレームワークを用いてチューニングを行う。

- 参考 OpenBenchのSPSA : https://github.com/AndyGrant/OpenBench/wiki/SPSA-Tuning-Workloads

# 使い方

チューニング指示ファイルを用いる。
チューニング指示ファイルの書き方は後述。

いま仮にチューニング指示ファイル`suisho10.tune`があるとする。

💡 ここにはSPSAで自動調整したパラメーターも書き込まれている。

やねうら王のソースコードは、YaneuraOu/source に格納されているものとする。

## `suisho10.tune`をやねうら王のソースコードに適用する例

これをやねうら王のソースコードに適用する例。(`apply`コマンド)

> python tune.py apply suisho10.tune YaneuraOu/source

⚠ 本番ソースコードが書き換わるのでソースコードを別のフォルダなどにコピーして、そのコピーしたフォルダを対象にするようにしてください。

## `suisho10.tune`の内容に従い、やねうら王をSPSA用にpatchを当てる例

やねうら王のソースコードにSPSAするためのpatchを当てる方法。(`tune`コマンド)

> python tune.py tune suisho10.tune YaneuraOu/source

こうすると、suisho10.tuneの内容に従って、patchが当てられたやねうら王ができあがる。

これをビルドすると、suisho10.tuneで指定されたオプション項目が追加されている。

これを用いて、SPSAフレームワークで基準ソフトと連続自己対局させて調整する。

調整されたパラメーターが`suisho10.tune`に書き戻されるので、上の`apply`コマンドで、やねうら王のソースコードにSPSAで自動調整されたパラメーターを適用してビルドすれば完成。

# `.tune`ファイルのフォーマット

チューニング指示ファイル(拡張子`.tune`)のフォーマットについて。

-  `suisho10.tune` の例

```
#file engine\yaneuraou-engine\yaneuraou-search.cpp
  💡 置換するソースファイル名

#context conthist_bonuses // update_continuation_histories()

💡 #contextの続きにある`conthist_bonuses`が変数名のprefix。`update_continuation_histories()`という文字列は、元のソースコードの関数名などを備忘のために書いたもの。

📝 以下が置換対象 tune.pyの`apply`コマンドでは、この`@`の左側の数値が、`@`の右側の変数の値で置き換わる。

static constexpr std::array<ConthistBonus, 6> conthist_bonuses = 
    {{1, 1092@1}, {2, 631@2}, {3, 294@3}, {4, 517@4}, {5,126@5}, {6, 445@6}};

#replace

💡 tune.pyの`tune`コマンド実行時には上のcontextが行はここにある文字列で置換される。(ここでは関数のなかから配列の定義を消すためにコメントアウトしたものと置換している)

// static constexpr std::array<ConthistBonus, 6> conthist_bonuses = { ... };

#add

💡 tune.pyの`tune`コマンドでは、以下の内容を#fileで指定されたファイルの%%TUNE_HEADER%%と書いてあるところの下に追加する。

std::array<ConthistBonus, 6> conthist_bonuses = {
    {{1, @1 }, {2, @2 }, {3, @3 }, {4, @4 }, {5, @5 }, {6, @6 }}};
// ここ書いたコメントなどは、そのままソースコードの追加される。

💡 以下、繰り返し。このあと#fileを省略して、#contextから書いていく場合、同じファイルが対象となる。
```

### 解説

元の配列は、(関数内で定義されており)static constexprなので、このconstを外し、かつ、globalな変数(配列)にしてやる必要がある。

そのため、上の例では、ソースコード(`yaneuraou-search.cpp`)の冒頭らへんのglobal変数を配置するところに`%%TUNE_HEADER%%`と、書いてある。

上のフォーマットのうち、`#replace`ブロックと、`#add`ブロックは省略できる。

上の例では、実際の思考エンジンオプションに追加される変数名は、`conthist_bonuses_1`～``conthist_bonuses_6`となる。つまり、`#context`の直後に書いた文字 と `@`を`_`に置換してパラメーターを連結したものが思考エンジンオプション名になる。

また、
```
static constexpr std::array<ConthistBonus, 6> conthist_bonuses = 
    {{1, 1092@1}, {2, 631@2}, {3, 294@3}, {4, 517@4}, {5,126@5}, {6, 445@6}};
```
を以下のように`@`のあとの数字を省略した場合、自動的に1からの連番とみなされる。
```
static constexpr std::array<ConthistBonus, 6> conthist_bonuses = 
    {{1, 1092@}, {2, 631@}, {3, 294@}, {4, 517@}, {5,126@}, {6, 445@}};
```


## `.params`ファイルのフォーマットについて

実際のパラメーターは、今回の場合、`suisho10.params`というファイルに書き出される。(`suisho10.tune`に対して拡張し`.tune`を取り除き、`.params`を付与したもの。)

この`.params`ファイルは以下のフォーマットになっている。自分で編集することもできる。

```
conthist_bonuses_1,int,1092,0,2000,10,0.002
conthist_bonuses_2,int,631,0,2000,10,0.002
```

パラメーター名(思考エンジンオプション名) , v(現在の値) ,min,max,step,delta。

vの初期値は、`tune`コマンドで置換対象の行から取得したもの。

min,maxは、v > 0なら [0, 2v] , v < 0なら [2v , 0]がその範囲となる。このファイルが生成されたあと、自分でこのファイルを直接編集しても良い。

stepは、勾配を求めるときに、パラメーターを動かす量(の最終値)。StockfishのSPSAのC_End。強さが変わる程度の値を指定する必要がある。(max-min)/20がデフォルト。

deltaは、一度に移動させる量(の最終値)。StockfishのSPSAのR_End。

💡 `最終値`と書いているのは、最初は少し大きめの値からスタートするため。

OpenmBenchの入力ファイルっぽくしてある。

https://github.com/AndyGrant/OpenBench/wiki/SPSA-Tuning-Workloads



