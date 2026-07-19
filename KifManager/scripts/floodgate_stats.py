#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
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
    stats = PlayerStats()
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


def get_player_entry(stats: AllPlayerStats, name: str) -> PlayerEntry:
    normalized_name = normalize_player_name(name)
    entry = stats.players.get(normalized_name)
    if entry is None:
        entry = PlayerEntry(name=name.strip())
        stats.players[normalized_name] = entry
    return entry


def collect_all_player_stats(input_dir: Path) -> AllPlayerStats:
    stats = AllPlayerStats()
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
            add_result(get_player_entry(stats, game.black).stats.black, game)
            add_reverse_result(get_player_entry(stats, game.white).stats.white, game)

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
    args = parser.parse_args(argv)
    if args.contains and not args.player:
        parser.error("--contains requires --player")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input_dir.is_dir():
        raise SystemExit(f"input folder not found: {args.input_dir}")

    if args.player:
        stats = collect_player_stats(args.input_dir, args.player, contains=args.contains)
        print_player_stats(stats)
    else:
        all_stats = collect_all_player_stats(args.input_dir)
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
