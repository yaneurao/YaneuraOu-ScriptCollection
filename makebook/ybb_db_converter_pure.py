#!/usr/bin/env python3
"""Pure Python .db <-> .ybb converter used by the GUI.

This module intentionally does not import cshogi or numpy so that it can be
packaged by PyInstaller as a simple standalone GUI application.
"""

from __future__ import annotations

import heapq
import os
import shutil
import struct
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"

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
MOVE_DROP = 1 << 14
MOVE_PROMOTE = 1 << 15

NO_PIECE_TYPE = 0
PAWN = 1
LANCE = 2
KNIGHT = 3
SILVER = 4
BISHOP = 5
ROOK = 6
GOLD = 7
KING = 8
PIECE_PROMOTE = 8
PIECE_WHITE = 16
SQ_NB = 81
BLACK = 0
WHITE = 1

PIECE_TO_CHAR = " PLNSBRGK        plnsbrgk"
BASE_PIECE_BY_CHAR = {
    "P": PAWN,
    "L": LANCE,
    "N": KNIGHT,
    "S": SILVER,
    "B": BISHOP,
    "R": ROOK,
    "G": GOLD,
    "K": KING,
    "p": PIECE_WHITE + PAWN,
    "l": PIECE_WHITE + LANCE,
    "n": PIECE_WHITE + KNIGHT,
    "s": PIECE_WHITE + SILVER,
    "b": PIECE_WHITE + BISHOP,
    "r": PIECE_WHITE + ROOK,
    "g": PIECE_WHITE + GOLD,
    "k": PIECE_WHITE + KING,
}
DROP_PIECE_BY_CHAR = {
    "P": PAWN,
    "L": LANCE,
    "N": KNIGHT,
    "S": SILVER,
    "B": BISHOP,
    "R": ROOK,
    "G": GOLD,
}
USI_HAND_ORDER = [ROOK, BISHOP, GOLD, SILVER, KNIGHT, LANCE, PAWN]
PACK_HAND_ORDER = [PAWN, LANCE, KNIGHT, SILVER, GOLD, BISHOP, ROOK]
ALL_PIECE_COUNTS = {
    PAWN: 18,
    LANCE: 4,
    KNIGHT: 4,
    SILVER: 4,
    BISHOP: 2,
    ROOK: 2,
    GOLD: 4,
}
HUFFMAN_TABLE = {
    NO_PIECE_TYPE: (0x00, 1),
    PAWN: (0x01, 2),
    LANCE: (0x03, 4),
    KNIGHT: (0x0B, 4),
    SILVER: (0x07, 4),
    BISHOP: (0x1F, 6),
    ROOK: (0x3F, 6),
    GOLD: (0x0F, 5),
}
HUFFMAN_TABLE_PIECEBOX = {
    PAWN: (0x02, 2),
    LANCE: (0x09, 4),
    KNIGHT: (0x0D, 4),
    SILVER: (0x0B, 4),
    BISHOP: (0x2F, 6),
    ROOK: (0x3F, 6),
    GOLD: (0x1B, 5),
}

RUN_MAGIC_YBB = b"YBBRUN1\0"
RUN_MAGIC_DB = b"DBRUN1\0\0"
RUN_HEADER_STRUCT = struct.Struct("<8sQ")
YBB_RUN_RECORD_STRUCT = struct.Struct("<32sHH")
DB_RUN_RECORD_STRUCT = struct.Struct("<II")

DEFAULT_CHUNK_POSITIONS = 500_000
DEFAULT_CHUNK_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_OPEN_RUNS = 64

YbbRunRecord = tuple[bytes, int, bytes]
DbRunRecord = tuple[bytes, bytes]


@dataclass
class ParsedSfen:
    board: list[int]
    hands: list[dict[int, int]]
    turn: int
    ply: int


class BitStream:
    def __init__(self, data: bytearray | bytes | None = None) -> None:
        self.data = bytearray(32) if data is None else bytearray(data)
        if len(self.data) != 32:
            raise ValueError("PackedSfen must be 32 bytes")
        self.cursor = 0

    def write_one_bit(self, value: int) -> None:
        if self.cursor >= 256:
            raise ValueError("PackedSfen write overflow")
        if value:
            self.data[self.cursor // 8] |= 1 << (self.cursor & 7)
        self.cursor += 1

    def write_n_bit(self, value: int, bits: int) -> None:
        for i in range(bits):
            self.write_one_bit(value & (1 << i))

    def read_one_bit(self) -> int:
        if self.cursor >= 256:
            raise ValueError("PackedSfen read overflow")
        value = (self.data[self.cursor // 8] >> (self.cursor & 7)) & 1
        self.cursor += 1
        return value

    def read_n_bit(self, bits: int) -> int:
        result = 0
        for i in range(bits):
            if self.read_one_bit():
                result |= 1 << i
        return result


def square_from_file_rank(file_index: int, rank_index: int) -> int:
    if not (0 <= file_index < 9 and 0 <= rank_index < 9):
        raise ValueError("square is out of range")
    return file_index * 9 + rank_index


def square_from_usi(text: str) -> int:
    if len(text) != 2:
        raise ValueError(f"invalid square: {text}")
    file_index = ord(text[0]) - ord("1")
    rank_index = ord(text[1]) - ord("a")
    return square_from_file_rank(file_index, rank_index)


def square_to_usi(square: int) -> str:
    if not (0 <= square < SQ_NB):
        raise ValueError(f"invalid square: {square}")
    return f"{chr(ord('1') + square // 9)}{chr(ord('a') + square % 9)}"


def color_of(piece: int) -> int:
    return (piece & PIECE_WHITE) >> 4


def type_of(piece: int) -> int:
    return piece & 15


def raw_type_of(piece: int) -> int:
    return piece & 7


def is_promoted(piece_or_type: int) -> bool:
    return type_of(piece_or_type) >= PIECE_PROMOTE + PAWN


def make_piece(color: int, piece_type: int) -> int:
    return (color << 4) + piece_type


def add_hand(hands: list[dict[int, int]], color: int, piece_type: int, count: int = 1) -> None:
    hands[color][piece_type] = hands[color].get(piece_type, 0) + count


def hand_count(hands: list[dict[int, int]], color: int, piece_type: int) -> int:
    return hands[color].get(piece_type, 0)


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


def trim_number(s: str) -> str:
    end = len(s)
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    while end > 0 and s[end - 1].isdigit():
        end -= 1
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    return s[:end]


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
    token = value_text.split()[0].replace(",", "") if value_text else ""
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


def ybb_path_from_output(path: Path) -> Path:
    if path.suffix.lower() == ".ybb":
        return path
    return path.with_name(f"{path.name}.ybb")


def resolve_ybb_input(path: Path) -> Path:
    if path.name.lower().endswith(".ybb"):
        return path
    return path.with_name(f"{path.name}.ybb")


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


def parse_sfen(sfen: str) -> ParsedSfen:
    tokens = sfen.split()
    if tokens and tokens[0] == "sfen":
        tokens = tokens[1:]
    if len(tokens) < 3:
        raise ValueError(f"invalid sfen: {sfen}")

    board_text, turn_text, hand_text = tokens[:3]
    ply = 1
    if len(tokens) >= 4:
        try:
            ply = int(tokens[3])
        except ValueError as exc:
            raise ValueError(f"invalid sfen ply: {tokens[3]}") from exc

    board = [0] * SQ_NB
    ranks = board_text.split("/")
    if len(ranks) != 9:
        raise ValueError(f"invalid sfen board: {board_text}")

    for rank_index, rank_text in enumerate(ranks):
        file_index = 8
        promote = False
        for token in rank_text:
            if token.isdigit():
                if promote:
                    raise ValueError(f"invalid promoted marker in sfen: {sfen}")
                file_index -= int(token)
                continue
            if token == "+":
                if promote:
                    raise ValueError(f"duplicated promoted marker in sfen: {sfen}")
                promote = True
                continue
            piece = BASE_PIECE_BY_CHAR.get(token)
            if piece is None:
                raise ValueError(f"invalid piece in sfen: {token}")
            if not (0 <= file_index < 9):
                raise ValueError(f"invalid file in sfen: {sfen}")
            if promote:
                if type_of(piece) >= GOLD:
                    raise ValueError(f"invalid promoted piece in sfen: +{token}")
                piece |= PIECE_PROMOTE
            board[square_from_file_rank(file_index, rank_index)] = piece
            file_index -= 1
            promote = False
        if promote or file_index != -1:
            raise ValueError(f"invalid rank in sfen: {rank_text}")

    if turn_text not in ("b", "w"):
        raise ValueError(f"invalid side to move: {turn_text}")
    turn = BLACK if turn_text == "b" else WHITE

    hands: list[dict[int, int]] = [{}, {}]
    if hand_text != "-":
        count_text = ""
        for token in hand_text:
            if token.isdigit():
                count_text += token
                continue
            piece = BASE_PIECE_BY_CHAR.get(token)
            if piece is None:
                raise ValueError(f"invalid hand piece in sfen: {token}")
            piece_type = type_of(piece)
            if not (PAWN <= piece_type < KING):
                raise ValueError(f"invalid hand piece in sfen: {token}")
            count = int(count_text) if count_text else 1
            add_hand(hands, color_of(piece), piece_type, count)
            count_text = ""
        if count_text:
            raise ValueError(f"invalid hand count in sfen: {hand_text}")

    return ParsedSfen(board, hands, turn, ply)


def piece_to_sfen(piece: int) -> str:
    piece_type = type_of(piece)
    raw_piece = piece & ~PIECE_PROMOTE
    if piece_type >= PIECE_PROMOTE + PAWN:
        return "+" + PIECE_TO_CHAR[raw_piece]
    return PIECE_TO_CHAR[piece]


def format_sfen(parsed: ParsedSfen) -> str:
    board_parts: list[str] = []
    for rank_index in range(9):
        empty = 0
        rank_parts: list[str] = []
        for file_index in range(8, -1, -1):
            piece = parsed.board[square_from_file_rank(file_index, rank_index)]
            if piece == 0:
                empty += 1
                continue
            if empty:
                rank_parts.append(str(empty))
                empty = 0
            rank_parts.append(piece_to_sfen(piece))
        if empty:
            rank_parts.append(str(empty))
        board_parts.append("".join(rank_parts))

    hand_parts: list[str] = []
    for color in (BLACK, WHITE):
        for piece_type in USI_HAND_ORDER:
            count = hand_count(parsed.hands, color, piece_type)
            if count == 0:
                continue
            if count != 1:
                hand_parts.append(str(count))
            hand_parts.append(PIECE_TO_CHAR[make_piece(color, piece_type)])

    hands = "".join(hand_parts) if hand_parts else "-"
    turn = "b" if parsed.turn == BLACK else "w"
    return f"{'/'.join(board_parts)} {turn} {hands} {parsed.ply}"


def write_board_piece(stream: BitStream, piece: int) -> None:
    piece_type = raw_type_of(piece)
    code, bits = HUFFMAN_TABLE[piece_type]
    stream.write_n_bit(code, bits)
    if piece == 0:
        return
    if piece_type != GOLD:
        stream.write_one_bit(1 if piece & PIECE_PROMOTE else 0)
    stream.write_one_bit(color_of(piece))


def write_hand_piece(stream: BitStream, piece: int) -> None:
    piece_type = raw_type_of(piece)
    code, bits = HUFFMAN_TABLE[piece_type]
    stream.write_n_bit(code >> 1, bits - 1)
    if piece_type != GOLD:
        stream.write_one_bit(0)
    stream.write_one_bit(color_of(piece))


def write_piecebox_piece(stream: BitStream, piece_type: int) -> None:
    code, bits = HUFFMAN_TABLE_PIECEBOX[piece_type]
    stream.write_n_bit(code, bits)
    if piece_type != GOLD:
        stream.write_one_bit(0)


def read_board_piece(stream: BitStream) -> int:
    code = 0
    bits = 0
    while True:
        code |= stream.read_one_bit() << bits
        bits += 1
        for piece_type, (table_code, table_bits) in HUFFMAN_TABLE.items():
            if table_code == code and table_bits == bits:
                if piece_type == NO_PIECE_TYPE:
                    return 0
                promote = False if piece_type == GOLD else bool(stream.read_one_bit())
                color = stream.read_one_bit()
                return make_piece(color, piece_type + (PIECE_PROMOTE if promote else 0))
        if bits > 6:
            raise ValueError("invalid board piece code in PackedSfen")


def read_hand_piece(stream: BitStream) -> int:
    code = 0
    bits = 0
    while True:
        code |= stream.read_one_bit() << bits
        bits += 1
        for piece_type in range(PAWN, KING):
            table_code, table_bits = HUFFMAN_TABLE[piece_type]
            if (table_code >> 1) == code and (table_bits - 1) == bits:
                if piece_type != GOLD and stream.read_one_bit():
                    piece_type |= PIECE_PROMOTE
                color = stream.read_one_bit()
                return make_piece(color, piece_type)
        if bits > 6:
            raise ValueError("invalid hand piece code in PackedSfen")


def pack_sfen_text(sfen: str) -> bytes:
    parsed = parse_sfen(sfen)
    stream = BitStream()
    stream.write_one_bit(parsed.turn)

    for color in (BLACK, WHITE):
        king = make_piece(color, KING)
        king_square = SQ_NB
        for square, piece in enumerate(parsed.board):
            if piece == king:
                king_square = square
                break
        stream.write_n_bit(king_square, 7)

    piecebox_counts = dict(ALL_PIECE_COUNTS)
    for square in range(SQ_NB):
        piece = parsed.board[square]
        if type_of(piece) == KING:
            continue
        write_board_piece(stream, piece)
        if piece:
            raw_piece = raw_type_of(piece)
            piecebox_counts[raw_piece] -= 1

    for color in (BLACK, WHITE):
        for piece_type in PACK_HAND_ORDER:
            count = hand_count(parsed.hands, color, piece_type)
            for _ in range(count):
                write_hand_piece(stream, make_piece(color, piece_type))
            piecebox_counts[piece_type] -= count

    for piece_type in PACK_HAND_ORDER:
        count = piecebox_counts[piece_type]
        if count < 0:
            raise ValueError(f"too many pieces in sfen: {sfen}")
        for _ in range(count):
            write_piecebox_piece(stream, piece_type)

    if stream.cursor != 256:
        raise ValueError(f"PackedSfen bit size mismatch: {stream.cursor}")
    return bytes(stream.data)


def unpack_sfen_text(packed_sfen: bytes, ply: int) -> str:
    stream = BitStream(packed_sfen)
    board = [0] * SQ_NB
    hands: list[dict[int, int]] = [{}, {}]
    turn = stream.read_one_bit()

    for color in (BLACK, WHITE):
        square = stream.read_n_bit(7)
        if square < SQ_NB:
            board[square] = make_piece(color, KING)

    for square in range(SQ_NB):
        if type_of(board[square]) == KING:
            continue
        board[square] = read_board_piece(stream)

    while stream.cursor < 256:
        piece = read_hand_piece(stream)
        if is_promoted(piece):
            continue
        add_hand(hands, color_of(piece), type_of(piece))

    if stream.cursor != 256:
        raise ValueError("PackedSfen bit size mismatch")
    return format_sfen(ParsedSfen(board, hands, turn, ply))


def usi_to_move16(usi: str) -> int:
    if usi in ("none", "None", ""):
        return MOVE_NONE
    if usi in ("null", "0000", "pass"):
        return MOVE_NULL
    if usi == "resign":
        return MOVE_RESIGN
    if usi == "win":
        return MOVE_WIN
    if len(usi) < 4:
        raise ValueError(f"invalid usi move: {usi}")
    to_square = square_from_usi(usi[2:4])
    if usi[1:2] == "*":
        piece_type = DROP_PIECE_BY_CHAR.get(usi[0])
        if piece_type is None:
            raise ValueError(f"invalid drop move: {usi}")
        return to_square + (piece_type << 7) + MOVE_DROP
    from_square = square_from_usi(usi[0:2])
    move = to_square + (from_square << 7)
    if len(usi) == 5 and usi[4] == "+":
        move += MOVE_PROMOTE
    elif len(usi) != 4:
        raise ValueError(f"invalid usi move: {usi}")
    return move


def move16_to_usi(move16: int) -> str:
    if move16 == MOVE_NONE:
        return "none"
    if move16 == MOVE_NULL:
        return "null"
    if move16 == MOVE_RESIGN:
        return "resign"
    if move16 == MOVE_WIN:
        return "win"
    to_square = move16 & 0x7F
    if move16 & MOVE_DROP:
        piece_type = (move16 >> 7) & 0x7F
        if not (PAWN <= piece_type < KING):
            return "none"
        return f"{PIECE_TO_CHAR[piece_type]}*{square_to_usi(to_square)}"
    from_square = (move16 >> 7) & 0x7F
    if from_square == to_square:
        return "none"
    suffix = "+" if move16 & MOVE_PROMOTE else ""
    return f"{square_to_usi(from_square)}{square_to_usi(to_square)}{suffix}"


def parse_move_line(line: str) -> tuple[str, int, int] | None:
    if "," in line:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[1]
        depth_text = parts[2] if len(parts) > 2 else "0"
    else:
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[2]
        depth_text = parts[3] if len(parts) > 3 else "0"
    if eval_text.lower() == "none":
        return None
    value = int(eval_text)
    if value < -32768 or value > 32767:
        raise ValueError(f"eval is out of int16 range: {value}")
    depth = int(depth_text)
    if depth < 0 or depth > 65535:
        raise ValueError(f"depth is out of uint16 range: {depth}")
    return move_text, value, depth


class YbbRunWriter:
    def __init__(self, path: Path, record_count: int, move_record_size: int) -> None:
        self.path = path
        self.record_count = record_count
        self.move_record_size = move_record_size
        self.written = 0
        self.file: BinaryIO | None = None

    def __enter__(self) -> "YbbRunWriter":
        self.file = self.path.open("wb")
        self.file.write(RUN_HEADER_STRUCT.pack(RUN_MAGIC_YBB, self.record_count))
        return self

    def write(self, record: YbbRunRecord) -> None:
        if self.file is None:
            raise RuntimeError("run writer is not open")
        packed_sfen, ply, moves_blob = record
        move_count, remainder = divmod(len(moves_blob), self.move_record_size)
        if remainder != 0:
            raise ValueError("moves blob size is broken")
        self.file.write(YBB_RUN_RECORD_STRUCT.pack(packed_sfen, ply, move_count))
        self.file.write(moves_blob)
        self.written += 1

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()
        if exc_type is None and self.written != self.record_count:
            raise RuntimeError(f"run record count mismatch: expected {self.record_count}, wrote {self.written}")


class YbbRunReader:
    def __init__(self, path: Path, move_record_size: int) -> None:
        self.path = path
        self.move_record_size = move_record_size
        self.file: BinaryIO | None = None
        self.remaining = 0

    def __enter__(self) -> "YbbRunReader":
        self.file = self.path.open("rb")
        header = self.file.read(RUN_HEADER_STRUCT.size)
        magic, count = RUN_HEADER_STRUCT.unpack(header)
        if magic != RUN_MAGIC_YBB:
            raise ValueError(f"invalid run magic: {self.path}")
        self.remaining = count
        return self

    def read_next(self) -> YbbRunRecord | None:
        if self.file is None:
            raise RuntimeError("run reader is not open")
        if self.remaining == 0:
            return None
        header = self.file.read(YBB_RUN_RECORD_STRUCT.size)
        if len(header) != YBB_RUN_RECORD_STRUCT.size:
            raise ValueError(f"broken run record: {self.path}")
        packed_sfen, ply, move_count = YBB_RUN_RECORD_STRUCT.unpack(header)
        moves_blob = self.file.read(move_count * self.move_record_size)
        if len(moves_blob) != move_count * self.move_record_size:
            raise ValueError(f"broken run move records: {self.path}")
        self.remaining -= 1
        return packed_sfen, ply, moves_blob

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()


class DbRunWriter:
    def __init__(self, path: Path, record_count: int) -> None:
        self.path = path
        self.record_count = record_count
        self.written = 0
        self.file: BinaryIO | None = None

    def __enter__(self) -> "DbRunWriter":
        self.file = self.path.open("wb")
        self.file.write(RUN_HEADER_STRUCT.pack(RUN_MAGIC_DB, self.record_count))
        return self

    def write(self, record: DbRunRecord) -> None:
        if self.file is None:
            raise RuntimeError("run writer is not open")
        key, block = record
        self.file.write(DB_RUN_RECORD_STRUCT.pack(len(key), len(block)))
        self.file.write(key)
        self.file.write(block)
        self.written += 1

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()
        if exc_type is None and self.written != self.record_count:
            raise RuntimeError(f"run record count mismatch: expected {self.record_count}, wrote {self.written}")


class DbRunReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: BinaryIO | None = None
        self.remaining = 0

    def __enter__(self) -> "DbRunReader":
        self.file = self.path.open("rb")
        header = self.file.read(RUN_HEADER_STRUCT.size)
        magic, count = RUN_HEADER_STRUCT.unpack(header)
        if magic != RUN_MAGIC_DB:
            raise ValueError(f"invalid run magic: {self.path}")
        self.remaining = count
        return self

    def read_next(self) -> DbRunRecord | None:
        if self.file is None:
            raise RuntimeError("run reader is not open")
        if self.remaining == 0:
            return None
        header = self.file.read(DB_RUN_RECORD_STRUCT.size)
        if len(header) != DB_RUN_RECORD_STRUCT.size:
            raise ValueError(f"broken run record: {self.path}")
        key_size, block_size = DB_RUN_RECORD_STRUCT.unpack(header)
        key = self.file.read(key_size)
        block = self.file.read(block_size)
        if len(key) != key_size or len(block) != block_size:
            raise ValueError(f"broken run payload: {self.path}")
        self.remaining -= 1
        return key, block

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()


def read_run_count(path: Path, magic_expected: bytes) -> int:
    with path.open("rb") as f:
        header = f.read(RUN_HEADER_STRUCT.size)
    if len(header) != RUN_HEADER_STRUCT.size:
        raise ValueError(f"broken run header: {path}")
    magic, count = RUN_HEADER_STRUCT.unpack(header)
    if magic != magic_expected:
        raise ValueError(f"invalid run magic: {path}")
    return int(count)


def iter_merged_ybb_records(readers: list[YbbRunReader]):
    heap: list[tuple[bytes, int, YbbRunRecord]] = []
    for index, reader in enumerate(readers):
        record = reader.read_next()
        if record is not None:
            heapq.heappush(heap, (record[0], index, record))
    previous_key: bytes | None = None
    while heap:
        packed_sfen, reader_index, record = heapq.heappop(heap)
        if previous_key == packed_sfen:
            raise ValueError(f"duplicated packed sfen: {packed_sfen.hex()}")
        previous_key = packed_sfen
        yield record
        next_record = readers[reader_index].read_next()
        if next_record is not None:
            heapq.heappush(heap, (next_record[0], reader_index, next_record))


def iter_merged_db_records(readers: list[DbRunReader]):
    heap: list[tuple[bytes, int, DbRunRecord]] = []
    for index, reader in enumerate(readers):
        record = reader.read_next()
        if record is not None:
            heapq.heappush(heap, (record[0], index, record))
    previous_key: bytes | None = None
    while heap:
        key, reader_index, record = heapq.heappop(heap)
        if previous_key == key:
            raise ValueError(f"duplicated sfen: {key.decode('utf-8', errors='replace')}")
        previous_key = key
        yield record
        next_record = readers[reader_index].read_next()
        if next_record is not None:
            heapq.heappush(heap, (next_record[0], reader_index, next_record))


def write_ybb_run(records: list[YbbRunRecord], path: Path, move_record_size: int) -> None:
    records.sort(key=lambda item: item[0])
    with YbbRunWriter(path, len(records), move_record_size) as writer:
        for record in records:
            writer.write(record)


def write_db_run(records: list[DbRunRecord], path: Path) -> None:
    records.sort(key=lambda item: item[0])
    with DbRunWriter(path, len(records)) as writer:
        for record in records:
            writer.write(record)


def reduce_ybb_runs(
    run_paths: list[Path],
    work_dir: Path,
    max_open_runs: int,
    move_record_size: int,
    progress_interval: int,
) -> list[Path]:
    stage = 0
    current = run_paths
    while len(current) > max_open_runs:
        next_runs: list[Path] = []
        for group_index, start in enumerate(range(0, len(current), max_open_runs)):
            group = current[start : start + max_open_runs]
            if len(group) == 1:
                next_runs.append(group[0])
                continue
            output_path = work_dir / f"merge-ybb-{stage:02d}-{group_index:06d}.run"
            total = sum(read_run_count(path, RUN_MAGIC_YBB) for path in group)
            with ExitStack() as stack:
                readers = [stack.enter_context(YbbRunReader(path, move_record_size)) for path in group]
                writer = stack.enter_context(YbbRunWriter(output_path, total, move_record_size))
                done = 0
                for record in iter_merged_ybb_records(readers):
                    writer.write(record)
                    done += 1
                    if should_report_progress(done, total, progress_interval):
                        print(f"merge run progress: {output_path} ({format_position_progress(done, total)})")
            next_runs.append(output_path)
            for path in group:
                path.unlink(missing_ok=True)
        current = next_runs
        stage += 1
    return current


def reduce_db_runs(
    run_paths: list[Path],
    work_dir: Path,
    max_open_runs: int,
    progress_interval: int,
) -> list[Path]:
    stage = 0
    current = run_paths
    while len(current) > max_open_runs:
        next_runs: list[Path] = []
        for group_index, start in enumerate(range(0, len(current), max_open_runs)):
            group = current[start : start + max_open_runs]
            if len(group) == 1:
                next_runs.append(group[0])
                continue
            output_path = work_dir / f"merge-db-{stage:02d}-{group_index:06d}.run"
            total = sum(read_run_count(path, RUN_MAGIC_DB) for path in group)
            with ExitStack() as stack:
                readers = [stack.enter_context(DbRunReader(path)) for path in group]
                writer = stack.enter_context(DbRunWriter(output_path, total))
                done = 0
                for record in iter_merged_db_records(readers):
                    writer.write(record)
                    done += 1
                    if should_report_progress(done, total, progress_interval):
                        print(f"merge run progress: {output_path} ({format_position_progress(done, total)})")
            next_runs.append(output_path)
            for path in group:
                path.unlink(missing_ok=True)
        current = next_runs
        stage += 1
    return current


def make_db_to_ybb_work_dir(tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="convert-db-to-ybb-", dir=tmp_dir))


def make_ybb_to_db_work_dir(tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="convert-ybb-to-db-", dir=tmp_dir))


def flush_ybb_chunk(
    records: list[YbbRunRecord],
    run_paths: list[Path],
    work_dir: Path,
    run_index: int,
    move_record_size: int,
    processed_positions: int,
    total_positions: int | None,
) -> int:
    if not records:
        return run_index
    run_path = work_dir / f"db-to-ybb-{run_index:06d}.run"
    print(f"write run: {run_path} (chunk={len(records):,} positions, {format_position_progress(processed_positions, total_positions)})")
    write_ybb_run(records, run_path, move_record_size)
    run_paths.append(run_path)
    records.clear()
    return run_index + 1


def write_final_ybb(
    run_paths: list[Path],
    output_base: Path,
    flags: int,
    move_record_size: int,
    progress_interval: int,
) -> None:
    output_path = ybb_path_from_output(output_base)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output_path.with_name(output_path.name + ".tmp")
    total = sum(read_run_count(path, RUN_MAGIC_YBB) for path in run_paths)
    index_size = YBB_HEADER_STRUCT.size + total * YBB_INDEX_STRUCT.size
    try:
        with output_tmp.open("w+b") as output_file:
            output_file.write(YBB_HEADER_STRUCT.pack(YBB_MAGIC, total, flags))
            index_offset = YBB_HEADER_STRUCT.size
            move_offset = 0
            done = 0
            if run_paths:
                with ExitStack() as stack:
                    readers = [stack.enter_context(YbbRunReader(path, move_record_size)) for path in run_paths]
                    for packed_sfen, ply, moves_blob in iter_merged_ybb_records(readers):
                        move_count = len(moves_blob) // move_record_size
                        output_file.seek(index_size + move_offset)
                        output_file.write(moves_blob)
                        output_file.seek(index_offset)
                        output_file.write(YBB_INDEX_STRUCT.pack(packed_sfen, move_offset, ply, move_count))
                        index_offset += YBB_INDEX_STRUCT.size
                        move_offset += len(moves_blob)
                        done += 1
                        if should_report_progress(done, total, progress_interval):
                            print(f"write ybb progress: {output_path} ({format_position_progress(done, total)})")
        os.replace(output_tmp, output_path)
    except Exception:
        output_tmp.unlink(missing_ok=True)
        raise


def convert_db_to_ybb(
    input_db: Path,
    output_base: Path,
    work_dir: Path,
    chunk_positions: int,
    chunk_bytes: int,
    max_open_runs: int,
    include_depth: bool,
) -> None:
    move_struct = YBB_MOVE_DEPTH_STRUCT if include_depth else YBB_MOVE_STRUCT
    move_record_size = move_struct.size
    flags = YBB_FLAG_MOVE_DEPTH if include_depth else 0
    run_paths: list[Path] = []
    chunk_records: list[YbbRunRecord] = []
    chunk_estimated_bytes = 0
    run_index = 0
    total_positions = 0
    input_positions = count_yaneuraou_db_positions(input_db)
    print(f"input positions: {input_positions:,}")

    current_packed_sfen: bytes | None = None
    current_ply = 1
    current_moves: list[bytes] = []
    current_sfen = ""

    def finish_current() -> None:
        nonlocal chunk_estimated_bytes, run_index, total_positions
        nonlocal current_packed_sfen, current_ply, current_moves, current_sfen
        if current_packed_sfen is None:
            return
        if current_moves:
            moves_blob = b"".join(current_moves)
            chunk_records.append((current_packed_sfen, current_ply, moves_blob))
            chunk_estimated_bytes += 32 + 2 + 2 + len(moves_blob)
            total_positions += 1
            if len(chunk_records) >= chunk_positions or chunk_estimated_bytes >= chunk_bytes:
                run_index = flush_ybb_chunk(
                    chunk_records,
                    run_paths,
                    work_dir,
                    run_index,
                    move_record_size,
                    total_positions,
                    input_positions,
                )
                chunk_estimated_bytes = 0
        current_packed_sfen = None
        current_ply = 1
        current_moves = []
        current_sfen = ""

    with input_db.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_number, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            if line.startswith("sfen "):
                finish_current()
                current_sfen, current_ply = trim_sfen_ply(line[5:])
                if current_ply < 0 or current_ply > 65535:
                    raise ValueError(f"line {line_number}: ply is out of uint16 range: {current_ply}")
                current_packed_sfen = pack_sfen_text(f"{current_sfen} {current_ply}")
                continue
            if current_packed_sfen is None:
                raise ValueError(f"line {line_number}: move line appears before sfen line: {line}")
            parsed = parse_move_line(line)
            if parsed is None:
                continue
            move_text, value, depth = parsed
            move16 = usi_to_move16(move_text)
            if include_depth:
                current_moves.append(move_struct.pack(move16, value, depth))
            else:
                current_moves.append(move_struct.pack(move16, value))
            if len(current_moves) > 65535:
                raise ValueError(f"line {line_number}: too many moves: {current_sfen}")

    finish_current()
    flush_ybb_chunk(chunk_records, run_paths, work_dir, run_index, move_record_size, total_positions, input_positions)
    print(f"read positions: {total_positions:,} (input={input_positions:,}, skipped={max(input_positions - total_positions, 0):,})")
    run_paths = reduce_ybb_runs(run_paths, work_dir, max_open_runs, move_record_size, chunk_positions)
    write_final_ybb(run_paths, output_base, flags, move_record_size, chunk_positions)


def flush_db_chunk(
    records: list[DbRunRecord],
    run_paths: list[Path],
    work_dir: Path,
    run_index: int,
    processed_positions: int,
    total_positions: int | None,
) -> int:
    if not records:
        return run_index
    run_path = work_dir / f"ybb-to-db-{run_index:06d}.run"
    print(f"write run: {run_path} (chunk={len(records):,} positions, {format_position_progress(processed_positions, total_positions)})")
    write_db_run(records, run_path)
    run_paths.append(run_path)
    records.clear()
    return run_index + 1


def ybb_record_to_db_block(packed_sfen: bytes, ply: int, moves_blob: bytes, flags: int) -> DbRunRecord:
    sfen = unpack_sfen_text(packed_sfen, ply)
    sfen_no_ply = trim_number(sfen)
    move_struct = YBB_MOVE_DEPTH_STRUCT if flags & YBB_FLAG_MOVE_DEPTH else YBB_MOVE_STRUCT
    moves: list[tuple[int, int, int]] = []
    for offset in range(0, len(moves_blob), move_struct.size):
        if flags & YBB_FLAG_MOVE_DEPTH:
            move16, value, depth = move_struct.unpack(moves_blob[offset : offset + move_struct.size])
        else:
            move16, value = move_struct.unpack(moves_blob[offset : offset + move_struct.size])
            depth = 0
        moves.append((move16, value, depth))
    moves.sort(key=lambda item: item[1], reverse=True)

    lines = [f"sfen {sfen_no_ply} {ply}\n"]
    for move16, value, depth in moves:
        lines.append(f"{move16_to_usi(move16)} none {value} {depth}\n")
    return sfen_no_ply.encode("utf-8"), "".join(lines).encode("utf-8")


def write_final_db(run_paths: list[Path], output_db: Path, progress_interval: int) -> None:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output_db.with_name(output_db.name + ".tmp")
    total = sum(read_run_count(path, RUN_MAGIC_DB) for path in run_paths)
    try:
        with output_tmp.open("wb") as output_file:
            output_file.write(f"{YANEURAOU_BOOK_HEADER_V1}\n".encode("ascii"))
            output_file.write(f"# NOE:{total}\n".encode("ascii"))
            if run_paths:
                with ExitStack() as stack:
                    readers = [stack.enter_context(DbRunReader(path)) for path in run_paths]
                    done = 0
                    for _, block in iter_merged_db_records(readers):
                        output_file.write(block)
                        done += 1
                        if should_report_progress(done, total, progress_interval):
                            print(f"write db progress: {output_db} ({format_position_progress(done, total)})")
        os.replace(output_tmp, output_db)
    except Exception:
        output_tmp.unlink(missing_ok=True)
        raise


def convert_ybb_to_db(
    input_base: Path,
    output_db: Path,
    work_dir: Path,
    chunk_positions: int,
    chunk_bytes: int,
    max_open_runs: int,
) -> None:
    input_ybb = resolve_ybb_input(input_base)
    record_count, flags = read_ybb_header(input_ybb)
    print(f"input positions: {record_count:,}")
    move_struct = YBB_MOVE_DEPTH_STRUCT if flags & YBB_FLAG_MOVE_DEPTH else YBB_MOVE_STRUCT
    moves_base = YBB_HEADER_STRUCT.size + record_count * YBB_INDEX_STRUCT.size
    file_size = input_ybb.stat().st_size
    if file_size < moves_base:
        raise ValueError(f"broken ybb index area: {input_ybb}")
    moves_file_size = file_size - moves_base
    run_paths: list[Path] = []
    chunk_records: list[DbRunRecord] = []
    chunk_estimated_bytes = 0
    run_index = 0
    previous_packed_sfen: bytes | None = None

    with input_ybb.open("rb") as index_file, input_ybb.open("rb") as moves_file:
        index_file.seek(YBB_HEADER_STRUCT.size)
        for index in range(record_count):
            header = index_file.read(YBB_INDEX_STRUCT.size)
            if len(header) != YBB_INDEX_STRUCT.size:
                raise ValueError(f"broken ybb index record: {input_ybb}")
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

            record = ybb_record_to_db_block(packed_sfen, ply, moves_blob, flags)
            chunk_records.append(record)
            chunk_estimated_bytes += len(record[0]) + len(record[1])
            if len(chunk_records) >= chunk_positions or chunk_estimated_bytes >= chunk_bytes:
                run_index = flush_db_chunk(chunk_records, run_paths, work_dir, run_index, index + 1, record_count)
                chunk_estimated_bytes = 0

    flush_db_chunk(chunk_records, run_paths, work_dir, run_index, record_count, record_count)
    run_paths = reduce_db_runs(run_paths, work_dir, max_open_runs, chunk_positions)
    write_final_db(run_paths, output_db, chunk_positions)
