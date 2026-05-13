# makebook-script

YaneuraOu の `makebook` コマンドの一部を Python スクリプトとして外部化するためのディレクトリです。

## 目次

| スクリプト | 用途 | 入力 | 出力 |
| --- | --- | --- | --- |
| [`convert_to_apery.py`](convert_to_apery.py) | やねうら王標準定跡を Apery定跡へ変換します。 | `#YANEURAOU-DB2016 1.00` 形式の `.db` | Apery定跡の `.bin` |
| [`convert_from_apery.py`](convert_from_apery.py) | Apery定跡をやねうら王標準定跡へ変換します。 | Apery定跡の `.bin` | `#YANEURAOU-DB2016 1.00` 形式の `.db` |

## 必要なもの

- Python 3
- `cshogi`
- `numpy` (`convert_from_apery.py` で使用)

## 使い方

```bash
python3 convert_to_apery.py input.db output.bin
python3 convert_from_apery.py input.bin output.db
```

`convert_from_apery.py` は、未登録局面を何手先まで探索するかを `--unreg-depth` で指定できます。

```bash
python3 convert_from_apery.py --unreg-depth 1 input.bin output.db
```
