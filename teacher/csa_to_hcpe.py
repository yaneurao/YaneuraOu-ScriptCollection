#!/usr/bin/env python3
"""Convert CSA game records to an HCPE teacher file.

This converter intentionally does not require engine eval comments in CSA
files.  It writes the played move as bestMove16, a constant eval value, and the
CSA game result to every position.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import BinaryIO

import cshogi
from cshogi import CSA
import numpy as np


COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherFormatLib import HCPE  # noqa: E402


CSA_BLACK_RATE_RE = re.compile(r"^'black_rate\s*:\s*(.+)$", re.IGNORECASE)
CSA_WHITE_RATE_RE = re.compile(r"^'white_rate\s*:\s*(.+)$", re.IGNORECASE)


@dataclass
class Stats:
    files: int = 0
    games: int = 0
    output_games: int = 0
    positions: int = 0
    skipped_endgame: int = 0
    skipped_moves: int = 0
    skipped_rating: int = 0
    skipped_missing_rating: int = 0
    skipped_duplicate: int = 0
    skipped_error: int = 0


def parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"?", "-"}:
        return None
    text = text.split()[0].rstrip(",")
    try:
        return float(text)
    except ValueError:
        return None


def read_header_ratings(path: Path) -> tuple[float | None, float | None]:
    black_rating: float | None = None
    white_rating: float | None = None

    with path.open("rb") as f:
        for raw_line in f:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if line.startswith(("+", "-")):
                break

            match = CSA_BLACK_RATE_RE.match(line)
            if match:
                black_rating = parse_optional_float(match.group(1))
                continue

            match = CSA_WHITE_RATE_RE.match(line)
            if match:
                white_rating = parse_optional_float(match.group(1))

    return black_rating, white_rating


def get_game_ratings(kif: object, path: Path) -> tuple[float | None, float | None]:
    ratings = getattr(kif, "ratings", None)
    if ratings is not None and len(ratings) >= 2:
        black_rating = parse_optional_float(ratings[0])
        white_rating = parse_optional_float(ratings[1])
        if (
            black_rating is not None
            and white_rating is not None
            and (black_rating > 0 or white_rating > 0)
        ):
            return black_rating, white_rating

    return read_header_ratings(path)


def collect_csa_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".csa":
            raise ValueError(f"input file must have .csa extension: {input_path}")
        return [input_path]

    if not input_path.is_dir():
        raise FileNotFoundError(f"input folder not found: {input_path}")

    pattern = "**/*" if recursive else "*"
    files = sorted(
        path for path in input_path.glob(pattern)
        if path.is_file() and path.suffix.lower() == ".csa"
    )
    if not files:
        raise FileNotFoundError(f"no .csa files found in: {input_path}")
    return files


def duplicate_key(kif: object) -> str:
    moves = getattr(kif, "moves", [])
    sfen = getattr(kif, "sfen", cshogi.STARTING_SFEN)
    return sfen + " " + " ".join(cshogi.move_to_usi(move) for move in moves)


def write_game_hcpe(kif: object, output: BinaryIO, teacher_eval: int) -> int:
    moves = getattr(kif, "moves", [])
    if not moves:
        return 0

    board = cshogi.Board()
    board.set_sfen(getattr(kif, "sfen", cshogi.STARTING_SFEN))

    records = np.zeros(len(moves), dtype=HCPE)
    p = 0
    for move in moves:
        if not board.is_legal(move):
            raise ValueError(f"illegal move: {cshogi.move_to_usi(move)}")

        record = records[p]
        board.to_hcp(record["hcp"])
        record["eval"] = teacher_eval
        record["bestMove16"] = cshogi.move16(move)
        record["gameResult"] = int(getattr(kif, "win", 0))
        p += 1
        board.push(move)

    records[:p].tofile(output)
    return p


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert CSA format game records to one HCPE file. "
            "The converter does not require eval comments."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", help="optional positional input and output paths")
    parser.add_argument("--input", "-i", type=Path, help="input .csa file or folder")
    parser.add_argument("--output", "-o", type=Path, help="output .hcpe file")
    parser.add_argument(
        "--filter_rating", "--filter-rating",
        dest="filter_rating",
        type=float,
        default=0.0,
        help="only output games where both players have at least this rating; 0 disables the filter",
    )
    parser.add_argument(
        "--filter_moves", "--filter-moves", "--min-moves",
        dest="filter_moves",
        type=int,
        default=1,
        help="skip games with fewer moves than this value",
    )
    parser.add_argument("--out_draw", action="store_true", help="include senichite draw games")
    parser.add_argument("--out_maxmove", action="store_true", help="include jishogi/max-move games")
    parser.add_argument("--uniq", action="store_true", help="skip duplicated game move sequences")
    parser.add_argument(
        "--teacher-eval",
        type=int,
        default=0,
        help="constant eval value written to HCPE records",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="do not scan input folders recursively",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=1000,
        help="print progress every N input files; 0 disables progress",
    )
    args = parser.parse_args()

    paths = list(args.paths)
    if args.input is None and paths:
        args.input = Path(paths.pop(0))
    if args.output is None and paths:
        args.output = Path(paths.pop(0))
    if paths:
        parser.error(f"too many positional arguments: {' '.join(paths)}")
    if args.input is None:
        parser.error("--input/-i is required")
    if args.output is None:
        parser.error("--output/-o is required")
    if args.filter_moves < 0:
        parser.error("--filter_moves must be non-negative")
    if args.progress_interval < 0:
        parser.error("--progress-interval must be non-negative")
    return args


def main() -> None:
    args = parse_args()

    csa_files = collect_csa_files(args.input, recursive=not args.no_recursive)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    endgames = {"%TORYO", "%KACHI"}
    if args.out_draw:
        endgames.add("%SENNICHITE")
    if args.out_maxmove:
        endgames.add("%JISHOGI")

    stats = Stats(files=len(csa_files))
    duplicates: set[str] = set()

    with args.output.open("wb") as output:
        for index, path in enumerate(csa_files, start=1):
            if args.progress_interval and (
                index == 1 or index == len(csa_files) or index % args.progress_interval == 0
            ):
                print(
                    f"processed {index}/{len(csa_files)} files, "
                    f"games={stats.output_games}, positions={stats.positions}",
                    file=sys.stderr,
                    flush=True,
                )

            try:
                games = CSA.Parser.parse_file(str(path))
            except Exception as exc:  # noqa: BLE001
                stats.skipped_error += 1
                print(f"skip parse error: {path}: {exc}", file=sys.stderr)
                continue

            for kif in games:
                stats.games += 1

                if getattr(kif, "endgame", None) not in endgames:
                    stats.skipped_endgame += 1
                    continue

                if len(getattr(kif, "moves", [])) < args.filter_moves:
                    stats.skipped_moves += 1
                    continue

                if args.filter_rating > 0:
                    black_rating, white_rating = get_game_ratings(kif, path)
                    if black_rating is None or white_rating is None:
                        stats.skipped_missing_rating += 1
                        continue
                    if black_rating < args.filter_rating or white_rating < args.filter_rating:
                        stats.skipped_rating += 1
                        continue

                if args.uniq:
                    key = duplicate_key(kif)
                    if key in duplicates:
                        stats.skipped_duplicate += 1
                        continue
                    duplicates.add(key)

                try:
                    positions = write_game_hcpe(kif, output, args.teacher_eval)
                except Exception as exc:  # noqa: BLE001
                    stats.skipped_error += 1
                    print(f"skip convert error: {path}: {exc}", file=sys.stderr)
                    continue

                if positions == 0:
                    stats.skipped_moves += 1
                    continue

                stats.output_games += 1
                stats.positions += positions

    print(f"files {stats.files}")
    print(f"games {stats.games}")
    print(f"output_games {stats.output_games}")
    print(f"positions {stats.positions}")
    print(f"skipped_endgame {stats.skipped_endgame}")
    print(f"skipped_moves {stats.skipped_moves}")
    print(f"skipped_rating {stats.skipped_rating}")
    print(f"skipped_missing_rating {stats.skipped_missing_rating}")
    print(f"skipped_duplicate {stats.skipped_duplicate}")
    print(f"skipped_error {stats.skipped_error}")


if __name__ == "__main__":
    main()
