# やねうら王形式の定跡ファイルから互角局面を抽出するためのスクリプト

# この手数の局面から探す
PLY = 32

# 評価値の絶対値がこれ以下の局面を抽出する
EVAL_TH = 50

# やねうら王の定跡ファイルPATH
YANEURAOU_DB_PATH = r"C:\Users\yaneen\largefile\Shogi\Shogidokoro\Engine\tanuki-dr5_with_petabook\book\user_book1.db"

# 書き出す互角局面のファイルPATH
START_SFENS_PATH = "start_sfens.txt"

# 直近で読み込んだsfen
last_sfen = ""
count = 0
with open(START_SFENS_PATH, 'w', encoding='utf-8') as w:
    with open(YANEURAOU_DB_PATH, 'r', encoding='utf-8') as r:
        for line in r:
            if 'sfen' in line:
                last_sfen = line
                split_sfen = last_sfen.split()
                # 末尾に手数が付与されていることを前提とする。
                ply = int(split_sfen[-1])
                if ply != PLY:
                    last_sfen = ""
                continue

            if last_sfen == "":
                continue

            # 手数がPLYであるsfenの最初のmove(これがbestmove)
            # "9a6d none -26 0"
            eval = int(line.split()[2])
            if abs(eval) <= EVAL_TH:
                count += 1
                w.write(last_sfen) # ファイルに書き出す。
                if count % 100 == 0:
                    print(count)

            last_sfen = ""
