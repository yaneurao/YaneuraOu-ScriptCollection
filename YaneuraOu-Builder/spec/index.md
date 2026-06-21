# YaneuraOu-Builder spec

このフォルダは、やねうら王の頒布用実行ファイル・補助実行ファイルを作るためのビルド仕様を整理する場所である。

現状は `YaneuraOu-Builder/refs/` の手書きスクリプトで、次の用途を個別に処理している。

- Windows x64 / Windows x86 / macOS 向けの頒布パッケージ生成
- ペタショック化用 `YO-MATERIAL.exe` の生成
- SPSA 調整用実行ファイルの生成
- SPSA 適用後ソースからの実行ファイル生成

今後は、これらを Python GUI から同じ操作体系で扱えるようにする。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [current-build-flow.md](current-build-flow.md) | `YaneuraOu-Builder/refs/` にある既存スクリプトの現状仕様。入力、出力、ビルド matrix、補助ビルド、問題点を整理する。 |
| [gui-concept.md](gui-concept.md) | Python GUI 化する場合の画面構成、ビルドレシピ、キュー、成果物、検証、段階的実装案。 |
