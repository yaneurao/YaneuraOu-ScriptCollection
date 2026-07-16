#!/usr/bin/env python3
"""
Concatenate HCPE3 files from multiple folders in a round-robin pattern.

HCPE3 files are sequences of game records and do not have a whole-file header,
so concatenating complete HCPE3 game records is valid.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys
import struct
import time


HCPE3_HEADER_SIZE = 36
MOVE_INFO_SIZE = 6
MOVE_VISITS_SIZE = 4
MOVE_NUM_OFFSET = 32
CANDIDATE_NUM_OFFSET = 4
MAX_MOVE_NUM = 513
MAX_CANDIDATE_NUM = 593


SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024 ** 2,
    "MB": 1024 ** 2,
    "MIB": 1024 ** 2,
    "G": 1024 ** 3,
    "GB": 1024 ** 3,
    "GIB": 1024 ** 3,
    "T": 1024 ** 4,
    "TB": 1024 ** 4,
    "TIB": 1024 ** 4,
}


@dataclass(frozen=True)
class SourceSpec:
    source_dir: Path
    files: list[Path]
    games: int


@dataclass(frozen=True)
class GameRecord:
    source_index: int
    source_dir: Path
    input_file: Path
    file_game_index: int
    source_game_index: int
    data: bytes


@dataclass
class GameRange:
    input_file: Path
    start: int
    end: int


@dataclass
class SourceOutputStats:
    games: int = 0
    bytes: int = 0
    ranges: list[GameRange] = field(default_factory=list)

    def add(self, record: GameRecord) -> None:
        self.games += 1
        self.bytes += len(record.data)

        if (
            self.ranges
            and self.ranges[-1].input_file == record.input_file
            and self.ranges[-1].end + 1 == record.file_game_index
        ):
            self.ranges[-1].end = record.file_game_index
        else:
            self.ranges.append(
                GameRange(record.input_file, record.file_game_index, record.file_game_index)
            )


@dataclass
class OutputStats:
    output_file: Path
    source_stats: list[SourceOutputStats]
    games: int = 0
    bytes: int = 0

    def add(self, record: GameRecord) -> None:
        self.games += 1
        self.bytes += len(record.data)
        self.source_stats[record.source_index - 1].add(record)


class SourceReader:
    def __init__(self, source_index: int, spec: SourceSpec):
        self.source_index = source_index
        self.spec = spec
        self.file_index = 0
        self.current_file = None
        self.current_path = None
        self.file_game_index = 0
        self.source_game_index = 0

    def close(self) -> None:
        if self.current_file is not None:
            self.current_file.close()
            self.current_file = None
            self.current_path = None

    def open_next_file(self) -> bool:
        self.close()
        if self.file_index >= len(self.spec.files):
            return False
        self.current_path = self.spec.files[self.file_index]
        self.file_index += 1
        self.file_game_index = 0
        self.current_file = self.current_path.open("rb")
        return True

    def next_game(self) -> GameRecord | None:
        while True:
            if self.current_file is None and not self.open_next_file():
                return None

            data = read_hcpe3_game(self.current_file, self.current_path)
            if data is None:
                self.close()
                continue

            self.file_game_index += 1
            self.source_game_index += 1
            return GameRecord(
                source_index=self.source_index,
                source_dir=self.spec.source_dir,
                input_file=self.current_path,
                file_game_index=self.file_game_index,
                source_game_index=self.source_game_index,
                data=data,
            )


class SmoothWeightedSelector:
    def __init__(self, sources: list[SourceSpec]):
        self.weights = [source.games for source in sources]
        self.remaining = [source.games for source in sources]
        self.current = [0 for _ in sources]
        self.total_weight = sum(self.weights)

    def next_index(self) -> int | None:
        best_index = None
        best_weight = None
        for i, remaining in enumerate(self.remaining):
            if remaining <= 0:
                continue
            self.current[i] += self.weights[i]
            if best_index is None or self.current[i] > best_weight:
                best_index = i
                best_weight = self.current[i]

        if best_index is None:
            return None

        self.current[best_index] -= self.total_weight
        self.remaining[best_index] -= 1
        return best_index


class ProgressReporter:
    def __init__(self, enabled: bool, interval: float):
        self.enabled = enabled
        self.interval = interval
        self.last_report = 0.0

    def report(self, message: str, *, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if force or self.last_report == 0.0 or now - self.last_report >= self.interval:
            print(message, file=sys.stderr, flush=True)
            self.last_report = now


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def collect_files(src_dir: Path, output_dir: Path, pattern: str, recursive: bool) -> list[Path]:
    files = src_dir.rglob(pattern) if recursive else src_dir.glob(pattern)
    resolved_output_dir = output_dir.resolve()
    return sorted(
        path
        for path in files
        if path.is_file() and not is_relative_to(path.resolve(), resolved_output_dir)
    )


def parse_sources(
    source_args: list[str],
    output_dir: Path,
    pattern: str,
    recursive: bool,
    progress: ProgressReporter,
) -> list[SourceSpec]:
    sources = []
    source_count = len(source_args)
    for source_index, source_dir_text in enumerate(source_args, start=1):
        source_dir = Path(source_dir_text)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source folder not found: {source_dir}")
        if source_dir.resolve() == output_dir.resolve():
            raise ValueError("source folders and --output must be different folders")

        files = collect_files(source_dir, output_dir, pattern, recursive)
        if not files:
            raise FileNotFoundError(f"no input files found in {source_dir}: {pattern}")

        source_bytes = sum(path.stat().st_size for path in files)
        progress.report(
            f"count start source {source_index}/{source_count} "
            f"{source_dir} files={len(files)} bytes={format_bytes(source_bytes)}",
            force=True,
        )

        games = 0
        counted_bytes = 0
        for file_index, path in enumerate(files, start=1):
            file_size = path.stat().st_size
            games_before_file = games
            bytes_before_file = counted_bytes

            def report_file_progress(file_pos: int, file_games: int, *, force: bool = False) -> None:
                source_pos = bytes_before_file + file_pos
                source_pct = source_pos * 100.0 / source_bytes if source_bytes > 0 else 100.0
                file_pct = file_pos * 100.0 / file_size if file_size > 0 else 100.0
                progress.report(
                    f"count source {source_index}/{source_count} "
                    f"{source_pct:5.1f}% {format_bytes(source_pos)}/{format_bytes(source_bytes)} "
                    f"file {file_index}/{len(files)} {file_pct:5.1f}% {path.name} "
                    f"games={games_before_file + file_games}",
                    force=force,
                )

            file_games = count_hcpe3_games(path, report_file_progress)
            games += file_games
            counted_bytes += file_size

        if games <= 0:
            raise RuntimeError(f"no HCPE3 games found in {source_dir}: {pattern}")
        progress.report(
            f"count done source {source_index}/{source_count} "
            f"{source_dir} files={len(files)} games={games} bytes={format_bytes(source_bytes)}",
            force=True,
        )
        sources.append(SourceSpec(source_dir=source_dir, files=files, games=games))

    return sources


def parse_size(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)([A-Za-z]*)", value.strip())
    if match is None:
        raise ValueError(f"invalid size: {value}")

    number = int(match.group(1))
    unit = match.group(2).upper()
    if unit not in SIZE_UNITS:
        raise ValueError(f"invalid size unit: {value}")
    size = number * SIZE_UNITS[unit]
    if size <= 0:
        raise ValueError(f"size must be positive: {value}")
    return size


def read_exact(file, size: int, path: Path) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise RuntimeError(f"truncated HCPE3 file: {path}")
    return data


def read_hcpe3_game(file, path: Path) -> bytes | None:
    header = file.read(HCPE3_HEADER_SIZE)
    if len(header) == 0:
        return None
    if len(header) != HCPE3_HEADER_SIZE:
        raise RuntimeError(f"truncated HCPE3 header: {path}")

    move_num = struct.unpack_from("<H", header, MOVE_NUM_OFFSET)[0]
    if move_num > MAX_MOVE_NUM:
        raise RuntimeError(f"invalid moveNum {move_num}: {path}")

    parts = [header]
    for _ in range(move_num):
        move_info = read_exact(file, MOVE_INFO_SIZE, path)
        candidate_num = struct.unpack_from("<H", move_info, CANDIDATE_NUM_OFFSET)[0]
        if candidate_num > MAX_CANDIDATE_NUM:
            raise RuntimeError(f"invalid candidateNum {candidate_num}: {path}")
        parts.append(move_info)
        if candidate_num > 0:
            parts.append(read_exact(file, MOVE_VISITS_SIZE * candidate_num, path))

    return b"".join(parts)


def count_hcpe3_games(path: Path, progress=None) -> int:
    games = 0
    file_size = path.stat().st_size
    with path.open("rb") as file:
        while True:
            header = file.read(HCPE3_HEADER_SIZE)
            if len(header) == 0:
                break
            if len(header) != HCPE3_HEADER_SIZE:
                raise RuntimeError(f"truncated HCPE3 header: {path}")

            move_num = struct.unpack_from("<H", header, MOVE_NUM_OFFSET)[0]
            if move_num > MAX_MOVE_NUM:
                raise RuntimeError(f"invalid moveNum {move_num}: {path}")

            for _ in range(move_num):
                move_info = read_exact(file, MOVE_INFO_SIZE, path)
                candidate_num = struct.unpack_from("<H", move_info, CANDIDATE_NUM_OFFSET)[0]
                if candidate_num > MAX_CANDIDATE_NUM:
                    raise RuntimeError(f"invalid candidateNum {candidate_num}: {path}")
                if candidate_num > 0:
                    file.seek(MOVE_VISITS_SIZE * candidate_num, 1)
                    if file.tell() > file_size:
                        raise RuntimeError(f"truncated HCPE3 MoveVisits: {path}")
            games += 1
            if progress is not None and games % 100 == 0:
                progress(file.tell(), games)
        if progress is not None:
            progress(file.tell(), games)
    return games


def make_output_path(output_dir: Path, prefix: str, index: int, digits: int) -> Path:
    return output_dir / f"{prefix}-{index:0{digits}d}.hcpe3"


def write_manifest_header(manifest, sources: list[SourceSpec]) -> None:
    columns = ["output", "bytes", "games"]
    for source_index, _ in enumerate(sources, start=1):
        columns.extend(
            [
                f"source{source_index}_games",
                f"source{source_index}_bytes",
                f"source{source_index}_ranges",
            ]
        )
    manifest.write("\t".join(columns) + "\n")


def format_ranges(ranges: list[GameRange]) -> str:
    texts = []
    for game_range in ranges:
        if game_range.start == game_range.end:
            texts.append(f"{game_range.input_file}:{game_range.start}")
        else:
            texts.append(
                f"{game_range.input_file}:{game_range.start}-{game_range.end}"
            )
    return ";".join(texts)


def write_manifest_row(manifest, stats: OutputStats) -> None:
    columns = [str(stats.output_file), str(stats.bytes), str(stats.games)]
    for source_stats in stats.source_stats:
        columns.extend(
            [
                str(source_stats.games),
                str(source_stats.bytes),
                format_ranges(source_stats.ranges),
            ]
        )
    manifest.write("\t".join(columns) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Concatenate HCPE3 game records from multiple folders. The script "
            "first counts games in each --source and then mixes records by that "
            "ratio using smooth weighted round-robin. Output files are split by "
            "--max-output-size when specified."
        )
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    parser.add_argument(
        "--source",
        action="append",
        metavar="DIR",
        required=True,
        help="source folder; can be specified multiple times",
    )
    parser.add_argument("--pattern", default="*.hcpe3", help="input filename pattern")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect input files from each source folder",
    )
    parser.add_argument("--prefix", default="mixed", help="output filename prefix")
    parser.add_argument(
        "--digits",
        type=int,
        default=5,
        help="zero-padding width for output file numbers",
    )
    parser.add_argument(
        "--max-outputs",
        type=int,
        help="maximum number of output files to write",
    )
    parser.add_argument(
        "--max-output-size",
        type=parse_size,
        metavar="SIZE",
        help="maximum output file size, such as 512M, 8G, or byte count",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="do not write the manifest TSV file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow overwriting existing output and manifest files",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=5.0,
        help="seconds between progress messages",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable progress messages",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.digits <= 0:
        raise ValueError("--digits must be positive")
    if args.max_outputs is not None and args.max_outputs <= 0:
        raise ValueError("--max-outputs must be positive")
    if args.progress_interval < 0:
        raise ValueError("--progress-interval must be non-negative")

    progress = ProgressReporter(not args.no_progress, args.progress_interval)
    sources = parse_sources(args.source, args.output, args.pattern, args.recursive, progress)
    selector = SmoothWeightedSelector(sources)
    readers = [
        SourceReader(source_index, source)
        for source_index, source in enumerate(sources, start=1)
    ]

    manifest_path = args.output / f"{args.prefix}-manifest.tsv"
    if not args.no_manifest and manifest_path.exists() and not args.force:
        raise FileExistsError(
            "manifest already exists; use --force to overwrite: " + str(manifest_path)
        )

    args.output.mkdir(parents=True, exist_ok=True)

    manifest = None
    if not args.no_manifest:
        manifest = manifest_path.open("w", encoding="utf-8", newline="")
        write_manifest_header(manifest, sources)

    source_used_games = [0 for _ in sources]
    source_used_bytes = [0 for _ in sources]
    source_used_files = [set() for _ in sources]
    output = None
    output_stats = None
    output_index = 0
    outputs = 0
    total_games = sum(source.games for source in sources)
    written_games = 0

    def start_output():
        nonlocal output, output_stats, output_index
        if args.max_outputs is not None and output_index >= args.max_outputs:
            return False
        output_index += 1
        output_file = make_output_path(args.output, args.prefix, output_index, args.digits)
        if output_file.exists() and not args.force:
            raise FileExistsError(
                "output already exists; use --force to overwrite: " + str(output_file)
            )
        output = output_file.open("wb")
        output_stats = OutputStats(
            output_file=output_file,
            source_stats=[SourceOutputStats() for _ in sources],
        )
        return True

    def finish_output():
        nonlocal output, output_stats, outputs
        if output is None:
            return
        output.close()
        output = None
        if output_stats.games > 0:
            if manifest is not None:
                write_manifest_row(manifest, output_stats)
            print(output_stats.output_file, "games", output_stats.games, "bytes", output_stats.bytes)
            outputs += 1
        output_stats = None

    try:
        while True:
            source_index = selector.next_index()
            if source_index is None:
                break

            record = readers[source_index].next_game()
            if record is None:
                raise RuntimeError(
                    f"source ended earlier than counted: {sources[source_index].source_dir}"
                )

            if (
                args.max_output_size is not None
                and output_stats is not None
                and output_stats.bytes > 0
                and output_stats.bytes + len(record.data) > args.max_output_size
            ):
                finish_output()

            if output is None and not start_output():
                break

            output.write(record.data)
            output_stats.add(record)
            source_pos = record.source_index - 1
            source_used_games[source_pos] += 1
            source_used_bytes[source_pos] += len(record.data)
            source_used_files[source_pos].add(record.input_file)
            written_games += 1
            progress.report(
                f"write {written_games}/{total_games} games "
                f"({written_games * 100.0 / total_games:5.1f}%) "
                f"current_output={output_stats.output_file.name} "
                f"current_bytes={format_bytes(output_stats.bytes)}",
            )

        finish_output()
    finally:
        if manifest is not None:
            manifest.close()
        for reader in readers:
            reader.close()

    if outputs == 0:
        raise ValueError("no output files were written from the specified sources")

    for i, source in enumerate(sources, start=1):
        print(
            f"source{i}",
            source.source_dir,
            "input_files",
            len(source.files),
            "total_games",
            source.games,
            "used_files",
            len(source_used_files[i - 1]),
            "used_games",
            source_used_games[i - 1],
            "used_bytes",
            source_used_bytes[i - 1],
        )
    print("outputs", outputs)


if __name__ == "__main__":
    main()
