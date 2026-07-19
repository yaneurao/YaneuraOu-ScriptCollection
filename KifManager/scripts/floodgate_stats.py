#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cshogi

from kif_extractor_common import GameRecord, iter_kifu_files, normalize_player_name, parse_games


KIFMANAGER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = KIFMANAGER_DIR / "downloaded-kif" / "floodgate-daily"
FLOODGATE_SUFFIXES = {".csa", ".csv"}


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
    black: RoleStats
    white: RoleStats
    scanned_files: int = 0
    parsed_games: int = 0
    matched_games: int = 0
    skipped_parse: int = 0


def player_matches(player_name: str, target_name: str, *, contains: bool) -> bool:
    normalized_player = normalize_player_name(player_name)
    normalized_target = normalize_player_name(target_name)
    if contains:
        return normalized_target in normalized_player
    return normalized_player == normalized_target


def add_result(stats: RoleStats, game: GameRecord) -> None:
    if game.draw:
        stats.draws += 1
    elif game.winner == cshogi.BLACK:
        stats.wins += 1
    elif game.winner == cshogi.WHITE:
        stats.losses += 1
    else:
        stats.unknown += 1


def add_reverse_result(stats: RoleStats, game: GameRecord) -> None:
    if game.draw:
        stats.draws += 1
    elif game.winner == cshogi.WHITE:
        stats.wins += 1
    elif game.winner == cshogi.BLACK:
        stats.losses += 1
    else:
        stats.unknown += 1


def collect_player_stats(input_dir: Path, player: str, *, contains: bool = False) -> PlayerStats:
    stats = PlayerStats(black=RoleStats(), white=RoleStats())
    paths = [
        path
        for path in iter_kifu_files(input_dir)
        if path.suffix.lower() in FLOODGATE_SUFFIXES
    ]

    for path in sorted(paths):
        stats.scanned_files += 1
        try:
            games = parse_games(path, allow_non_startpos=True)
        except Exception:
            stats.skipped_parse += 1
            continue

        for game in games:
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

    return stats


def format_role_stats(stats: RoleStats) -> str:
    text = f"{stats.wins}-{stats.draws}-{stats.losses}"
    if stats.unknown:
        text += f" unknown={stats.unknown}"
    return text


def print_player_stats(stats: PlayerStats) -> None:
    print(f"black : {format_role_stats(stats.black)}")
    print(f"white : {format_role_stats(stats.white)}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count black/white records for a player in floodgate CSA files."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"directory to scan recursively. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument("--player", required=True, help="player name to count")
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input_dir.is_dir():
        raise SystemExit(f"input folder not found: {args.input_dir}")

    stats = collect_player_stats(args.input_dir, args.player, contains=args.contains)
    print_player_stats(stats)
    if args.summary:
        print(f"scanned_files : {stats.scanned_files}")
        print(f"parsed_games  : {stats.parsed_games}")
        print(f"matched_games : {stats.matched_games}")
        print(f"skipped_parse : {stats.skipped_parse}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
