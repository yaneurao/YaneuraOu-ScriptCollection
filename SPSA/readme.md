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

## ブロックについて

- 1つのファイルは複数のブロックで構成される。
- ブロックは、1行目の行頭が`#`で始まる。この1行目のことをブロックヘッダーと呼ぶ。
- ブロックヘッダーで、`//`で書いた部分以降は無視される。
- 1つのブロックは、次のブロックヘッダーまでである。つまり、1つのブロックは、複数行でも良い。
- ブロックは、`#context`のように、ブロック名を書くことができる。このブロック名として使えるのは、`#set`,`#context`,`#add`である。

## マーカーについて

対象とするソースコードのどこに追加するのかは、マーカーで指定する。

マーカーとは、例えば、`[[ TUNE ISREADY ]]`のような任意の文字列である。この文字列は、C++のソースコード上で、コメントとして書く。

例 : `// [[ TUNE ISREADY ]]`

C++のソースコードで、その行の`//`以降はコメント領域なので自由に文字列を書くことができるが、これを利用してマーカーを設置する。

マーカーのある行をマーカー行と呼ぶ。

このスクリプトでは、マーカーの直下(次の行以降)に追加されていく。

## #setブロックについて

- `#set`のあとには、オプション名と設定内容をスペース区切りで書くことができる。例 : `#set file movepick.cpp`。このとき、`file`は`movepick.cpp`になる。
- `#set`のあとに書けるオプション名は、
  - `file` : 対象とするソースファイルのPathを書ける。
  - `options` : 対象とするソースファイルに`TUNE`マクロを追加するマーカーを指定する。このタイミングでしこうエンジンオプションに追加される。(add_optionsのタイミングが良いと思う。)
  - `declaration` : 対象とするソースファイルに`int my_value;`のようなオプション変数の変数宣言を追加するマーカーを指定する。(ソースファイルの冒頭付近が良いと思う。)

## `TUNE`マクロについて

`TUNE`マクロとはStockfishのTUNEマクロ。tune.hで定義されている。詳しい説明は、そこを見ること。

- 元ソースコードに思考エンジンオプションとしてそのパラメーターの値を変更できる命令を追加しないといけないが、これはStockfishの`TUNE`マクロを用いて、次のように追加する。`TUNE(SetRange(-100, 100), param_1 , SetDefaultRange);`  やねうら王では、`add_options()`のなかなどに追加されるように設定すると良いと思う。


## #contextブロックについて

- `#context`は、そのあとにブロック名を書くことができる。例 : `#context conthist_bonuses`の`conthist_bonuses`のように。
- `#context`ブロックは、ソースコードのどの部分を対象にするかを書くことができる。ソースコードからコピペしてくると良い。
- `#context`ブロックのなかで、数字の末尾に`@`をつけると、それが調整すべきパラメーターとなる。例 : `123@` このとき、そのブロック名 + `_` + 1からの連番 がそれに対応する変数名となり、思考エンジンオプションに自動的に追加される。例えば、`conthist_bonuses_1`のように。
- `#context`ブロックのなかで、数字の末尾に`@`をつけ、そのあとさらに英数字を書くこともできる。例 : `123@a12` このとき、そのブロック名 + `_` + `@`の右横の英数字 がそれに対応する変数名となり、思考エンジンオプションに自動的に追加される。例えば、`conthist_bonuses_a12`のように。

## #addブロックについて

- `#add`も、そのあとにブロック名を書くことができる。
- `#add`のあとブロック名を省略した場合、「無名addブロック」と呼ばれるものになる。無名addブロックは、直前の`#context`にマッチしたソースコードの部分を置換する時の置換対象となる。
- `#add`ブロックのブロック名は、そのブロックをソースコード中に追加するときの、マーカーの名前となる。例えば、`#add %%TUNE_GLOBAL%%`と書いてあれば、`// %%TUNE_GLOBAL%%`のように`%%TUNE_GLOBAL%%`が含まれる行(通例、`//`によるコメント行)の直下に追加される。このように、追加する位置を示すマーカーとして用いる。

## TuneBlockについて

TuneBlockとは、1つのpatchのこと。これには、対象ファイルPathと1つの`#context`ブロック、任意の個数の`#add`ブロックが必要である。

つまり、`#context`が2回目に出てくるとそこまでが1つのTuneBlockということになる。(1つのTuneBlockには1つの`#context`ブロックしか内包しないため。)

また、`#set`した値は、TuneBlockをまたいで保持されるので、再度`#set`しない限り値は変わらない。このため、処理対象のファイル名などは一度設定すれば、変更する時まで再度`#set`する必要はない。


-  `suisho10.tune` の例

```
#set file engine\yaneuraou-engine\yaneuraou-search.cpp
  💡 置換するソースファイル名

#set declaration %%TUNE_DECLARE%%
#set options %%TUNE_OPTIONS%%

  💡 オプション変数を追加するソースコード中のマーカー

#context conthist_bonuses // update_continuation_histories()

💡 #contextの続きにある`conthist_bonuses`が変数名のprefix。`update_continuation_histories()`という文字列は、元のソースコードの関数名などを備忘のために書いたもの。

📝 以下が置換対象 tune.pyの`apply`コマンドでは、この`@`の左側の数値が、`@`の右側の変数の値で置き換わる。

static constexpr std::array<ConthistBonus, 6> conthist_bonuses = 
    {{1, 1092@1}, {2, 631@2}, {3, 294@3}, {4, 517@4}, {5,126@5}, {6, 445@6}};

#add

💡 tune.pyの`tune`コマンド実行時には上のcontextが行はここにある文字列で置換される。(ここでは関数のなかから配列の定義を消すためにコメントアウトしたものと置換している)
📝 addのあとに文字列を書かなければ、無名addブロックであり、`#context`ブロックとマッチした部分がこのブロックの内容に置換される。

// static constexpr std::array<ConthistBonus, 6> conthist_bonuses = { ... };

#add %%TUNE_DECLARATION%%

💡 tune.pyの`tune`コマンドでは、以下の内容を#fileで指定されたファイルのaddの直後の文字列(`%%TUNE_GLOBAL%%`)に合致する行の下に追加する。

    std::array<ConthistBonus, 6> conthist_bonuses = {
        {{1, 0 }, {2, 0 }, {3, 0 }, {4, 0 }, {5, 0 }, {6, 0 }}};

#add %%TUNE_ISREADY%%

    {
        int t[6] = {@,@,@,@,@,@};
        for (size_t i = 0; i < conthist_bonuses.size(); ++i)
            conthist_bonuses[i].weight = t[i];
    }

💡 以下、繰り返し。このあと#fileを省略して、#contextから書いていく場合、同じソースファイルが対象となる。
```

### 解説

元の配列は、(関数内で定義されており)static constexprなので、このconstを外し、かつ、globalな変数(配列)にしてやる必要がある。

そのため、上の例では、ソースコード(`yaneuraou-search.cpp`)の冒頭らへんのglobal変数を配置するところに`%%TUNE_GLOBAL%%`と、書いてある。

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

vの初期値は、tune.pyの`tune`コマンド実行時に置換対象の行から取得したもの。

min,maxは、v > 0なら [0, 2v] , v < 0なら [2v , 0]がその範囲となる。このファイルが生成されたあと、自分でこのファイルを直接編集しても良い。

stepは、勾配を求めるときに、パラメーターを動かす量(の最終値)。StockfishのSPSAのC_End。強さが変わる程度の値を指定する必要がある。(max-min)/20がデフォルト。

deltaは、一度に移動させる量(の最終値)。StockfishのSPSAのR_End。

💡 `最終値`と書いているのは、最初は少し大きめの値からスタートするため。

OpenmBenchの入力ファイルっぽくしてある。

https://github.com/AndyGrant/OpenBench/wiki/SPSA-Tuning-Workloads
