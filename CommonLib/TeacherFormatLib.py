"""Common binary format helpers for teacher-data scripts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import cshogi
import numpy as np

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


HCPE = cshogi.HuffmanCodedPosAndEval
HCPE_SIZE = HCPE.itemsize

HCPE3_HEADER = np.dtype(
    [
        ("hcp", cshogi.dtypeHcp),
        ("moveNum", "<u2"),
        ("result", "u1"),
        ("gameInfo", "u1"),
    ]
)

MOVE_INFO = np.dtype(
    [
        ("selectedMove16", cshogi.dtypeMove16),
        ("eval", cshogi.dtypeEval),
        ("candidateNum", "<u2"),
    ]
)

MOVE_VISITS = np.dtype(
    [
        ("move16", cshogi.dtypeMove16),
        ("visitNum", "<u2"),
    ]
)

PSV = cshogi.PackedSfenValue
PSV_SIZE = PSV.itemsize

if HCPE_SIZE != 38:
    raise RuntimeError(f"HCPE size must be 38 bytes: {HCPE_SIZE}")
if HCPE3_HEADER.itemsize != 36:
    raise RuntimeError(f"HCPE3 header size must be 36 bytes: {HCPE3_HEADER.itemsize}")
if MOVE_INFO.itemsize != 6:
    raise RuntimeError(f"MoveInfo size must be 6 bytes: {MOVE_INFO.itemsize}")
if MOVE_VISITS.itemsize != 4:
    raise RuntimeError(f"MoveVisits size must be 4 bytes: {MOVE_VISITS.itemsize}")
if PSV_SIZE != 40:
    raise RuntimeError(f"PSV size must be 40 bytes: {PSV_SIZE}")


@dataclass
class ConvertStats:
    files: int = 0
    games: int = 0
    positions: int = 0

    def add(self, other: "ConvertStats") -> None:
        self.files += other.files
        self.games += other.games
        self.positions += other.positions


def has_extension(path: Path) -> bool:
    return path.suffix != ""


def extension_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def classify_input(path: Path, input_ext: str) -> str:
    if has_extension(path):
        if extension_of(path) != input_ext.lower().lstrip("."):
            raise ValueError(f"--input file must have .{input_ext} extension: {path}")
        if not path.is_file():
            raise FileNotFoundError(f"input file not found: {path}")
        return "file"

    if not path.is_dir():
        raise FileNotFoundError(f"input folder not found: {path}")
    return "folder"


def classify_output(path: Path, output_ext: str) -> str:
    if has_extension(path):
        if extension_of(path) != output_ext.lower().lstrip("."):
            raise ValueError(f"--output file must have .{output_ext} extension: {path}")
        return "file"
    return "folder"


def collect_inputs(input_path: Path, input_ext: str, recursive: bool) -> list[Path]:
    mode = classify_input(input_path, input_ext)
    if mode == "file":
        return [input_path]

    pattern = f"**/*.{input_ext}" if recursive else f"*.{input_ext}"
    files = sorted(p for p in input_path.glob(pattern) if p.is_file())
    if not files:
        raise FileNotFoundError(f"no .{input_ext} files found in: {input_path}")
    return files


def output_for_file(
    input_file: Path,
    input_root: Path,
    output_dir: Path,
    output_ext: str,
    recursive: bool,
) -> Path:
    if recursive and input_root.is_dir():
        rel = input_file.relative_to(input_root)
        return (output_dir / rel).with_suffix(f".{output_ext}")
    return output_dir / f"{input_file.stem}.{output_ext}"


def read_exact(f: BinaryIO, size: int, context: str) -> bytes:
    data = f.read(size)
    if len(data) != size:
        raise EOFError(f"truncated {context}: expected {size} bytes, got {len(data)}")
    return data


def validate_fixed_record_file(path: Path, record_size: int, format_name: str) -> int:
    file_size = path.stat().st_size
    if file_size % record_size != 0:
        raise ValueError(
            f"{format_name} file size {file_size} is not divisible by "
            f"record size {record_size}: {path}"
        )
    return file_size // record_size


def hcpe_game_result_to_hcpe3_result(game_result: int) -> int:
    """
    Pack cshogi's HCPE gameResult into the low 2 bits of HCPE3 result.

    cshogi uses 0=Draw/unknown, 1=BlackWin, 2=WhiteWin. HCPE3 keeps the same
    values in result[1:0]; other HCPE3 result flags are intentionally cleared.
    """
    return int(game_result) & 0x3


def game_result_for_side_to_move(result: int, turn: int) -> int:
    """Convert HCPE3 result to PSV's side-to-move view: win=1, loss=-1, draw=0."""
    result &= 0x3
    if result == 1:
        return 1 if turn == cshogi.BLACK else -1
    if result == 2:
        return 1 if turn == cshogi.WHITE else -1
    return 0


def make_progress(path: Path, *, no_progress: bool):
    if tqdm is None or no_progress:
        return None
    return tqdm(
        total=path.stat().st_size,
        unit="B",
        unit_scale=True,
        desc=path.name,
        ncols=80,
    )
