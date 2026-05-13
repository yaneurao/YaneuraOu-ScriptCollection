# HCPEファイルから評価値が大きすぎる局面を取り除く。
#
# コマンド例:
#   python filter_hcpe_by_eval.py input.hcpe output.hcpe
#   python filter_hcpe_by_eval.py input.hcpe --threshold 25000
#   python filter_hcpe_by_eval.py -source hcpe/ -dest hcpe-filtered-by-eval/

import argparse
import os
import sys
from pathlib import Path


HCPE_RECORD_SIZE = 38
HCPE_EVAL_OFFSET = 32
DEFAULT_THRESHOLD = 25000
DEFAULT_CHUNK_RECORDS = 1_000_000


def default_output_path(input_path: Path) -> Path:
    if input_path.suffix:
        return input_path.with_name(input_path.stem + ".filtered" + input_path.suffix)
    return input_path.with_name(input_path.name + ".filtered.hcpe")


def filter_hcpe_by_eval(
    input_path: Path,
    output_path: Path,
    threshold: int,
    chunk_records: int,
) -> tuple[int, int, int]:
    file_size = input_path.stat().st_size
    if file_size % HCPE_RECORD_SIZE != 0:
        raise ValueError(
            f"HCPE record size mismatch: file size {file_size} is not divisible by {HCPE_RECORD_SIZE}"
        )

    total = file_size // HCPE_RECORD_SIZE
    kept = 0
    removed = 0
    chunk_size = HCPE_RECORD_SIZE * chunk_records

    with input_path.open("rb") as r, output_path.open("wb") as w:
        while True:
            data = r.read(chunk_size)
            if not data:
                break

            if len(data) % HCPE_RECORD_SIZE != 0:
                raise ValueError("internal error: partial HCPE record was read")

            out = bytearray()
            mv = memoryview(data)
            for offset in range(0, len(data), HCPE_RECORD_SIZE):
                eval16 = int.from_bytes(
                    mv[offset + HCPE_EVAL_OFFSET : offset + HCPE_EVAL_OFFSET + 2],
                    byteorder="little",
                    signed=True,
                )

                if abs(eval16) >= threshold:
                    removed += 1
                    continue

                out.extend(mv[offset : offset + HCPE_RECORD_SIZE])
                kept += 1

            w.write(out)

    return total, kept, removed


def iter_source_files(source_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(path for path in source_dir.glob(pattern) if path.is_file())


def filter_hcpe_directory(
    source_dir: Path,
    dest_dir: Path,
    threshold: int,
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
            total, kept, removed = filter_hcpe_by_eval(
                input_path,
                output_path,
                threshold,
                chunk_records,
            )
        except Exception as e:
            print(f"Error! : {input_path}: {e}", file=sys.stderr)
            failed += 1
            continue

        print(f"{input_path} -> {output_path}: kept {kept} / {total}, removed {removed}")
        succeeded += 1
        total_records += total
        kept_records += kept
        removed_records += removed

    return len(source_files), succeeded, failed, total_records, kept_records, removed_records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HCPEファイルから abs(eval) >= threshold の局面を取り除きます。"
    )
    parser.add_argument("input_hcpe", nargs="?", help="入力HCPEファイル")
    parser.add_argument(
        "output_hcpe",
        nargs="?",
        help="出力HCPEファイル。省略時は input.filtered.hcpe のような名前にします。",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"取り除く評価値の絶対値の下限。abs(eval) >= threshold を削除します。(default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=DEFAULT_CHUNK_RECORDS,
        help=f"一度に処理するHCPE record数。(default: {DEFAULT_CHUNK_RECORDS})",
    )
    parser.add_argument(
        "-source",
        "--source",
        dest="source_dir",
        help="一括処理する入力フォルダ。この直下の通常ファイルを処理します。",
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

    if args.threshold <= 0:
        print("Error! : threshold must be greater than 0.", file=sys.stderr)
        return 1
    if args.chunk_records <= 0:
        print("Error! : chunk-records must be greater than 0.", file=sys.stderr)
        return 1

    directory_mode = args.source_dir is not None or args.dest_dir is not None
    if directory_mode:
        if args.source_dir is None or args.dest_dir is None:
            print("Error! : -source and -dest must be specified together.", file=sys.stderr)
            return 1
        if args.input_hcpe is not None or args.output_hcpe is not None:
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
        print("Remove rule    : ", f"abs(eval) >= {args.threshold}")
        print("Recursive      : ", args.recursive)

        file_count, succeeded, failed, total, kept, removed = filter_hcpe_directory(
            source_dir,
            dest_dir,
            args.threshold,
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

    if args.input_hcpe is None:
        parser.print_help(sys.stderr)
        return 1

    input_path = Path(args.input_hcpe)
    output_path = Path(args.output_hcpe) if args.output_hcpe else default_output_path(input_path)

    if not input_path.is_file():
        print(f"Error! : input file not found: {input_path}", file=sys.stderr)
        return 1
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        print("Error! : input and output must be different files.", file=sys.stderr)
        return 1

    print("Input          : ", input_path)
    print("Output         : ", output_path)
    print("Remove rule    : ", f"abs(eval) >= {args.threshold}")

    try:
        total, kept, removed = filter_hcpe_by_eval(
            input_path,
            output_path,
            args.threshold,
            args.chunk_records,
        )
    except Exception as e:
        print(f"Error! : {e}", file=sys.stderr)
        return 1

    print("Total records  : ", total)
    print("Kept records   : ", kept)
    print("Removed records: ", removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
