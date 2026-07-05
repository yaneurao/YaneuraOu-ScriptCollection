from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, TextIO

import cshogi  # type: ignore
import numpy as np


YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"
UINT64_MOD = 1 << 64

YBB_MAGIC = b"YANE-BINBOOK-V1\0"
YBB_FLAG_MOVE_DEPTH = 1
YBB_KNOWN_FLAGS = YBB_FLAG_MOVE_DEPTH
YBB_HEADER_STRUCT = struct.Struct("<16sQQ")
YBB_INDEX_STRUCT = struct.Struct("<32sQHH")
YBB_MOVE_STRUCT = struct.Struct("<Hh")
YBB_MOVE_DEPTH_STRUCT = struct.Struct("<HhH")

MOVE_NONE = 0
MOVE_NULL = (1 << 7) + 1
MOVE_RESIGN = (2 << 7) + 2
MOVE_WIN = (3 << 7) + 3


@dataclass
class BookMove:
    move: str
    ponder: str
    value: int
    depth: int
    move_count: int


def c_atoll(token: str, default: int) -> int:
    if token == "":
        return default

    i = 0
    sign = 1
    if token[i : i + 1] in ("+", "-"):
        sign = -1 if token[i] == "-" else 1
        i += 1

    value = 0
    found = False
    while i < len(token) and token[i].isdigit():
        value = value * 10 + ord(token[i]) - ord("0")
        i += 1
        found = True

    return sign * value if found else 0


def as_u64_from_s64(value: int) -> int:
    return value % UINT64_MOD


def trim_number(s: str) -> str:
    end = len(s)
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    while end > 0 and s[end - 1].isdigit():
        end -= 1
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    return s[:end]


def sfen_ply(sfen: str) -> int:
    left = trim_number(sfen)
    tail = sfen[len(left) :]
    try:
        return int(tail.strip())
    except ValueError:
        return 0


def read_text_lines(path: str):
    with open(path, "r", encoding="utf-8-sig", newline=None) as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r").strip(" \t")
            if line != "":
                yield line


def parse_yaneuraou_noe(line: str) -> int | None:
    line = line.strip()
    if not line.startswith("#"):
        return None

    body = line[1:].strip()
    if not body.upper().startswith("NOE"):
        return None

    value_text = body[3:].strip()
    if value_text.startswith(":") or value_text.startswith("="):
        value_text = value_text[1:].strip()
    if not value_text:
        return None

    token = value_text.split()[0].replace(",", "")
    return int(token) if token.isdigit() else None


def count_yaneuraou_db_positions(path: str | Path) -> int:
    count = 0
    with Path(path).open("r", encoding="utf-8-sig", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            noe = parse_yaneuraou_noe(line)
            if noe is not None:
                return noe
            if line.startswith("sfen "):
                count += 1
    return count


def format_position_progress(done: int, total: int | None) -> str:
    if total is None:
        return f"done={done:,}, total=unknown, remaining=unknown"

    remaining = max(total - done, 0)
    percent = 100.0 if total == 0 else done * 100.0 / total
    return f"done={done:,}/{total:,}, remaining={remaining:,}, {percent:.2f}%"


def should_report_progress(done: int, total: int | None, interval: int) -> bool:
    if done <= 0:
        return False
    if total is not None and done >= total:
        return True
    return interval > 0 and done % interval == 0


def cleanup_work_dir(work_dir: str | Path, tmp_dir: str | Path, tmp_dir_existed: bool) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
    if tmp_dir_existed:
        return

    try:
        Path(tmp_dir).rmdir()
    except OSError:
        pass


def split_space_tokens(line: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    n = len(line)
    while pos < n:
        while pos < n and line[pos] == " ":
            pos += 1
        if pos >= n:
            break
        start = pos
        while pos < n and line[pos] != " ":
            pos += 1
        tokens.append(line[start:pos])
    return tokens


def normalize_move(move: str) -> str:
    if move in ("", "none", "None", "resign"):
        return "none"
    return move


def parse_book_move(line: str) -> BookMove:
    tokens = split_space_tokens(line)
    move = normalize_move(tokens[0] if len(tokens) > 0 else "")
    ponder = normalize_move(tokens[1] if len(tokens) > 1 else "")
    value = c_atoll(tokens[2], 0) if len(tokens) > 2 else 0
    depth = c_atoll(tokens[3], 0) if len(tokens) > 3 else 0
    move_count = c_atoll(tokens[4], 1) if len(tokens) > 4 else 1
    return BookMove(move, ponder, value, depth, as_u64_from_s64(move_count))


def insert_book_move(moves: list[BookMove], new_move: BookMove) -> None:
    for i, old in enumerate(moves):
        if old.move == new_move.move:
            new_move.move_count = as_u64_from_s64(new_move.move_count) + old.move_count
            moves[i] = new_move
            return
    moves.append(new_move)


def read_yaneuraou_book(path: str, *, ignore_book_ply: bool = False) -> dict[str, list[BookMove]]:
    book: dict[str, list[BookMove]] = {}

    for sfen, block_moves in read_yaneuraou_book_blocks(
        path, ignore_book_ply=ignore_book_ply
    ):
        moves = book.setdefault(sfen, [])
        for move in block_moves:
            insert_book_move(moves, move)

    return book


def sorted_book_moves(moves: list[BookMove]) -> list[BookMove]:
    return sorted(moves, key=lambda move: (-move.move_count, -move.value))


def normalize_sfen(sfen: str) -> str:
    board = cshogi.Board()
    board.set_sfen(sfen)
    return board.sfen()


def is_ybb_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() == ".ybb"


def ybb_path_from_output(path: Path) -> Path:
    if path.suffix.lower() == ".ybb":
        return path
    return path.with_name(f"{path.name}.ybb")


def resolve_ybb_input(path: Path) -> Path:
    if path.name.lower().endswith(".ybb"):
        return path
    return path.with_name(f"{path.name}.ybb")


def trim_sfen_ply(sfen: str) -> tuple[str, int]:
    tokens = sfen.split()
    if tokens and tokens[0] == "sfen":
        tokens = tokens[1:]
    ply = 1
    if tokens:
        try:
            ply = int(tokens[-1])
            tokens = tokens[:-1]
        except ValueError:
            pass
    return " ".join(tokens), ply


def read_yaneuraou_book_blocks(
    path: str, *, ignore_book_ply: bool = False
) -> Iterator[tuple[str, list[BookMove]]]:
    if is_ybb_path(path):
        yield from read_ybb_book_blocks(Path(path), ignore_book_ply=ignore_book_ply)
        return

    yield from read_yaneuraou_db_book_blocks(path, ignore_book_ply=ignore_book_ply)


def read_yaneuraou_db_book_blocks(
    path: str, *, ignore_book_ply: bool = False
) -> Iterator[tuple[str, list[BookMove]]]:
    current_sfen = ""
    current_moves: list[BookMove] = []

    for line in read_text_lines(path):
        if line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("sfen "):
            if current_sfen != "":
                yield current_sfen, current_moves
            current_sfen = line[5:]
            if ignore_book_ply:
                current_sfen = trim_number(current_sfen)
            current_moves = []
            continue
        if current_sfen == "":
            continue

        insert_book_move(current_moves, parse_book_move(line))

    if current_sfen != "":
        yield current_sfen, current_moves


def read_ybb_header(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        header = f.read(YBB_HEADER_STRUCT.size)
    if len(header) != YBB_HEADER_STRUCT.size:
        raise ValueError(f"broken ybb header: {path}")
    magic, record_count, flags = YBB_HEADER_STRUCT.unpack(header)
    if magic != YBB_MAGIC:
        raise ValueError(f"invalid ybb magic: {path}")
    if flags & ~YBB_KNOWN_FLAGS:
        raise ValueError(f"unknown ybb flags: {flags}")
    return int(record_count), int(flags)


def normalize_probe_sfen(sfen: str) -> str:
    sfen = sfen.strip()
    if sfen == "startpos":
        return trim_number(cshogi.Board().sfen())
    if sfen.startswith("sfen "):
        sfen = sfen[5:].strip()
    return trim_number(sfen)


def board_from_packed_sfen(packed_sfen: bytes) -> cshogi.Board:
    board = cshogi.Board()
    psfen = np.frombuffer(packed_sfen, dtype=cshogi.PackedSfen, count=1)
    board.set_psfen(psfen)
    return board


def move16_to_usi(board: cshogi.Board, move16: int) -> str:
    if move16 == MOVE_NONE:
        return "none"
    if move16 == MOVE_NULL:
        return "null"
    if move16 == MOVE_RESIGN:
        return "resign"
    if move16 == MOVE_WIN:
        return "win"

    cshogi_move16 = cshogi.move16_from_psv(move16)
    move = board.move_from_move16(cshogi_move16)
    if move == cshogi.MOVE_NONE:
        return "none"
    return cshogi.move_to_usi(move)


class BookProbe:
    """やねうら王定跡を全読みせず、指定局面だけ読むための共通probe。"""

    def probe(self, sfen: str) -> list[BookMove] | None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "BookProbe":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class YbbBookProbe(BookProbe):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.record_count, self.flags = read_ybb_header(self.path)
        self.move_struct = (
            YBB_MOVE_DEPTH_STRUCT if self.flags & YBB_FLAG_MOVE_DEPTH else YBB_MOVE_STRUCT
        )
        self.moves_base = YBB_HEADER_STRUCT.size + self.record_count * YBB_INDEX_STRUCT.size
        file_size = self.path.stat().st_size
        if file_size < self.moves_base:
            raise ValueError(f"broken ybb index area: {self.path}")
        self.moves_file_size = file_size - self.moves_base
        self.file = self.path.open("rb")

    def close(self) -> None:
        self.file.close()

    def _index_record_at(self, index: int) -> tuple[bytes, int, int, int]:
        self.file.seek(YBB_HEADER_STRUCT.size + index * YBB_INDEX_STRUCT.size)
        record = self.file.read(YBB_INDEX_STRUCT.size)
        if len(record) != YBB_INDEX_STRUCT.size:
            raise ValueError(f"broken ybb index record: {self.path}")
        packed_sfen, move_offset, ply, move_count = YBB_INDEX_STRUCT.unpack(record)
        return packed_sfen, int(move_offset), int(ply), int(move_count)

    def probe(self, sfen: str) -> list[BookMove] | None:
        board = cshogi.Board()
        board.set_sfen(normalize_probe_sfen(sfen))
        target = pack_sfen(board)

        lo = 0
        hi = self.record_count
        while lo < hi:
            mid = (lo + hi) // 2
            packed_sfen, _move_offset, _ply, _move_count = self._index_record_at(mid)
            if packed_sfen < target:
                lo = mid + 1
            else:
                hi = mid

        if lo >= self.record_count:
            return None

        packed_sfen, move_offset, _ply, move_count = self._index_record_at(lo)
        if packed_sfen != target:
            return None

        moves_size = move_count * self.move_struct.size
        if move_offset + moves_size > self.moves_file_size:
            raise ValueError(f"moves offset is out of range at record {lo}")
        self.file.seek(self.moves_base + move_offset)
        moves_blob = self.file.read(moves_size)
        if len(moves_blob) != moves_size:
            raise ValueError(f"broken ybb move records at record {lo}")

        moves: list[BookMove] = []
        for offset in range(0, len(moves_blob), self.move_struct.size):
            if self.flags & YBB_FLAG_MOVE_DEPTH:
                move16, value, depth = self.move_struct.unpack(
                    moves_blob[offset : offset + self.move_struct.size]
                )
            else:
                move16, value = self.move_struct.unpack(
                    moves_blob[offset : offset + self.move_struct.size]
                )
                depth = 0
            moves.append(BookMove(move16_to_usi(board, move16), "none", value, depth, 1))
        return moves


def sfen_key_from_db_line(raw_line: bytes) -> str | None:
    try:
        line = raw_line.decode("utf-8-sig")
    except UnicodeDecodeError:
        line = raw_line.decode("utf-8", errors="replace")
    line = line.strip()
    if line.startswith("\ufeff"):
        line = line.lstrip("\ufeff")
    if not line.startswith("sfen "):
        return None
    return trim_number(line[5:].strip())


class DbBookProbe(BookProbe):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.file = self.path.open("rb")
        self.size = self.path.stat().st_size

    def close(self) -> None:
        self.file.close()

    def _seek_to_line_start(self, offset: int) -> None:
        offset = max(0, min(offset, self.size))
        if offset == 0:
            self.file.seek(0)
            return
        self.file.seek(offset - 1)
        if self.file.read(1) != b"\n":
            self.file.readline()

    def _sfen_line_at_or_after(self, offset: int) -> tuple[int, int, str] | None:
        self._seek_to_line_start(offset)
        while True:
            pos = self.file.tell()
            raw = self.file.readline()
            if not raw:
                return None
            next_pos = self.file.tell()
            key = sfen_key_from_db_line(raw)
            if key is not None:
                return pos, next_pos, key

    def _read_moves_after(self, offset: int) -> list[BookMove]:
        self.file.seek(offset)
        moves: list[BookMove] = []
        while True:
            raw = self.file.readline()
            if not raw:
                break
            try:
                line = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                line = raw.decode("utf-8", errors="replace")
            line = line.strip()
            if not line:
                continue
            if line.startswith("\ufeff"):
                line = line.lstrip("\ufeff")
            if line.startswith("sfen "):
                break
            if line.startswith("#") or line.startswith("//"):
                continue
            moves.append(parse_book_move(line))
        return moves

    def probe(self, sfen: str) -> list[BookMove] | None:
        target = normalize_probe_sfen(sfen)
        lo = 0
        hi = self.size

        while lo < hi:
            mid = (lo + hi) // 2
            entry = self._sfen_line_at_or_after(mid)
            if entry is None:
                hi = mid
                continue
            pos, next_pos, key = entry
            if key < target:
                lo = max(next_pos, mid + 1)
            else:
                hi = pos if pos < hi else mid

        entry = self._sfen_line_at_or_after(lo)
        if entry is None:
            return None
        _pos, next_pos, key = entry
        if key != target:
            return None
        return self._read_moves_after(next_pos)


def open_book_probe(path: str | Path) -> BookProbe:
    return YbbBookProbe(path) if is_ybb_path(path) else DbBookProbe(path)


def read_ybb_book_blocks(
    path: Path, *, ignore_book_ply: bool = False
) -> Iterator[tuple[str, list[BookMove]]]:
    record_count, flags = read_ybb_header(path)
    move_struct = YBB_MOVE_DEPTH_STRUCT if flags & YBB_FLAG_MOVE_DEPTH else YBB_MOVE_STRUCT
    moves_base = YBB_HEADER_STRUCT.size + record_count * YBB_INDEX_STRUCT.size
    file_size = path.stat().st_size
    if file_size < moves_base:
        raise ValueError(f"broken ybb index area: {path}")
    moves_file_size = file_size - moves_base
    previous_packed_sfen: bytes | None = None

    with path.open("rb") as index_file, path.open("rb") as moves_file:
        index_file.seek(YBB_HEADER_STRUCT.size)
        for index in range(record_count):
            header = index_file.read(YBB_INDEX_STRUCT.size)
            if len(header) != YBB_INDEX_STRUCT.size:
                raise ValueError(f"broken ybb index record: {path}")
            packed_sfen, move_offset, ply, move_count = YBB_INDEX_STRUCT.unpack(header)
            if previous_packed_sfen is not None and packed_sfen <= previous_packed_sfen:
                raise ValueError(f"ybb index is not strictly sorted at record {index}")
            previous_packed_sfen = packed_sfen

            moves_size = move_count * move_struct.size
            if move_offset + moves_size > moves_file_size:
                raise ValueError(f"moves offset is out of range at record {index}")
            moves_file.seek(moves_base + move_offset)
            moves_blob = moves_file.read(moves_size)
            if len(moves_blob) != moves_size:
                raise ValueError(f"broken ybb move records at record {index}")

            board = board_from_packed_sfen(packed_sfen)
            sfen = f"{trim_number(board.sfen())} {ply}"
            if ignore_book_ply:
                sfen = trim_number(sfen)

            moves: list[BookMove] = []
            for offset in range(0, len(moves_blob), move_struct.size):
                if flags & YBB_FLAG_MOVE_DEPTH:
                    move16, value, depth = move_struct.unpack(
                        moves_blob[offset : offset + move_struct.size]
                    )
                else:
                    move16, value = move_struct.unpack(
                        moves_blob[offset : offset + move_struct.size]
                    )
                    depth = 0
                moves.append(
                    BookMove(move16_to_usi(board, move16), "none", value, depth, 1)
                )

            yield sfen, moves


def write_yaneuraou_header(out: TextIO) -> None:
    out.write(YANEURAOU_BOOK_HEADER_V1 + "\n")


def write_yaneuraou_book_block(out: TextIO, sfen: str, moves: list[BookMove]) -> int:
    out.write(f"sfen {sfen}\n")
    sorted_moves = sorted_book_moves(moves)
    for move in sorted_moves:
        out.write(
            f"{move.move} {move.ponder} {move.value} "
            f"{move.depth} {move.move_count}\n"
        )
    return len(sorted_moves)


def normalized_book_entries(
    book: dict[str, list[BookMove]]
) -> list[tuple[str, list[BookMove]]]:
    vectored_book: list[tuple[str, list[BookMove]]] = []
    book_ply: dict[str, int] = {}

    for sfen, moves in book.items():
        if not moves:
            continue

        normalized_sfen = normalize_sfen(sfen)
        vectored_book.append((normalized_sfen, moves))

        sfen_left = trim_number(normalized_sfen)
        ply = sfen_ply(normalized_sfen)
        old_ply = book_ply.get(sfen_left)
        book_ply[sfen_left] = ply if old_ply is None else min(old_ply, ply)

    vectored_book.sort(key=lambda item: item[0])
    return [
        (sfen, moves)
        for sfen, moves in vectored_book
        if book_ply[trim_number(sfen)] == sfen_ply(sfen)
    ]


def write_yaneuraou_db_book(book: dict[str, list[BookMove]], dst: str) -> None:
    with open(dst, "w", encoding="utf-8", newline="\r\n") as f:
        write_yaneuraou_header(f)
        for sfen, moves in normalized_book_entries(book):
            write_yaneuraou_book_block(f, sfen, moves)


def pack_sfen(board: cshogi.Board) -> bytes:
    psfen = np.empty(1, dtype=cshogi.PackedSfen)
    board.to_psfen(psfen)
    return psfen[0]["sfen"].tobytes()


def usi_to_move16(board: cshogi.Board, usi: str) -> int:
    if usi in ("none", "None"):
        return MOVE_NONE
    if usi in ("null", "0000", "pass"):
        return MOVE_NULL
    if usi == "resign":
        return MOVE_RESIGN
    if usi == "win":
        return MOVE_WIN
    move = board.move_from_usi(usi)
    if move == cshogi.MOVE_NONE:
        raise ValueError(f"invalid move for position: {usi} / {board.sfen()}")
    return int(cshogi.move16_to_psv(cshogi.move16(move)))


def validate_ybb_move(move: BookMove) -> None:
    if move.value < -32768 or move.value > 32767:
        raise ValueError(f"eval is out of int16 range: {move.value}")
    if move.depth < 0 or move.depth > 65535:
        raise ValueError(f"depth is out of uint16 range: {move.depth}")


def ybb_record_from_book_block(sfen: str, moves: list[BookMove]) -> tuple[bytes, int, bytes]:
    board = cshogi.Board()
    board.set_sfen(sfen)
    packed_sfen = pack_sfen(board)
    ply = sfen_ply(sfen) or 1
    if ply < 0 or ply > 65535:
        raise ValueError(f"ply is out of uint16 range: {ply}")

    move_records: list[bytes] = []
    for move in sorted_book_moves(moves):
        validate_ybb_move(move)
        move16 = usi_to_move16(board, move.move)
        move_records.append(YBB_MOVE_DEPTH_STRUCT.pack(move16, move.value, move.depth))
    if len(move_records) > 65535:
        raise ValueError(f"too many moves in one position: {sfen}")

    return packed_sfen, ply, b"".join(move_records)


def write_ybb_book(book: dict[str, list[BookMove]], dst: str | Path) -> None:
    output_path = Path(dst)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output_path.with_name(output_path.name + ".tmp")

    records = [
        ybb_record_from_book_block(sfen, moves)
        for sfen, moves in normalized_book_entries(book)
    ]
    records.sort(key=lambda item: item[0])
    previous_packed_sfen: bytes | None = None
    for packed_sfen, _, _ in records:
        if previous_packed_sfen == packed_sfen:
            raise ValueError(f"duplicated packed sfen: {packed_sfen.hex()}")
        previous_packed_sfen = packed_sfen

    flags = YBB_FLAG_MOVE_DEPTH
    index_size = YBB_HEADER_STRUCT.size + len(records) * YBB_INDEX_STRUCT.size

    try:
        with output_tmp.open("w+b") as output_file:
            output_file.write(YBB_HEADER_STRUCT.pack(YBB_MAGIC, len(records), flags))
            index_offset = YBB_HEADER_STRUCT.size
            move_offset = 0

            for packed_sfen, ply, moves_blob in records:
                move_count = len(moves_blob) // YBB_MOVE_DEPTH_STRUCT.size
                output_file.seek(index_size + move_offset)
                output_file.write(moves_blob)
                output_file.seek(index_offset)
                output_file.write(
                    YBB_INDEX_STRUCT.pack(packed_sfen, move_offset, ply, move_count)
                )
                index_offset += YBB_INDEX_STRUCT.size
                move_offset += len(moves_blob)

        os.replace(output_tmp, output_path)
    except Exception:
        output_tmp.unlink(missing_ok=True)
        raise


def write_yaneuraou_book(book: dict[str, list[BookMove]], dst: str) -> None:
    if is_ybb_path(dst):
        write_ybb_book(book, dst)
    else:
        write_yaneuraou_db_book(book, dst)
