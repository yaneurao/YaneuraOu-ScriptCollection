#!/usr/bin/env python3
"""
HCPE3 files to PSV(PackedSfenValue) converter.

The converter streams HCPE3 games and writes PSV records one by one, so memory
usage is independent of input size.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import BinaryIO, Iterable
import sys

import cshogi
import numpy as np

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherFormatLib import (  # noqa: E402
    ConvertStats,
    HCPE3_HEADER,
    MOVE_INFO,
    MOVE_VISITS,
    PSV,
    classify_input,
    classify_output,
    collect_inputs,
    game_result_for_side_to_move,
    make_progress,
    output_for_file,
    read_exact,
)


def convert_one_file(
    input_path: Path,
    output: BinaryIO,
    *,
    no_progress: bool = False,
) -> ConvertStats:
    stats = ConvertStats(files=1)
    board = cshogi.Board()
    psv = np.zeros(1, dtype=PSV)
    file_size = input_path.stat().st_size

    progress = make_progress(input_path, no_progress=no_progress)

    def update_progress(n: int) -> None:
        if progress is not None:
            progress.update(n)

    try:
        with input_path.open("rb") as f:
            while True:
                header_bytes = f.read(HCPE3_HEADER.itemsize)
                if not header_bytes:
                    break
                if len(header_bytes) != HCPE3_HEADER.itemsize:
                    raise EOFError(
                        f"{input_path}: truncated HCPE3 header at game {stats.games}"
                    )
                update_progress(len(header_bytes))

                header = np.frombuffer(header_bytes, dtype=HCPE3_HEADER, count=1)[0]
                move_num = int(header["moveNum"])
                board.set_hcp(header["hcp"])
                if not board.is_ok():
                    raise ValueError(f"{input_path}: invalid HCP at game {stats.games}")

                for ply in range(move_num):
                    mi_bytes = read_exact(
                        f,
                        MOVE_INFO.itemsize,
                        f"{input_path}: MoveInfo at game {stats.games}, ply {ply}",
                    )
                    update_progress(len(mi_bytes))
                    move_info = np.frombuffer(mi_bytes, dtype=MOVE_INFO, count=1)[0]

                    candidate_num = int(move_info["candidateNum"])
                    selected_move16 = int(move_info["selectedMove16"]) & 0xFFFF

                    psv.fill(0)
                    board.to_psfen(psv["sfen"])
                    psv["score"][0] = int(move_info["eval"])
                    psv["move"][0] = cshogi.move16_to_psv(selected_move16)
                    psv["gamePly"][0] = ply
                    psv["game_result"][0] = game_result_for_side_to_move(
                        int(header["result"]), board.turn
                    )
                    psv.tofile(output)
                    stats.positions += 1

                    if candidate_num:
                        visits_bytes = read_exact(
                            f,
                            MOVE_VISITS.itemsize * candidate_num,
                            f"{input_path}: MoveVisits at game {stats.games}, ply {ply}",
                        )
                        update_progress(len(visits_bytes))

                    if ply + 1 < move_num:
                        try:
                            board.push_move16(selected_move16)
                        except Exception as exc:
                            raise ValueError(
                                f"{input_path}: illegal selectedMove16 "
                                f"{selected_move16:#06x} at game {stats.games}, ply {ply}"
                            ) from exc

                stats.games += 1

        if progress is not None and progress.n < file_size:
            progress.update(file_size - progress.n)
    finally:
        if progress is not None:
            progress.close()

    return stats


def convert_to_single_file(
    input_files: Iterable[Path],
    output_path: Path,
    *,
    no_progress: bool,
) -> ConvertStats:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = ConvertStats()
    with output_path.open("wb") as output:
        for input_file in input_files:
            stats = convert_one_file(input_file, output, no_progress=no_progress)
            total.add(stats)
    print(f"{output_path}: files={total.files}, games={total.games}, positions={total.positions}")
    return total


def convert_to_output_folder(
    input_files: Iterable[Path],
    input_root: Path,
    output_dir: Path,
    *,
    recursive: bool,
    no_progress: bool,
) -> ConvertStats:
    total = ConvertStats()
    for input_file in input_files:
        output_path = output_for_file(input_file, input_root, output_dir, "psv", recursive)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as output:
            stats = convert_one_file(input_file, output, no_progress=no_progress)
        total.add(stats)
        print(f"{input_file} -> {output_path}: games={stats.games}, positions={stats.positions}")

    print(f"total: files={total.files}, games={total.games}, positions={total.positions}")
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert HCPE3 files to PSV(PackedSfenValue). "
            "A path with an extension is treated as a file; a path without an "
            "extension is treated as a folder."
        )
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="input .hcpe3 file or folder")
    parser.add_argument("--output", "-o", type=Path, required=True, help="output .psv file or folder")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect *.hcpe3 when --input is a folder",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable tqdm progress bars",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_mode = classify_input(args.input, "hcpe3")
    output_mode = classify_output(args.output, "psv")
    input_files = collect_inputs(args.input, "hcpe3", args.recursive)

    if output_mode == "file":
        convert_to_single_file(input_files, args.output, no_progress=args.no_progress)
        return

    convert_to_output_folder(
        input_files,
        args.input if input_mode == "folder" else args.input.parent,
        args.output,
        recursive=args.recursive,
        no_progress=args.no_progress,
    )


if __name__ == "__main__":
    main()
