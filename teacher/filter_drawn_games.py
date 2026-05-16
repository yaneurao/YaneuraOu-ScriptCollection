# HCPEファイル / PSVファイルから対局結果が引き分けの局面を取り除く。
#
# コマンド例:
#   python filter_drawn_games.py input.hcpe output.hcpe
#   python filter_drawn_games.py input.psv  output.psv
#   python filter_drawn_games.py input.hcpe                              # → input.no-drawn.hcpe
#   python filter_drawn_games.py -source teacher/ -dest teacher-no-drawn/
#
# 想定用途:
#   `test_value_accuracy` 系メトリクス (BulletOu / YaneuraOu の
#   `test eval_accuracy`) は、引き分け局面を accuracy の分母分子から
#   除外して計算する (= W vs L の符号一致率)。これと数値を一致させたい
#   場合、検証用局面ファイル自体からも引き分けを取り除いておくのが
#   一番手っ取り早い。dlshogi本家の検証用局面集にも引き分けは含まれて
#   いない。

import argparse
import os
import sys
from pathlib import Path


# 各形式のレコードレイアウト。
#   HCPE (.hcpe, 38 byte/record):
#     bytes  0..31 : packedHcp
#     bytes 32..33 : eval        (int16 little-endian)
#     bytes 34..35 : bestMove16  (uint16)
#     byte  36     : gameResult  (uint8, 0=Draw, 1=BlackWin, 2=WhiteWin)
#     byte  37     : dummy
#
#   PSV (.psv, 40 byte/record = YaneuraOu PackedSfenValue):
#     bytes  0..31 : packedSfen
#     bytes 32..33 : score       (int16, STM perspective)
#     bytes 34..35 : move        (uint16)
#     bytes 36..37 : gamePly     (uint16)
#     byte  38     : game_result (int8, -1=Loss / 0=Draw / +1=Win, STM perspective)
#     byte  39     : padding
#
# 引き分け判定はどちらも「該当 byte == 0」で済む (符号有無無関係に 0x00 が draw)。
FORMATS = {
    ".hcpe": {"record_size": 38, "result_offset": 36},
    ".psv":  {"record_size": 40, "result_offset": 38},
}

DEFAULT_CHUNK_RECORDS = 1_000_000


def detect_format(path: Path) -> dict:
    suffix = path.suffix.lower()
    fmt = FORMATS.get(suffix)
    if fmt is None:
        raise ValueError(
            f"unsupported format: {suffix} (supported: {', '.join(FORMATS)})"
        )
    return fmt


def default_output_path(input_path: Path) -> Path:
    if input_path.suffix:
        return input_path.with_name(input_path.stem + ".no-drawn" + input_path.suffix)
    return input_path.with_name(input_path.name + ".no-drawn")


def filter_drawn_games(
    input_path: Path,
    output_path: Path,
    chunk_records: int,
) -> tuple[int, int, int, str]:
    fmt = detect_format(input_path)
    record_size = fmt["record_size"]
    result_offset = fmt["result_offset"]

    file_size = input_path.stat().st_size
    if file_size % record_size != 0:
        raise ValueError(
            f"record size mismatch: file size {file_size} is not divisible by "
            f"{record_size} (expected for {input_path.suffix})"
        )

    total = file_size // record_size
    kept = 0
    removed = 0
    chunk_size = record_size * chunk_records

    with input_path.open("rb") as r, output_path.open("wb") as w:
        while True:
            data = r.read(chunk_size)
            if not data:
                break

            if len(data) % record_size != 0:
                raise ValueError("internal error: partial record was read")

            out = bytearray()
            mv = memoryview(data)
            for offset in range(0, len(data), record_size):
                # game_result byte: HCPE は uint8 / PSV は int8 だが、
                # どちらも値 0 が draw を表すのでそのまま比較可能。
                if mv[offset + result_offset] == 0:
                    removed += 1
                    continue
                out.extend(mv[offset : offset + record_size])
                kept += 1

            w.write(out)

    return total, kept, removed, input_path.suffix.lower().lstrip(".")


def iter_source_files(source_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        path for path in source_dir.glob(pattern)
        if path.is_file() and path.suffix.lower() in FORMATS
    )


def filter_directory(
    source_dir: Path,
    dest_dir: Path,
    chunk_records: int,
    recursive: bool,
) -> tuple[int, int, int, int, int, int]:
    source_files = iter_source_files(source_dir, recursive)
    dest_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = 0
    total_records = 0
    kept_records = 0
    removed_records = 0

    for input_path in source_files:
        relative_path = input_path.relative_to(source_dir)
        output_path = dest_dir / relative_path
        if os.path.abspath(input_path) == os.path.abspath(output_path):
            print(f"Skip: input and output are the same file: {input_path}", file=sys.stderr)
            failed += 1
            continue

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            total, kept, removed, fmt = filter_drawn_games(
                input_path,
                output_path,
                chunk_records,
            )
        except Exception as e:
            print(f"Error! : {input_path}: {e}", file=sys.stderr)
            failed += 1
            continue

        print(f"{input_path} -> {output_path} ({fmt}): kept {kept} / {total}, removed {removed}")
        succeeded += 1
        total_records += total
        kept_records += kept
        removed_records += removed

    return len(source_files), succeeded, failed, total_records, kept_records, removed_records


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HCPE / PSV ファイルから対局結果が引き分け (game_result == 0) の "
            "局面を取り除きます。形式は拡張子で自動判別します (.hcpe / .psv)。"
        )
    )
    parser.add_argument("input", nargs="?", help="入力ファイル (.hcpe または .psv)")
    parser.add_argument(
        "output",
        nargs="?",
        help=(
            "出力ファイル。省略時は <input>.no-drawn<.ext> のような名前にします。"
            " 入力と同じ拡張子で書き出します。"
        ),
    )
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=DEFAULT_CHUNK_RECORDS,
        help=f"一度に処理するレコード数。(default: {DEFAULT_CHUNK_RECORDS})",
    )
    parser.add_argument(
        "-source",
        "--source",
        dest="source_dir",
        help="一括処理する入力フォルダ。.hcpe / .psv ファイルを処理します。",
    )
    parser.add_argument(
        "-dest",
        "--dest",
        dest="dest_dir",
        help="一括処理の出力フォルダ。入力ファイルと同じ相対pathで出力します。",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="-source配下のサブフォルダも再帰的に処理します。",
    )

    args = parser.parse_args()

    if args.chunk_records <= 0:
        print("Error! : chunk-records must be greater than 0.", file=sys.stderr)
        return 1

    directory_mode = args.source_dir is not None or args.dest_dir is not None
    if directory_mode:
        if args.source_dir is None or args.dest_dir is None:
            print("Error! : -source and -dest must be specified together.", file=sys.stderr)
            return 1
        if args.input is not None or args.output is not None:
            print("Error! : positional input/output cannot be used with -source/-dest.", file=sys.stderr)
            return 1

        source_dir = Path(args.source_dir)
        dest_dir = Path(args.dest_dir)
        if not source_dir.is_dir():
            print(f"Error! : source directory not found: {source_dir}", file=sys.stderr)
            return 1

        source_resolved = source_dir.resolve()
        dest_resolved = dest_dir.resolve()
        if source_resolved == dest_resolved:
            print("Error! : source and dest must be different directories.", file=sys.stderr)
            return 1
        if args.recursive and dest_resolved.is_relative_to(source_resolved):
            print("Error! : with --recursive, dest must not be inside source.", file=sys.stderr)
            return 1

        print("Source dir     : ", source_dir)
        print("Dest dir       : ", dest_dir)
        print("Remove rule    :  game_result == 0 (drawn games)")
        print("Recursive      : ", args.recursive)

        file_count, succeeded, failed, total, kept, removed = filter_directory(
            source_dir,
            dest_dir,
            args.chunk_records,
            args.recursive,
        )

        print("Files found    : ", file_count)
        print("Files succeeded: ", succeeded)
        print("Files failed   : ", failed)
        print("Total records  : ", total)
        print("Kept records   : ", kept)
        print("Removed records: ", removed)
        return 1 if failed else 0

    if args.input is None:
        parser.print_help(sys.stderr)
        return 1

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path(input_path)

    if not input_path.is_file():
        print(f"Error! : input file not found: {input_path}", file=sys.stderr)
        return 1
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        print("Error! : input and output must be different files.", file=sys.stderr)
        return 1
    if input_path.suffix.lower() != output_path.suffix.lower():
        print(
            f"Warning: input ({input_path.suffix}) and output ({output_path.suffix}) "
            "have different extensions. The output will be written in the input's "
            "format regardless of its name.",
            file=sys.stderr,
        )

    print("Input          : ", input_path)
    print("Output         : ", output_path)
    print("Remove rule    :  game_result == 0 (drawn games)")

    try:
        total, kept, removed, fmt = filter_drawn_games(
            input_path,
            output_path,
            args.chunk_records,
        )
    except Exception as e:
        print(f"Error! : {e}", file=sys.stderr)
        return 1

    print("Format         : ", fmt)
    print("Total records  : ", total)
    print("Kept records   : ", kept)
    print("Removed records: ", removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
