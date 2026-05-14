"""Streaming converters for teacher-data binary formats."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import cshogi
import numpy as np

from TeacherFormatLib import (
    ConvertStats,
    HCPE,
    HCPE_SIZE,
    HCPE3_HEADER,
    MOVE_INFO,
    MOVE_VISITS,
    PSV,
    PSV_SIZE,
    game_result_for_side_to_move,
    i16_from_u16,
    make_progress,
    read_exact,
    side_to_move_game_result_to_hcpe,
    u16,
    validate_fixed_record_file,
)
from YaneShogiLib import GameDataDecoder  # noqa: E402


def convert_pack_to_hcpe_file(
    input_path: Path,
    output: BinaryIO,
    *,
    batch_size: int = 65536,
    no_progress: bool = False,
) -> ConvertStats:
    del batch_size, no_progress

    stats = ConvertStats(files=1)
    with input_path.open("rb") as f:
        data = f.read()

    decoder = GameDataDecoder(bytearray(data))
    while not decoder.eof():
        sfen = decoder.get_sfen()
        stats.games += 1
        board = cshogi.Board(sfen)
        game_records: list[tuple[int, int]] = []
        game_result = 0

        while True:
            move = decoder.read_uint16()
            sq1 = move & 0x7F
            sq2 = (move >> 7) & 0x7F
            if sq1 == sq2:
                game_result = sq1
                decoder.read_uint8()
                break

            eval16 = decoder.read_int16()
            game_records.append((move, eval16))

        hcpe = np.zeros(1, dtype=HCPE)
        for move, eval16 in game_records:
            hcpe.fill(0)
            board.to_hcp(hcpe["hcp"])
            hcpe["eval"][0] = int(eval16)
            hcpe["bestMove16"][0] = i16_from_u16(move)
            hcpe["gameResult"][0] = int(game_result)
            hcpe.tofile(output)

            board.push_move16(move)
            stats.positions += 1

    return stats


def convert_hcpe_to_psv_file(
    input_path: Path,
    output: BinaryIO,
    *,
    batch_size: int = 65536,
    no_progress: bool = False,
) -> ConvertStats:
    total_records = validate_fixed_record_file(input_path, HCPE_SIZE, "HCPE")
    stats = ConvertStats(files=1, positions=0)
    board = cshogi.Board()
    chunk_size = HCPE_SIZE * batch_size
    progress = make_progress(input_path, no_progress=no_progress)

    try:
        with input_path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                if progress is not None:
                    progress.update(len(chunk))

                hcpes = np.frombuffer(chunk, dtype=HCPE)
                psvs = np.zeros(len(hcpes), dtype=PSV)
                for i, hcpe in enumerate(hcpes):
                    board.set_hcp(hcpe["hcp"])
                    if not board.is_ok():
                        raise ValueError(
                            f"{input_path}: invalid HCP at record {stats.positions + i}"
                        )

                    board.to_psfen(psvs["sfen"][i])
                    psvs["score"][i] = int(hcpe["eval"])
                    psvs["move"][i] = cshogi.move16_to_psv(u16(hcpe["bestMove16"]))
                    psvs["game_result"][i] = game_result_for_side_to_move(
                        int(hcpe["gameResult"]), board.turn
                    )

                psvs.tofile(output)
                stats.positions += len(hcpes)
    finally:
        if progress is not None:
            progress.close()

    if stats.positions != total_records:
        raise RuntimeError(
            f"{input_path}: converted {stats.positions} records, expected {total_records}"
        )
    return stats


def convert_psv_to_hcpe_file(
    input_path: Path,
    output: BinaryIO,
    *,
    batch_size: int = 65536,
    no_progress: bool = False,
) -> ConvertStats:
    total_records = validate_fixed_record_file(input_path, PSV_SIZE, "PSV")
    stats = ConvertStats(files=1, positions=0)
    board = cshogi.Board()
    chunk_size = PSV_SIZE * batch_size
    progress = make_progress(input_path, no_progress=no_progress)

    try:
        with input_path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                if progress is not None:
                    progress.update(len(chunk))

                psvs = np.frombuffer(chunk, dtype=PSV)
                hcpes = np.zeros(len(psvs), dtype=HCPE)
                for i, psv in enumerate(psvs):
                    board.set_psfen(psv["sfen"])
                    if not board.is_ok():
                        raise ValueError(
                            f"{input_path}: invalid packed SFEN at record "
                            f"{stats.positions + i}"
                        )

                    board.to_hcp(hcpes["hcp"][i])
                    hcpes["eval"][i] = int(psv["score"])
                    hcpes["bestMove16"][i] = i16_from_u16(
                        cshogi.move16_from_psv(int(psv["move"]))
                    )
                    hcpes["gameResult"][i] = side_to_move_game_result_to_hcpe(
                        int(psv["game_result"]), board.turn
                    )

                hcpes.tofile(output)
                stats.positions += len(psvs)
    finally:
        if progress is not None:
            progress.close()

    if stats.positions != total_records:
        raise RuntimeError(
            f"{input_path}: converted {stats.positions} records, expected {total_records}"
        )
    return stats


def convert_hcpe3_to_hcpe_file(
    input_path: Path,
    output: BinaryIO,
    *,
    batch_size: int = 65536,
    no_progress: bool = False,
) -> ConvertStats:
    del batch_size

    stats = ConvertStats(files=1)
    board = cshogi.Board()
    hcpe = np.zeros(1, dtype=HCPE)
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
                result = int(header["result"]) & 0x3

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
                    selected_move16 = u16(move_info["selectedMove16"])

                    hcpe.fill(0)
                    board.to_hcp(hcpe["hcp"])
                    hcpe["eval"][0] = int(move_info["eval"])
                    hcpe["bestMove16"][0] = i16_from_u16(selected_move16)
                    hcpe["gameResult"][0] = result
                    hcpe.tofile(output)
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


def convert_hcpe3_to_psv_file(
    input_path: Path,
    output: BinaryIO,
    *,
    batch_size: int = 65536,
    no_progress: bool = False,
) -> ConvertStats:
    del batch_size

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
                    selected_move16 = u16(move_info["selectedMove16"])

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
