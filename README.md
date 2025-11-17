# これは何？

やねうら王関連のスクリプト集である。

やねうら王のメインのリポジトリは、こちら。
- https://github.com/yaneurao/YaneuraOu

# スクリプト一覧

| スクリプト | 種別 | 説明 |
| -- | -- | -- | 
| 📁&nbsp;[SPSA](https://github.com/yaneurao/YaneuraOu-ScriptCollection/tree/main/SPSA) | パラメーター自動調整 | パラメーター自動調整のためのツール群 |
| 📁&nbsp;[GenSfen](https://github.com/yaneurao/YaneuraOu-ScriptCollection/tree/main/GenSfen) | 教師生成 | 教師生成スクリプト |
| 📁&nbsp;PetaShock | 定跡 | 定跡の次に掘ると良い局面をリストアップするスクリプト(`peta_next`) | 
| 📁&nbsp;BookMiner | 定跡 | 定跡の採掘スクリプト |  
| 📁&nbsp;Bloodgate | 棋力計測 | 棋力計測用スクリプト |

## SPSA

探索部のパラメーター自動調整フレームワーク。

詳しくは、こちら。
- 📁 https://github.com/yaneurao/YaneuraOu-ScriptCollection/tree/main/SPSA

## peta_next.py

定跡ツリーをminimax化することをペタショック化と呼んでいる。これは、以下の説明にあるように、やねうら王本体のペタショックコマンドで実現できる。

- [makebook peta_shock - やねうら王Wiki 定跡の作成](https://github.com/yaneurao/YaneuraOu/wiki/%E5%AE%9A%E8%B7%A1%E3%81%AE%E4%BD%9C%E6%88%90#makebook-peta_shock)

このペタショック化がなされた、やねうら王の定跡ファイルに対して、次に定跡を掘っていくと良い局面をリストアップするスクリプトが peta_next.py である。

これは、2025年5月まで、やねうら王本体内蔵の定跡コマンドであったが、やねうら王本体に内包しておくとカスタマイズがしにくいため、Pythonで書き直すことにしたものである。

かきかけ


# ライセンス

ここのあるスクリプトは、すべてMIT Licenseとします。
