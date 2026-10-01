# YaneuraOu-Builder

`YaneuraOu-Builder` は、やねうら王のリリース用ビルドscriptを生成・実行するための
Python GUIです。GUIでビルド設定を編集し、具体的な build plan と実行scriptを
`runs/` 以下に出力します。

## archの追加

[arch-list.txt](arch-list.txt) に1行1archで追記し、GUIを再起動してください。
JSONやPythonの編集は不要です。空行と `#` から始まるコメント行を使用できます。

```text
NNUE_HALFKP_256X2_32_32
SFNN_halfka2_1024_8_64_k3k3
MATERIAL
```

`edition = YANEURAOU_ENGINE_ + arch`、`artifact_prefix = YaneuraOu_ + arch` として
一律に生成します。初回・新規追加archは選択済みで、保存済みの同じeditionの選択状態は維持します。
従来の `NNUE` は明示的な `NNUE_HALFKP_256X2_32_32` に置き換えたため、新規の行として扱います。
出力ファイル名の大小文字もarchの記載どおりになります。ビルド元ソースが未対応のarchは追加してもビルドできません。
BookMinerでMATERIAL版を使う場合は、生成した実行ファイルを配置先の指定名 `YO-MATERIAL.exe` に変更してください。

## 目次

- [はじめに](tutorial/getting-started.md)
- [Windows x64 / x86 ビルドチュートリアル](tutorial/windows-x86-x64.md)
- [Windows ARM ビルドチュートリアル](tutorial/windows-arm.md)
- [BookMinerCpp ビルドチュートリアル](tutorial/bookminer-cpp.md)
- [仕様メモ](spec/index.md)
