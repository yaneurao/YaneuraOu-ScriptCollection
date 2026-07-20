#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Sequence

import cshogi

from kif_extractor_common import (
    CSA_BLACK_NAME_RE,
    CSA_MOVE_LINE_RE,
    CSA_WHITE_NAME_RE,
    ParseError,
    decode_lines,
    iter_kifu_files,
    normalize_player_name,
)


KIFMANAGER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = KIFMANAGER_DIR / "downloaded-kif" / "floodgate-daily"
FLOODGATE_SUFFIXES = {".csa", ".csv"}
DEFAULT_PROGRESS_INTERVAL = 1000
DRAW_RESULTS = {"%SENNICHITE", "%JISHOGI", "%HIKIWAKE"}
WIN_BY_OPPONENT_OF_SIDE_TO_MOVE_RESULTS = {"%TORYO", "%TIME_UP", "%ILLEGAL_MOVE"}
WIN_BY_SIDE_TO_MOVE_RESULTS = {"%KACHI"}
UNKNOWN_RESULTS = {"%CHUDAN", "%MATTA"}


@dataclass
class RoleStats:
    wins: int = 0
    draws: int = 0
    losses: int = 0
    unknown: int = 0

    def total(self) -> int:
        return self.wins + self.draws + self.losses + self.unknown


@dataclass
class PlayerStats:
    black: RoleStats = field(default_factory=RoleStats)
    white: RoleStats = field(default_factory=RoleStats)
    scanned_files: int = 0
    parsed_games: int = 0
    matched_games: int = 0
    skipped_parse: int = 0


@dataclass
class PlayerEntry:
    name: str
    stats: PlayerStats = field(default_factory=PlayerStats)


@dataclass
class AllPlayerStats:
    players: dict[str, PlayerEntry] = field(default_factory=dict)
    scanned_files: int = 0
    parsed_games: int = 0
    skipped_parse: int = 0


@dataclass(frozen=True)
class CsaGameSummary:
    black: str
    white: str
    winner: int | None
    draw: bool = False


def log_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def progress_enabled(progress_interval: int) -> bool:
    return progress_interval > 0


def collect_floodgate_files(input_dir: Path, *, progress_interval: int) -> list[Path]:
    files: list[Path] = []
    if progress_enabled(progress_interval):
        log_progress(f"棋譜列挙開始: {input_dir}")

    for path in iter_kifu_files(input_dir):
        if path.suffix.lower() not in FLOODGATE_SUFFIXES:
            continue
        files.append(path)
        if progress_enabled(progress_interval) and len(files) % progress_interval == 0:
            log_progress(f"棋譜列挙中: files={len(files)}")

    files.sort()
    if progress_enabled(progress_interval):
        log_progress(f"棋譜列挙完了: files={len(files)}")
    return files


def player_matches(player_name: str, target_name: str, *, contains: bool) -> bool:
    normalized_player = normalize_player_name(player_name)
    normalized_target = normalize_player_name(target_name)
    if contains:
        return normalized_target in normalized_player
    return normalized_player == normalized_target


def add_result(stats: RoleStats, game: CsaGameSummary) -> None:
    if game.draw:
        stats.draws += 1
    elif game.winner == cshogi.BLACK:
        stats.wins += 1
    elif game.winner == cshogi.WHITE:
        stats.losses += 1
    else:
        stats.unknown += 1


def add_reverse_result(stats: RoleStats, game: CsaGameSummary) -> None:
    if game.draw:
        stats.draws += 1
    elif game.winner == cshogi.WHITE:
        stats.wins += 1
    elif game.winner == cshogi.BLACK:
        stats.losses += 1
    else:
        stats.unknown += 1


def result_token(line: str) -> str | None:
    token = line.split(",", 1)[0].strip()
    if token.startswith("%"):
        return token
    return None


def opponent(side: int | None) -> int | None:
    if side is None:
        return None
    return side ^ 1


def game_result_from_token(token: str, side_to_move: int | None) -> tuple[int | None, bool]:
    if token in DRAW_RESULTS:
        return None, True
    if token in WIN_BY_OPPONENT_OF_SIDE_TO_MOVE_RESULTS:
        return opponent(side_to_move), False
    if token in WIN_BY_SIDE_TO_MOVE_RESULTS:
        return side_to_move, False
    if token in UNKNOWN_RESULTS:
        return None, False
    raise ParseError(f"unsupported result: {token}")


def parse_csa_game_summary(path: Path) -> CsaGameSummary:
    black = ""
    white = ""
    side_to_move: int | None = cshogi.BLACK
    winner: int | None = None
    draw = False
    saw_result = False

    with path.open("rb") as file:
        for raw_line in decode_lines(file):
            line = raw_line.strip()
            if not line:
                continue

            if match := CSA_BLACK_NAME_RE.match(line):
                black = match.group(1).strip()
                continue
            if match := CSA_WHITE_NAME_RE.match(line):
                white = match.group(1).strip()
                continue
            if line == "+":
                side_to_move = cshogi.BLACK
                continue
            if line == "-":
                side_to_move = cshogi.WHITE
                continue
            if match := CSA_MOVE_LINE_RE.match(line):
                moved_side = cshogi.BLACK if match.group(1) == "+" else cshogi.WHITE
                side_to_move = opponent(moved_side)
                continue
            if token := result_token(line):
                winner, draw = game_result_from_token(token, side_to_move)
                saw_result = True
                break

    if not black or not white:
        raise ParseError(f"missing player name: {path}")
    if not saw_result:
        raise ParseError(f"missing game result: {path}")
    return CsaGameSummary(black=black, white=white, winner=winner, draw=draw)


def log_player_progress(index: int, total: int, stats: PlayerStats, progress_interval: int) -> None:
    if not progress_enabled(progress_interval):
        return
    if index != total and index % progress_interval != 0:
        return
    log_progress(
        f"解析中: {index}/{total} parsed={stats.parsed_games} "
        f"matched={stats.matched_games} skipped_parse={stats.skipped_parse}"
    )


def log_all_players_progress(index: int, total: int, stats: AllPlayerStats, progress_interval: int) -> None:
    if not progress_enabled(progress_interval):
        return
    if index != total and index % progress_interval != 0:
        return
    log_progress(
        f"解析中: {index}/{total} parsed={stats.parsed_games} "
        f"players={len(stats.players)} skipped_parse={stats.skipped_parse}"
    )


def collect_player_stats(
    input_dir: Path,
    player: str,
    *,
    contains: bool = False,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
) -> PlayerStats:
    stats = PlayerStats()
    paths = collect_floodgate_files(input_dir, progress_interval=progress_interval)

    for index, path in enumerate(paths, start=1):
        stats.scanned_files += 1
        try:
            game = parse_csa_game_summary(path)
        except Exception:
            stats.skipped_parse += 1
            log_player_progress(index, len(paths), stats, progress_interval)
            continue

        stats.parsed_games += 1
        matched = False
        if player_matches(game.black, player, contains=contains):
            add_result(stats.black, game)
            matched = True
        if player_matches(game.white, player, contains=contains):
            add_reverse_result(stats.white, game)
            matched = True
        if matched:
            stats.matched_games += 1
        log_player_progress(index, len(paths), stats, progress_interval)

    return stats


def get_player_entry(stats: AllPlayerStats, name: str) -> PlayerEntry:
    normalized_name = normalize_player_name(name)
    entry = stats.players.get(normalized_name)
    if entry is None:
        entry = PlayerEntry(name=name.strip())
        stats.players[normalized_name] = entry
    return entry


def collect_all_player_stats(
    input_dir: Path,
    *,
    progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
) -> AllPlayerStats:
    stats = AllPlayerStats()
    paths = collect_floodgate_files(input_dir, progress_interval=progress_interval)

    for index, path in enumerate(paths, start=1):
        stats.scanned_files += 1
        try:
            game = parse_csa_game_summary(path)
        except Exception:
            stats.skipped_parse += 1
            log_all_players_progress(index, len(paths), stats, progress_interval)
            continue

        stats.parsed_games += 1
        add_result(get_player_entry(stats, game.black).stats.black, game)
        add_reverse_result(get_player_entry(stats, game.white).stats.white, game)
        log_all_players_progress(index, len(paths), stats, progress_interval)

    return stats


def format_role_stats(stats: RoleStats) -> str:
    text = f"{stats.wins}-{stats.draws}-{stats.losses}"
    if stats.unknown:
        text += f" unknown={stats.unknown}"
    return text


def print_player_stats(stats: PlayerStats) -> None:
    print(f"black : {format_role_stats(stats.black)}")
    print(f"white : {format_role_stats(stats.white)}")


def sorted_player_entries(stats: AllPlayerStats) -> list[PlayerEntry]:
    return sorted(stats.players.values(), key=lambda entry: normalize_player_name(entry.name))


def print_all_player_stats(stats: AllPlayerStats) -> None:
    for entry in sorted_player_entries(stats):
        print(entry.name)
        print(f"  black {format_role_stats(entry.stats.black)}")
        print(f"  white {format_role_stats(entry.stats.white)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count black/white records in floodgate CSA files."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"directory to scan recursively. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--player",
        default=None,
        help="player name to count. If omitted, all players are counted.",
    )
    parser.add_argument(
        "--contains",
        action="store_true",
        help="match --player as a case-insensitive substring instead of an exact name",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="also print scanned/parsed/matched file counts",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=DEFAULT_PROGRESS_INTERVAL,
        metavar="N",
        help=f"print progress to stderr every N files. Default: {DEFAULT_PROGRESS_INTERVAL}",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="do not print progress to stderr",
    )
    args = parser.parse_args(argv)
    if args.contains and not args.player:
        parser.error("--contains requires --player")
    if args.progress_interval < 0:
        parser.error("--progress-interval must be non-negative")
    if args.no_progress:
        args.progress_interval = 0
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input_dir.is_dir():
        raise SystemExit(f"input folder not found: {args.input_dir}")

    if args.player:
        stats = collect_player_stats(
            args.input_dir,
            args.player,
            contains=args.contains,
            progress_interval=args.progress_interval,
        )
        print_player_stats(stats)
    else:
        all_stats = collect_all_player_stats(args.input_dir, progress_interval=args.progress_interval)
        print_all_player_stats(all_stats)
        stats = all_stats

    if args.summary:
        print(f"scanned_files : {stats.scanned_files}")
        print(f"parsed_games  : {stats.parsed_games}")
        if args.player:
            print(f"matched_games : {stats.matched_games}")
        else:
            print(f"players       : {len(stats.players)}")
        print(f"skipped_parse : {stats.skipped_parse}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
