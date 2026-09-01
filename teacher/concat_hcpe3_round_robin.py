#!/usr/bin/env python3
"""
Concatenate HCPE3 files from multiple sources in a round-robin pattern.

HCPE3 files are sequences of game records and do not have a whole-file header,
so concatenating complete HCPE3 game records is valid.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import heapq
from pathlib import Path
import re
import shutil
import sys
import struct
import time
import tempfile

import cshogi
import numpy as np


HCPE3_HEADER_SIZE = 36
MOVE_INFO_SIZE = 6
MOVE_VISITS_SIZE = 4
MOVE_NUM_OFFSET = 32
CANDIDATE_NUM_OFFSET = 4
MAX_MOVE_NUM = 513
MAX_CANDIDATE_NUM = 593
DEFAULT_POSITIONS_PER_OUTPUT = 10_000_000
DEFAULT_BUCKET_COUNT = 1024
UINT64_MASK = (1 << 64) - 1


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
class InputFileSpec:
    source_index: int
    source_dir: Path
    path: Path
    games: int


@dataclass(frozen=True)
class SourceSpec:
    source_dir: Path
    files: list[InputFileSpec]
    games: int


@dataclass(frozen=True)
class SourceInput:
    source_index: int
    source_dir: Path
    files: list[Path]
    bytes: int


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
    last_range_by_file: dict[Path, GameRange] = field(default_factory=dict, repr=False)

    def add(self, record: GameRecord) -> None:
        self.games += 1
        self.bytes += len(record.data)

        last_range = self.last_range_by_file.get(record.input_file)
        if (
            last_range is not None
            and last_range.end + 1 == record.file_game_index
        ):
            last_range.end = record.file_game_index
        else:
            new_range = GameRange(record.input_file, record.file_game_index, record.file_game_index)
            self.ranges.append(new_range)
            self.last_range_by_file[record.input_file] = new_range


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


@dataclass
class PositionOutputStats:
    output_file: Path
    positions: int = 0
    bytes: int = 0

    def add(self, record_size: int) -> None:
        self.positions += 1
        self.bytes += record_size


class InputFileReader:
    def __init__(self, spec: InputFileSpec):
        self.spec = spec
        self.current_file = None
        self.file_game_index = 0
        self.offset = 0

    def close(self) -> None:
        if self.current_file is not None:
            self.offset = self.current_file.tell()
            self.current_file.close()
            self.current_file = None

    def open(self):
        if self.current_file is None:
            self.current_file = self.spec.path.open("rb")
            if self.offset:
                self.current_file.seek(self.offset)
        return self.current_file

    def next_game(
        self,
        source_game_index: int,
        open_readers: OpenReaderCache,
    ) -> GameRecord | None:
        file = open_readers.open(self)
        data = read_hcpe3_game(file, self.spec.path)
        self.offset = file.tell()
        if data is None:
            open_readers.close(self)
            return None

        self.file_game_index += 1
        record = GameRecord(
            source_index=self.spec.source_index,
            source_dir=self.spec.source_dir,
            input_file=self.spec.path,
            file_game_index=self.file_game_index,
            source_game_index=source_game_index,
            data=data,
        )

        if self.file_game_index >= self.spec.games:
            open_readers.close(self)

        return record


class OpenReaderCache:
    def __init__(self, max_open_files: int):
        self.max_open_files = max_open_files
        self.readers = []

    def open(self, reader: InputFileReader):
        if reader.current_file is not None:
            self._touch(reader)
            return reader.current_file

        while len(self.readers) >= self.max_open_files:
            self.readers.pop(0).close()

        file = reader.open()
        self.readers.append(reader)
        return file

    def close(self, reader: InputFileReader) -> None:
        if reader in self.readers:
            self.readers.remove(reader)
        reader.close()

    def close_all(self) -> None:
        for reader in list(self.readers):
            reader.close()
        self.readers = []

    def _touch(self, reader: InputFileReader) -> None:
        self.readers.remove(reader)
        self.readers.append(reader)


class WeightedSelector:
    def __init__(self, weights: list[int]):
        self.weights = weights
        self.remaining = list(weights)
        self.used = [0 for _ in weights]
        self.heap = [
            (0.0, index)
            for index, weight in enumerate(weights)
            if weight > 0
        ]
        heapq.heapify(self.heap)

    def next_index(self) -> int | None:
        if not self.heap:
            return None

        _, index = heapq.heappop(self.heap)
        self.used[index] += 1
        self.remaining[index] -= 1

        if self.remaining[index] > 0:
            heapq.heappush(
                self.heap,
                (self.used[index] / self.weights[index], index),
            )

        return index


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


def collect_source_inputs(
    source_args: list[str],
    output_dir: Path,
    pattern: str,
    recursive: bool,
) -> list[SourceInput]:
    source_paths = [Path(source_text) for source_text in source_args]
    missing_sources = [
        source_path for source_path in source_paths
        if not source_path.is_dir() and not source_path.is_file()
    ]
    if missing_sources:
        missing_text = "\n  ".join(str(source_path) for source_path in missing_sources)
        raise FileNotFoundError(f"source not found:\n  {missing_text}")

    resolved_output_dir = output_dir.resolve()
    same_as_output = [
        source_path for source_path in source_paths
        if source_path.is_dir() and source_path.resolve() == resolved_output_dir
    ]
    if same_as_output:
        source_text = "\n  ".join(str(source_path) for source_path in same_as_output)
        raise ValueError(
            "source folders and --output must be different folders:\n  "
            + source_text
        )
    files_in_output = [
        source_path for source_path in source_paths
        if source_path.is_file() and is_relative_to(source_path.resolve(), resolved_output_dir)
    ]
    if files_in_output:
        source_text = "\n  ".join(str(source_path) for source_path in files_in_output)
        raise ValueError(
            "source files must not be inside --output folder:\n  "
            + source_text
        )

    source_inputs = []
    empty_sources = []
    for source_index, source_path in enumerate(source_paths, start=1):
        if source_path.is_file():
            files = [source_path]
        else:
            files = collect_files(source_path, output_dir, pattern, recursive)

        if not files:
            empty_sources.append(source_path)
            continue
        source_inputs.append(
            SourceInput(
                source_index=source_index,
                source_dir=source_path,
                files=files,
                bytes=sum(path.stat().st_size for path in files),
            )
        )

    if empty_sources:
        empty_text = "\n  ".join(str(source_dir) for source_dir in empty_sources)
        raise FileNotFoundError(
            f"no input files found for pattern {pattern}:\n  {empty_text}"
        )

    return source_inputs


def parse_sources(
    source_inputs: list[SourceInput],
    progress: ProgressReporter,
) -> list[SourceSpec]:
    sources = []
    source_count = len(source_inputs)
    for source_input in source_inputs:
        source_index = source_input.source_index
        source_dir = source_input.source_dir
        files = source_input.files
        source_bytes = source_input.bytes
        progress.report(
            f"count start source {source_index}/{source_count} "
            f"{source_dir} files={len(files)} bytes={format_bytes(source_bytes)}",
            force=True,
        )

        file_specs = []
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
            file_specs.append(InputFileSpec(source_index, source_dir, path, file_games))
            games += file_games
            counted_bytes += file_size

        if games <= 0:
            raise RuntimeError(f"no HCPE3 games found in {source_dir}: {pattern}")
        progress.report(
            f"count done source {source_index}/{source_count} "
            f"{source_dir} files={len(files)} games={games} bytes={format_bytes(source_bytes)}",
            force=True,
        )
        sources.append(SourceSpec(source_dir=source_dir, files=file_specs, games=games))

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


def iter_hcpe3_position_records(record: GameRecord):
    board = cshogi.Board()
    hcp = np.zeros(1, dtype=cshogi.dtypeHcp)
    offset = 0
    header = record.data[offset:offset + HCPE3_HEADER_SIZE]
    offset += HCPE3_HEADER_SIZE
    move_num = struct.unpack_from("<H", header, MOVE_NUM_OFFSET)[0]

    board.set_hcp(np.frombuffer(header[:32], dtype=cshogi.dtypeHcp, count=1)[0])
    if not board.is_ok():
        raise RuntimeError(
            f"invalid HCP: {record.input_file} game={record.file_game_index}"
        )

    for ply in range(move_num):
        move_info = record.data[offset:offset + MOVE_INFO_SIZE]
        offset += MOVE_INFO_SIZE
        candidate_num = struct.unpack_from("<H", move_info, CANDIDATE_NUM_OFFSET)[0]
        visits_size = MOVE_VISITS_SIZE * candidate_num
        visits = record.data[offset:offset + visits_size]
        offset += visits_size

        board.to_hcp(hcp[0])
        position_header = bytearray(header)
        position_header[:32] = hcp[0].tobytes()
        struct.pack_into("<H", position_header, MOVE_NUM_OFFSET, 1)
        position_record = bytes(position_header) + move_info + visits
        yield hcp[0].tobytes(), position_record

        if ply + 1 < move_num:
            selected_move16 = struct.unpack_from("<H", move_info, 0)[0]
            try:
                board.push_move16(selected_move16)
            except Exception as exc:
                raise RuntimeError(
                    f"illegal selectedMove16 {selected_move16:#06x}: "
                    f"{record.input_file} game={record.file_game_index} ply={ply}"
                ) from exc


def packed_position_xor_key(hcp_bytes: bytes, seed: int) -> int:
    words = struct.unpack("<QQQQ", hcp_bytes)
    key = words[0] ^ words[1] ^ words[2] ^ words[3]
    if seed:
        key ^= seed & UINT64_MASK
    return key


def bucket_path(work_dir: Path, bucket: int) -> Path:
    return work_dir / f"bucket-{bucket:06}.hcpe3"


def write_length_prefixed(file, record: bytes) -> None:
    file.write(struct.pack("<I", len(record)))
    file.write(record)


def read_length_prefixed_records(path: Path) -> list[bytes]:
    records = []
    with path.open("rb") as file:
        while True:
            size_bytes = file.read(4)
            if not size_bytes:
                break
            if len(size_bytes) != 4:
                raise RuntimeError(f"truncated bucket record header: {path}")
            size = struct.unpack("<I", size_bytes)[0]
            data = file.read(size)
            if len(data) != size:
                raise RuntimeError(f"truncated bucket record body: {path}")
            records.append(data)
    return records


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


def write_position_manifest_header(manifest) -> None:
    manifest.write("output\tbytes\tpositions\n")


def write_position_manifest_row(manifest, stats: PositionOutputStats) -> None:
    manifest.write(
        "\t".join([str(stats.output_file), str(stats.bytes), str(stats.positions)])
        + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Concatenate HCPE3 game records from multiple source folders or files. The script "
            "first counts games in each input file and then mixes records by "
            "weighted round-robin across sources and across files within each "
            "source. Output files are split by --split or "
            "--max-output-size when specified."
        )
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    parser.add_argument(
        "--source",
        action="append",
        metavar="PATH",
        required=True,
        help="source folder or HCPE3 file; can be specified multiple times",
    )
    parser.add_argument("--pattern", default="*.hcpe3", help="input filename pattern for source folders")
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
        "--split",
        type=int,
        help="number of output files to write",
    )
    parser.add_argument(
        "--max-output-size",
        type=parse_size,
        metavar="SIZE",
        help="maximum output file size, such as 512M, 8G, or byte count",
    )
    parser.add_argument(
        "--shuffle-positions",
        action="store_true",
        help=(
            "expand HCPE3 games into one-position HCPE3 records, shuffle them "
            "out-of-core, and split by --positions"
        ),
    )
    parser.add_argument(
        "--positions",
        type=int,
        default=DEFAULT_POSITIONS_PER_OUTPUT,
        help=(
            "positions per output file for --shuffle-positions "
            f"(default: {DEFAULT_POSITIONS_PER_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--bucket-count",
        type=int,
        default=DEFAULT_BUCKET_COUNT,
        help=(
            "temporary bucket count for --shuffle-positions "
            f"(default: {DEFAULT_BUCKET_COUNT})"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="shuffle seed for --shuffle-positions")
    parser.add_argument("--tmp-dir", type=Path, help="temporary directory root for --shuffle-positions")
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep temporary bucket files for --shuffle-positions",
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
    parser.add_argument(
        "--max-open-files",
        type=int,
        default=64,
        help="maximum number of HCPE3 input files kept open while merging",
    )
    return parser.parse_args()


class PositionSplitWriter:
    def __init__(
        self,
        output_dir: Path,
        prefix: str,
        positions: int | None,
        split_targets: list[int] | None,
        digits: int,
        manifest,
    ):
        self.output_dir = output_dir
        self.prefix = prefix
        self.positions = positions
        self.split_targets = split_targets
        self.digits = digits
        self.manifest = manifest
        self.output = None
        self.stats = None
        self.output_index = 0
        self.outputs = 0

    def close(self) -> None:
        if self.output is not None:
            self.output.close()
            self.output = None
            if self.stats.positions > 0:
                if self.manifest is not None:
                    write_position_manifest_row(self.manifest, self.stats)
                print(self.stats.output_file, "positions", self.stats.positions, "bytes", self.stats.bytes)
                self.outputs += 1
            self.stats = None

    def write(self, record: bytes) -> None:
        if self.output is None or self.stats.positions >= self.current_target():
            self.close()
            self.output_index += 1
            output_file = make_output_path(self.output_dir, self.prefix, self.output_index, self.digits)
            self.output = output_file.open("wb")
            self.stats = PositionOutputStats(output_file=output_file)

        self.output.write(record)
        self.stats.add(len(record))

    def current_target(self) -> int:
        if self.split_targets is not None:
            return self.split_targets[self.output_index - 1]
        return self.positions


def build_round_robin_state(sources: list[SourceSpec], max_open_files: int):
    source_selector = WeightedSelector([source.games for source in sources])
    files_by_source = [
        [file_spec for file_spec in source.files if file_spec.games > 0]
        for source in sources
    ]
    file_selectors = [
        WeightedSelector([file_spec.games for file_spec in file_specs])
        for file_specs in files_by_source
    ]
    readers_by_source = [
        [InputFileReader(file_spec) for file_spec in file_specs]
        for file_specs in files_by_source
    ]
    open_readers = OpenReaderCache(max_open_files)
    source_read_games = [0 for _ in sources]
    return source_selector, file_selectors, readers_by_source, open_readers, source_read_games


def iter_round_robin_records(sources: list[SourceSpec], max_open_files: int):
    (
        source_selector,
        file_selectors,
        readers_by_source,
        open_readers,
        source_read_games,
    ) = build_round_robin_state(sources, max_open_files)
    try:
        while True:
            source_pos = source_selector.next_index()
            if source_pos is None:
                break

            file_pos = file_selectors[source_pos].next_index()
            if file_pos is None:
                raise RuntimeError(
                    f"source file selector ended earlier than counted: "
                    f"{sources[source_pos].source_dir}"
                )

            reader = readers_by_source[source_pos][file_pos]
            source_read_games[source_pos] += 1
            record = reader.next_game(source_read_games[source_pos], open_readers)
            if record is None:
                raise RuntimeError(
                    f"input file ended earlier than counted: {reader.spec.path}"
                )
            yield record
    finally:
        open_readers.close_all()


def run_shuffle_positions(args, sources: list[SourceSpec], progress: ProgressReporter) -> None:
    manifest_path = args.output / f"{args.prefix}-manifest.tsv"
    if args.max_output_size is not None:
        raise ValueError("--max-output-size cannot be used with --shuffle-positions; use --positions")
    if args.max_outputs is not None:
        raise ValueError("--max-outputs cannot be used with --shuffle-positions")
    if args.split is not None and args.split <= 0:
        raise ValueError("--split must be positive")
    if args.split is not None and args.positions != DEFAULT_POSITIONS_PER_OUTPUT:
        raise ValueError("--split and --positions cannot be specified together")
    if args.positions <= 0:
        raise ValueError("--positions must be positive")
    if args.bucket_count <= 0:
        raise ValueError("--bucket-count must be positive")

    args.output.mkdir(parents=True, exist_ok=True)
    if not args.no_manifest and manifest_path.exists() and not args.force:
        raise FileExistsError(
            "manifest already exists; use --force to overwrite: " + str(manifest_path)
        )

    for output_file in args.output.glob(f"{args.prefix}-*.hcpe3"):
        if output_file == manifest_path:
            continue
        if not args.force:
            raise FileExistsError(
                "output already exists; use --force to overwrite: " + str(output_file)
            )
        output_file.unlink()

    tmp_root = args.tmp_dir if args.tmp_dir is not None else args.output
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".concat_hcpe3_shuffle-", dir=tmp_root))
    bucket_files = {}
    total_games = sum(source.games for source in sources)
    written_positions = 0
    written_games = 0

    try:
        for record in iter_round_robin_records(sources, args.max_open_files):
            written_games += 1
            for hcp_bytes, position_record in iter_hcpe3_position_records(record):
                bucket = packed_position_xor_key(hcp_bytes, args.seed) % args.bucket_count
                out = bucket_files.get(bucket)
                if out is None:
                    out = bucket_path(work_dir, bucket).open("ab")
                    bucket_files[bucket] = out
                write_length_prefixed(out, position_record)
                written_positions += 1

            progress.report(
                f"shard {written_games}/{total_games} games "
                f"({written_games * 100.0 / total_games:5.1f}%) "
                f"positions={written_positions}",
            )

        for out in bucket_files.values():
            out.close()
        bucket_files = {}

        manifest = None
        if not args.no_manifest:
            manifest = manifest_path.open("w", encoding="utf-8", newline="")
            write_position_manifest_header(manifest)

        split_targets = None
        if args.split is not None:
            if args.split > written_positions:
                raise ValueError(
                    f"--split cannot be greater than total positions: "
                    f"split={args.split}, total_positions={written_positions}"
                )
            base_positions, extra_outputs = divmod(written_positions, args.split)
            split_targets = [
                base_positions + (1 if output_pos < extra_outputs else 0)
                for output_pos in range(args.split)
            ]

        rng = np.random.default_rng(args.seed)
        writer = PositionSplitWriter(
            args.output,
            args.prefix,
            None if split_targets is not None else args.positions,
            split_targets,
            args.digits,
            manifest,
        )
        shuffled_positions = 0
        try:
            for bucket in range(args.bucket_count):
                path = bucket_path(work_dir, bucket)
                if not path.exists():
                    continue
                records = read_length_prefixed_records(path)
                order = rng.permutation(len(records))
                for index in order:
                    writer.write(records[int(index)])
                    shuffled_positions += 1
                progress.report(
                    f"write buckets {bucket + 1}/{args.bucket_count} "
                    f"positions={shuffled_positions}/{written_positions}",
                )
            writer.close()
        finally:
            writer.close()
            if manifest is not None:
                manifest.close()

        if writer.outputs == 0:
            raise ValueError("no output files were written from the specified sources")
        print("outputs", writer.outputs)
        print("positions", shuffled_positions)
    finally:
        for out in bucket_files.values():
            out.close()
        if not args.keep_temp:
            shutil.rmtree(work_dir, ignore_errors=True)
        else:
            print("temp", work_dir)


def main() -> None:
    args = parse_args()

    if args.digits <= 0:
        raise ValueError("--digits must be positive")
    if args.max_outputs is not None and args.max_outputs <= 0:
        raise ValueError("--max-outputs must be positive")
    if args.split is not None and args.split <= 0:
        raise ValueError("--split must be positive")
    if args.split is not None and args.max_output_size is not None:
        raise ValueError("--split and --max-output-size cannot be specified together")
    if args.split is not None and args.max_outputs is not None:
        raise ValueError("--split and --max-outputs cannot be specified together")
    if args.progress_interval < 0:
        raise ValueError("--progress-interval must be non-negative")
    if args.max_open_files <= 0:
        raise ValueError("--max-open-files must be positive")

    progress = ProgressReporter(not args.no_progress, args.progress_interval)
    source_inputs = collect_source_inputs(
        args.source,
        args.output,
        args.pattern,
        args.recursive,
    )
    sources = parse_sources(source_inputs, progress)
    if args.shuffle_positions:
        run_shuffle_positions(args, sources, progress)
        return

    source_selector = WeightedSelector([source.games for source in sources])
    files_by_source = [
        [file_spec for file_spec in source.files if file_spec.games > 0]
        for source in sources
    ]
    file_selectors = [
        WeightedSelector([file_spec.games for file_spec in file_specs])
        for file_specs in files_by_source
    ]
    readers_by_source = [
        [InputFileReader(file_spec) for file_spec in file_specs]
        for file_specs in files_by_source
    ]
    open_readers = OpenReaderCache(args.max_open_files)

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
    source_read_games = [0 for _ in sources]
    output = None
    output_stats = None
    output_index = 0
    outputs = 0
    total_games = sum(source.games for source in sources)
    if args.split is not None and args.split > total_games:
        raise ValueError(
            f"--split cannot be greater than total games: "
            f"split={args.split}, total_games={total_games}"
        )
    split_targets = None
    if args.split is not None:
        base_games, extra_outputs = divmod(total_games, args.split)
        split_targets = [
            base_games + (1 if output_pos < extra_outputs else 0)
            for output_pos in range(args.split)
        ]
    written_games = 0

    def start_output():
        nonlocal output, output_stats, output_index
        if split_targets is not None and output_index >= len(split_targets):
            return False
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
            source_pos = source_selector.next_index()
            if source_pos is None:
                break

            file_pos = file_selectors[source_pos].next_index()
            if file_pos is None:
                raise RuntimeError(
                    f"source file selector ended earlier than counted: "
                    f"{sources[source_pos].source_dir}"
                )

            reader = readers_by_source[source_pos][file_pos]
            source_read_games[source_pos] += 1
            record = reader.next_game(source_read_games[source_pos], open_readers)
            if record is None:
                raise RuntimeError(
                    f"input file ended earlier than counted: {reader.spec.path}"
                )

            if (
                args.max_output_size is not None
                and output_stats is not None
                and output_stats.bytes > 0
                and output_stats.bytes + len(record.data) > args.max_output_size
            ):
                finish_output()
            if (
                split_targets is not None
                and output_stats is not None
                and output_stats.games >= split_targets[output_index - 1]
            ):
                finish_output()

            if output is None and not start_output():
                break

            output.write(record.data)
            output_stats.add(record)
            record_source_pos = record.source_index - 1
            source_used_games[record_source_pos] += 1
            source_used_bytes[record_source_pos] += len(record.data)
            source_used_files[record_source_pos].add(record.input_file)
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
        open_readers.close_all()

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
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
