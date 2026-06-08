#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import gc
import os
import re
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import cshogi
from cshogi import CSA, KIF


SUPPORTED_SUFFIXES = {".csa", ".csv", ".kif", ".kifu"}
ARCHIVE_SUFFIXES = {".zip", ".7z"}
CSA_MOVE_RE = re.compile(r"^[+-]\d{4}[A-Z]{2}$")
CSA_MOVE_LINE_RE = re.compile(r"^([+-])\d{4}[A-Z]{2}(?:$|[,\s])")
KIF_MOVE_LINE_RE = re.compile(r"^\s*(\d+)\s")
SEPARATED_DATE_RE = re.compile(r"(20\d{2})[-_/](\d{1,2})[-_/](\d{1,2})")
COMPACT_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?:\d{6})?(?!\d)")
PROGRESS_INTERVAL = 1000


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class EvalRecord:
    side: int
    value: int


def log_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class CountProgress:
    def __init__(self, label: str, total: int, *, interval: int = PROGRESS_INTERVAL) -> None:
        self.label = label
        self.total = total
        self.interval = interval
        self.last_reported = 0

    def update(self, count: int, *, force: bool = False) -> None:
        if self.total <= 0:
            return
        if force and self.last_reported == count:
            return
        if force or count == self.total or count - self.last_reported >= self.interval:
            log_progress(f"{self.label} {count}/{self.total}")
            self.last_reported = count


@dataclass
class GameRecord:
    path: Path
    black: str
    white: str
    moves: list[str]
    black_rating: float | None = None
    white_rating: float | None = None
    eval_records: list[EvalRecord] = field(default_factory=list)
    game_date: date | None = None


@dataclass
class CsaHeader:
    black: str = ""
    white: str = ""
    black_rating: float | None = None
    white_rating: float | None = None
    game_date: date | None = None


@dataclass
class Stats:
    scanned: int = 0
    selected: int = 0
    skipped_year: int = 0
    skipped_date: int = 0
    skipped_finalist: int = 0
    skipped_name: int = 0
    skipped_rating: int = 0
    skipped_reversal: int = 0
    skipped_parse: int = 0
    skipped_duplicate: int = 0


@dataclass(frozen=True)
class PlayerFilters:
    both_patterns: Sequence[re.Pattern[str]]
    either_patterns: Sequence[re.Pattern[str]]


@dataclass(frozen=True)
class YearFilter:
    source_kind: str
    start_year: int | None = None
    end_year: int | None = None


@dataclass(frozen=True)
class DateFilter:
    source_kind: str
    start_date: date | None = None
    end_date: date | None = None


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="replace")


def iter_kifu_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            yield path


def iter_archive_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in ARCHIVE_SUFFIXES:
            yield path


@contextlib.contextmanager
def extracted_archive_roots(
    input_dir: Path,
    *,
    year_filter: YearFilter | None,
    verbose: bool,
) -> Iterable[list[Path]]:
    archives = [
        archive
        for archive in iter_archive_files(input_dir)
        if year_filter is None or year_filter_passes(archive, year_filter)
    ]
    if not archives:
        yield []
        return

    tmp_root = Path.cwd() / "tmp"
    work_dir = tmp_root / f"kif-extractor-archives-{os.getpid()}"

    if work_dir.exists():
        remove_tree_with_retries(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    roots: list[Path] = []
    try:
        for index, archive in enumerate(archives):
            destination = work_dir / f"{index:04d}_{archive.stem}"
            destination.mkdir(parents=True, exist_ok=True)
            log_progress(f"解凍開始 {index + 1}/{len(archives)}: {archive}")
            extract_archive(archive, destination)
            log_progress(f"解凍完了 {index + 1}/{len(archives)}: {archive}")
            roots.append(destination)
            if verbose:
                print(f"extract archive: {archive} -> {destination}", file=sys.stderr)

        yield roots
    finally:
        remove_tree_with_retries(work_dir)
        remove_empty_directory(tmp_root)


def remove_tree_with_retries(path: Path, *, attempts: int = 5, delay_seconds: float = 0.2) -> bool:
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            gc.collect()
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    return False


def remove_empty_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.rmdir()


def extract_archive(archive: Path, destination: Path) -> None:
    suffix = archive.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            safe_extract_zip(z, destination)
        return

    if suffix == ".7z":
        extract_7z_with_py7zr(archive, destination)
        return

    raise RuntimeError(f"unsupported archive suffix: {archive.suffix}")


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    members = [member for member in archive.infolist() if not member.is_dir()]
    validate_archive_member_paths((member.filename for member in members), destination, "zip")
    progress = CountProgress("解凍中", len(members))
    for index, member in enumerate(members, 1):
        target = destination / member.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        progress.update(index)
    progress.update(len(members), force=True)


def extract_7z_with_py7zr(archive: Path, destination: Path) -> None:
    try:
        import py7zr
        from py7zr.callbacks import ExtractCallback
    except ImportError as exc:
        raise RuntimeError(
            ".7zを展開するには py7zr が必要です。"
            "KifManager/README.md の「必要なもの」を確認してください。"
        ) from exc

    try:
        with py7zr.SevenZipFile(archive, mode="r") as z:
            member_names = z.getnames()
            file_names = [info.filename for info in z.list() if getattr(info, "is_file", False)]
            validate_archive_member_paths(member_names, destination, "7z")
            z.extractall(path=destination, callback=make_7z_progress_callback(ExtractCallback, file_names))
    except Exception as exc:
        raise RuntimeError(f".7zの展開に失敗しました: {archive}: {exc}") from exc


def make_7z_progress_callback(extract_callback_type: type, file_names: Sequence[str]) -> object:
    target_files = set(file_names)

    class SevenZipExtractProgress(extract_callback_type):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.count = 0
            self.progress = CountProgress("解凍中", len(target_files))

        def report_start_preparation(self) -> None:
            pass

        def report_start(self, processing_file_path: str, processing_bytes: str) -> None:
            if processing_file_path not in target_files:
                return
            self.count += 1
            self.progress.update(self.count)

        def report_update(self, decompressed_bytes: str) -> None:
            pass

        def report_end(self, processing_file_path: str, wrote_bytes: str) -> None:
            pass

        def report_warning(self, message: str) -> None:
            log_progress(f"warning: {message}")

        def report_postprocess(self) -> None:
            self.progress.update(self.count, force=True)

    return SevenZipExtractProgress()


def validate_archive_member_paths(member_names: Iterable[str], destination: Path, archive_kind: str) -> None:
    destination_root = destination.resolve()
    for member_name in member_names:
        target = (destination / member_name).resolve()
        if destination_root != target and destination_root not in target.parents:
            raise RuntimeError(f"unsafe {archive_kind} member path: {member_name}")


def load_player_patterns(path: Path | None) -> list[re.Pattern[str]]:
    if path is None:
        return []

    text = read_text(path)
    patterns: list[re.Pattern[str]] = []
    for line_no, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line.lower()))
        except re.error as exc:
            raise SystemExit(f"invalid regex at {path}:{line_no}: {line!r}: {exc}") from exc
    return patterns


def player_matches(name: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    lower_name = name.lower()
    return any(pattern.search(lower_name) for pattern in patterns)


def player_filters_pass(black: str, white: str, player_filters: PlayerFilters) -> bool:
    conditions: list[bool] = []

    if player_filters.both_patterns:
        conditions.append(
            player_matches(black, player_filters.both_patterns)
            and player_matches(white, player_filters.both_patterns)
        )

    if player_filters.either_patterns:
        conditions.append(
            player_matches(black, player_filters.either_patterns)
            or player_matches(white, player_filters.either_patterns)
        )

    return not conditions or any(conditions)


def make_year_filter(
    source_kind: str | None,
    start_year: int | None,
    end_year: int | None,
) -> YearFilter | None:
    if start_year is None and end_year is None:
        return None
    if source_kind is None:
        return None
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError("開始年は終了年以下を指定してください。")
    return YearFilter(source_kind, start_year, end_year)


def make_date_filter(
    source_kind: str | None,
    start_date: date | str | None,
    end_date: date | str | None,
) -> DateFilter | None:
    parsed_start = parse_date_value(start_date, "開始日")
    parsed_end = parse_date_value(end_date, "終了日")
    if parsed_start is None and parsed_end is None:
        return None
    if source_kind != "floodgate":
        return None
    if parsed_start is not None and parsed_end is not None and parsed_start > parsed_end:
        raise ValueError("開始日は終了日以下を指定してください。")
    return DateFilter(source_kind, parsed_start, parsed_end)


def parse_date_value(value: date | str | None, label: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value

    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.replace("/", "-"))
    except ValueError as exc:
        raise ValueError(f"{label} は YYYY-MM-DD または YYYY/MM/DD 形式で指定してください: {value}") from exc


def make_effective_year_filter(
    source_kind: str | None,
    start_year: int | None,
    end_year: int | None,
    date_filter: DateFilter | None,
) -> YearFilter | None:
    if date_filter is None or source_kind != "floodgate":
        return make_year_filter(source_kind, start_year, end_year)

    if date_filter.start_date is not None:
        date_start_year = date_filter.start_date.year
        start_year = date_start_year if start_year is None else max(start_year, date_start_year)
    if date_filter.end_date is not None:
        date_end_year = date_filter.end_date.year
        end_year = date_end_year if end_year is None else min(end_year, date_end_year)

    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError("年条件と日付条件が矛盾しています。")
    return make_year_filter(source_kind, start_year, end_year)


def year_filter_passes(path: Path, year_filter: YearFilter) -> bool:
    year = infer_year_from_path(path, year_filter.source_kind)
    if year is None:
        return False
    if year_filter.start_year is not None and year < year_filter.start_year:
        return False
    if year_filter.end_year is not None and year > year_filter.end_year:
        return False
    return True


def date_filter_passes(game_date: date | None, date_filter: DateFilter | None) -> bool:
    if date_filter is None:
        return True
    if game_date is None:
        return False
    if date_filter.start_date is not None and game_date < date_filter.start_date:
        return False
    if date_filter.end_date is not None and game_date > date_filter.end_date:
        return False
    return True


def infer_year_from_path(path: Path, source_kind: str) -> int | None:
    parts = [part.lower() for part in path.parts]
    text = "/".join(parts)
    if source_kind == "floodgate":
        match = re.search(r"wdoor(20\d{2})", text)
        if match:
            return int(match.group(1))
        game_date = infer_game_date_from_path(path)
        return game_date.year if game_date is not None else None

    if source_kind == "wcsc":
        if any(part == "wcso1" or part.startswith("wcso1_") for part in parts):
            return 2020
        for part in parts:
            match = re.search(r"wcsc(\d+)", part)
            if match:
                return wcsc_year_from_number(int(match.group(1)))

    return None


def infer_game_date_from_path(path: Path) -> date | None:
    file_date = find_date_in_text(path.name)
    if file_date is not None:
        return file_date
    return find_date_in_text("/".join(path.parts))


def find_date_in_text(text: str) -> date | None:
    for match in SEPARATED_DATE_RE.finditer(text):
        parsed = make_date_from_parts(match.group(1), match.group(2), match.group(3))
        if parsed is not None:
            return parsed

    for match in COMPACT_DATE_RE.finditer(text):
        parsed = make_date_from_parts(match.group(1), match.group(2), match.group(3))
        if parsed is not None:
            return parsed

    return None


def make_date_from_parts(year_text: str, month_text: str, day_text: str) -> date | None:
    try:
        return date(int(year_text), int(month_text), int(day_text))
    except ValueError:
        return None


def infer_wcsc_event_key(path: Path) -> str | None:
    for part in (part.lower() for part in path.parts):
        if part == "wcso1" or part.startswith("wcso1_"):
            return "wcso1"
        match = re.search(r"wcsc(\d+)", part)
        if match:
            return f"wcsc{int(match.group(1))}"
    return None


def infer_wcsc_stage(path: Path) -> str | None:
    for part in (part.lower() for part in path.parts):
        if part in {"final", "finals"}:
            return "F"
        if part in {"upper", "upper_division", "upper-division"}:
            return "U"
        if part in {"lower", "lower_division", "lower-division"}:
            return "L"

    text = "/".join(path.with_suffix("").parts).upper()
    match = re.search(r"(?:^|[+_/\-])([FUL])\d+(?:$|[+_/\-.])", text)
    return match.group(1) if match else None


def infer_denryu_event_key(path: Path) -> str | None:
    for part in (part.lower() for part in path.parts):
        match = re.fullmatch(r"dr(\d+)_production", part)
        if match:
            return f"dr{int(match.group(1))}_production"

    text = "/".join(part.lower() for part in path.parts)
    match = re.search(r"dr(\d+)(?:prd|prod)\+", text)
    if match:
        return f"dr{int(match.group(1))}_production"
    return None


def infer_denryu_stage(path: Path) -> str | None:
    text = "/".join(path.with_suffix("").parts).lower()

    if re.search(r"(?:^|[+_/\-])sr2pa(?:$|[+_/\-.])", text):
        return "F"
    if re.search(r"(?:^|[+_/\-])dr[23]prda(?:$|[+_/\-.\d])", text):
        return "F"
    if re.search(r"(?:^|[+_/\-])dr4a(?:$|[+_/\-.\d])", text):
        return "F"
    if re.search(r"(?:^|[+_/\-])dr5prda0(?:$|[+_/\-.\d])", text):
        return "F"
    if re.search(r"(?:^|[+_/\-])dr\d+prdf\d*(?:$|[+_/\-.\d])", text):
        return "F"
    if denryu_header_contains_final(path):
        return "F"
    return None


def denryu_header_contains_final(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            data = file.read(4096)
    except OSError:
        return False

    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return "決勝" in data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return "決勝" in data.decode("utf-8", errors="replace")


def collect_wcsc_finalist_names(
    input_roots: Sequence[Path],
    year_filter: YearFilter | None,
    *,
    verbose: bool,
) -> dict[str, set[str]]:
    finalists_by_event: dict[str, set[str]] = {}
    for input_root in input_roots:
        for path in iter_kifu_files(input_root):
            if year_filter is not None and not year_filter_passes(path, year_filter):
                continue
            if infer_wcsc_stage(path) != "F":
                continue
            event_key = infer_wcsc_event_key(path)
            if event_key is None:
                continue

            try:
                parsed_games = parse_games(path)
            except Exception as exc:
                if verbose:
                    print(f"skip finalist parse: {path}: {exc}", file=sys.stderr)
                continue

            finalists = finalists_by_event.setdefault(event_key, set())
            for game in parsed_games:
                finalists.add(normalize_player_name(game.black))
                finalists.add(normalize_player_name(game.white))

    return finalists_by_event


def collect_denryu_finalist_names(
    input_roots: Sequence[Path],
    year_filter: YearFilter | None,
    *,
    verbose: bool,
) -> dict[str, set[str]]:
    finalists_by_event: dict[str, set[str]] = {}
    for input_root in input_roots:
        for path in iter_kifu_files(input_root):
            if year_filter is not None and not year_filter_passes(path, year_filter):
                continue
            if infer_denryu_stage(path) != "F":
                continue
            event_key = infer_denryu_event_key(path)
            if event_key is None:
                continue

            try:
                parsed_games = parse_games(path)
            except Exception as exc:
                if verbose:
                    print(f"skip finalist parse: {path}: {exc}", file=sys.stderr)
                continue

            finalists = finalists_by_event.setdefault(event_key, set())
            for game in parsed_games:
                finalists.add(normalize_player_name(game.black))
                finalists.add(normalize_player_name(game.white))

    return finalists_by_event


def normalize_player_name(name: str) -> str:
    return name.strip().lower()


def wcsc_finalist_filter_passes(game: GameRecord, finalists_by_event: dict[str, set[str]]) -> bool:
    event_key = infer_wcsc_event_key(game.path)
    if event_key is None:
        return False
    finalists = finalists_by_event.get(event_key)
    if not finalists:
        return False

    black = normalize_player_name(game.black)
    white = normalize_player_name(game.white)
    return any(finalist and (finalist in black or finalist in white) for finalist in finalists)


def denryu_finalist_filter_passes(game: GameRecord, finalists_by_event: dict[str, set[str]]) -> bool:
    event_key = infer_denryu_event_key(game.path)
    if event_key is None:
        return False
    finalists = finalists_by_event.get(event_key)
    if not finalists:
        return False

    black = normalize_player_name(game.black)
    white = normalize_player_name(game.white)
    return any(finalist and (finalist in black or finalist in white) for finalist in finalists)


def wcsc_year_from_number(number: int) -> int | None:
    if number < 1:
        return None
    if number <= 5:
        return 1989 + number
    return 1990 + number


def rating_passes(game: GameRecord, min_rating: float | None) -> bool:
    if min_rating is None:
        return True
    if game.black_rating is None or game.white_rating is None:
        return False
    return game.black_rating >= min_rating and game.white_rating >= min_rating


def reversal_passes(game: GameRecord, threshold: int | None) -> bool:
    if threshold is None:
        return True

    seen_positive = {cshogi.BLACK: False, cshogi.WHITE: False}
    seen_negative = {cshogi.BLACK: False, cshogi.WHITE: False}

    for record in game.eval_records:
        if seen_positive[record.side] and record.value < 0:
            return True
        if seen_negative[record.side] and record.value > 0:
            return True

        if record.value >= threshold:
            seen_positive[record.side] = True
        elif record.value <= -threshold:
            seen_negative[record.side] = True

    return False


def read_csa_header(path: Path) -> CsaHeader:
    with path.open("rb") as f:
        return scan_csa_header_lines(decode_lines(f))


def decode_lines(lines: Iterable[bytes]) -> Iterable[str]:
    for raw_line in lines:
        for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
            try:
                yield raw_line.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            yield raw_line.decode("utf-8", errors="replace")


def scan_csa_header_lines(lines: Iterable[str]) -> CsaHeader:
    header = CsaHeader()
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("N+"):
            header.black = line[2:].strip()
            continue
        if line.startswith("N-"):
            header.white = line[2:].strip()
            continue
        if line.startswith("'black_rate:"):
            header.black_rating = optional_float(line.rsplit(":", 1)[-1])
            continue
        if line.startswith("'white_rate:"):
            header.white_rating = optional_float(line.rsplit(":", 1)[-1])
            continue
        if header.game_date is None:
            header.game_date = find_date_in_text(line)
        if line.startswith("%") or CSA_MOVE_RE.match(line):
            break
    return header


def should_skip_by_csa_header(
    path: Path,
    player_filters: PlayerFilters,
    min_rating: float | None,
    date_filter: DateFilter | None,
    stats: Stats,
) -> bool:
    if path.suffix.lower() not in {".csa", ".csv"}:
        return False
    if (
        not player_filters.both_patterns
        and not player_filters.either_patterns
        and min_rating is None
        and date_filter is None
    ):
        return False

    header = read_csa_header(path)
    if date_filter is not None:
        game_date = header.game_date or infer_game_date_from_path(path)
        if not date_filter_passes(game_date, date_filter):
            stats.skipped_date += 1
            return True

    if header.black and header.white and not player_filters_pass(header.black, header.white, player_filters):
        stats.skipped_name += 1
        return True

    if min_rating is not None:
        pseudo_game = GameRecord(
            path,
            header.black,
            header.white,
            [],
            header.black_rating,
            header.white_rating,
        )
        if not rating_passes(pseudo_game, min_rating):
            stats.skipped_rating += 1
            return True

    return False


def parse_eval_from_comment(line: str) -> int | None:
    if "**" not in line and "評価値" not in line and "score cp" not in line:
        return None

    for pattern in (
        r"評価値\s*([-+]?\d+)",
        r"score\s+cp\s+([-+]?\d+)",
        r"\*\*\s*([-+]?\d+)",
    ):
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            return int(match.group(1))

    if "**" in line:
        tail = line.split("**", 1)[1]
    elif "評価値" in line:
        tail = line.split("評価値", 1)[1]
    else:
        tail = line

    for token in re.split(r"[\s,]+", tail):
        token = token.strip()
        if re.fullmatch(r"[-+]?\d+", token):
            return int(token)
    return None


def side_from_csa_move_line(line: str) -> int | None:
    match = CSA_MOVE_LINE_RE.match(line)
    if not match:
        return None
    return cshogi.BLACK if match.group(1) == "+" else cshogi.WHITE


def extract_csa_eval_records(path: Path) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    pending_eval: int | None = None

    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line:
            continue

        side = side_from_csa_move_line(line)
        value = parse_eval_from_comment(line)
        if side is not None:
            if value is not None:
                records.append(EvalRecord(side, value))
            elif pending_eval is not None:
                records.append(EvalRecord(side, pending_eval))
            pending_eval = None
            continue

        if value is not None:
            pending_eval = value

    return records


def extract_kif_eval_records(path: Path) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    current_side: int | None = None

    for raw_line in read_text(path).splitlines():
        line = raw_line.rstrip()
        move_match = KIF_MOVE_LINE_RE.match(line)
        if move_match:
            ply = int(move_match.group(1))
            current_side = cshogi.BLACK if ply % 2 == 1 else cshogi.WHITE
            value = parse_eval_from_comment(line)
            if value is not None:
                records.append(EvalRecord(current_side, value))
            continue

        value = parse_eval_from_comment(line)
        if value is not None and current_side is not None:
            records.append(EvalRecord(current_side, value))

    return records


def extract_eval_records(path: Path) -> list[EvalRecord]:
    suffix = path.suffix.lower()
    if suffix in {".csa", ".csv"}:
        return extract_csa_eval_records(path)
    if suffix in {".kif", ".kifu"}:
        return extract_kif_eval_records(path)
    return []


def parse_csa(path: Path, *, include_eval_records: bool = False) -> list[GameRecord]:
    parsed_games = CSA.Parser.parse_file(str(path))
    if not parsed_games:
        raise ParseError("no CSA game found")

    records: list[GameRecord] = []
    eval_records = extract_eval_records(path) if include_eval_records else []
    game_date = infer_game_date_from_path(path)
    if game_date is None:
        game_date = read_csa_header(path).game_date
    for parsed in parsed_games:
        names = read_names(parsed.names, path)
        ensure_startpos(parsed.sfen)
        moves = moves_to_usi(parsed.moves)
        if not moves:
            raise ParseError("no moves found")

        ratings = list(getattr(parsed, "ratings", []) or [])
        records.append(
            GameRecord(
                path,
                names[0],
                names[1],
                moves,
                optional_float(ratings[0] if len(ratings) >= 1 else None),
                optional_float(ratings[1] if len(ratings) >= 2 else None),
                eval_records,
                game_date,
            )
        )
    return records


def parse_kif(path: Path, *, include_eval_records: bool = False) -> list[GameRecord]:
    parsed = KIF.Parser.parse_file(str(path))
    names = read_names(parsed.names, path)
    ensure_startpos(parsed.sfen)
    moves = moves_to_usi(parsed.moves)
    if not moves:
        raise ParseError("no moves found")
    eval_records = extract_eval_records(path) if include_eval_records else []
    return [GameRecord(path, names[0], names[1], moves, eval_records=eval_records, game_date=infer_game_date_from_path(path))]


def read_names(names: Sequence[str | None], path: Path) -> tuple[str, str]:
    if len(names) < 2 or not names[0] or not names[1]:
        raise ParseError(f"missing player name: {path}")
    return str(names[0]), str(names[1])


def ensure_startpos(sfen: str) -> None:
    if sfen != cshogi.STARTING_SFEN:
        raise ParseError(f"unsupported initial position: {sfen}")


def moves_to_usi(moves: Sequence[int]) -> list[str]:
    return [cshogi.move_to_usi(move) for move in moves]


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_games(path: Path, *, include_eval_records: bool = False) -> list[GameRecord]:
    suffix = path.suffix.lower()
    if suffix in {".kif", ".kifu"}:
        return parse_kif(path, include_eval_records=include_eval_records)
    if suffix in {".csa", ".csv"}:
        return parse_csa(path, include_eval_records=include_eval_records)
    raise ParseError(f"unsupported suffix: {path.suffix}")


def collect_games_from_roots(
    input_roots: Sequence[Path],
    player_filters: PlayerFilters,
    min_rating: float | None,
    *,
    source_kind: str | None,
    year_filter: YearFilter | None,
    date_filter: DateFilter | None,
    wcsc_finalists_only: bool,
    reversal_threshold: int | None,
    require_rating: bool,
    log_target_files: bool,
    verbose: bool,
) -> tuple[list[GameRecord], Stats]:
    stats = Stats()
    games: list[GameRecord] = []
    logged_target_files: set[Path] = set()
    finalists_by_event: dict[str, set[str]] = {}
    if wcsc_finalists_only and source_kind == "wcsc":
        finalists_by_event = collect_wcsc_finalist_names(input_roots, year_filter, verbose=verbose)
    elif wcsc_finalists_only and source_kind == "denryu":
        finalists_by_event = collect_denryu_finalist_names(input_roots, year_filter, verbose=verbose)

    paths = [path for input_root in input_roots for path in iter_kifu_files(input_root)]
    parse_progress = CountProgress("解析中", len(paths))
    effective_min_rating = min_rating if require_rating else None

    for path in paths:
        stats.scanned += 1
        parse_progress.update(stats.scanned)
        if year_filter is not None and not year_filter_passes(path, year_filter):
            stats.skipped_year += 1
            continue

        if should_skip_by_csa_header(path, player_filters, effective_min_rating, date_filter, stats):
            continue

        try:
            parsed_games = parse_games(path, include_eval_records=reversal_threshold is not None)
        except Exception as exc:
            stats.skipped_parse += 1
            if verbose:
                print(f"skip parse: {path}: {exc}", file=sys.stderr)
            continue

        for game in parsed_games:
            if (
                wcsc_finalists_only
                and source_kind == "wcsc"
                and not wcsc_finalist_filter_passes(game, finalists_by_event)
            ):
                stats.skipped_finalist += 1
                continue

            if (
                wcsc_finalists_only
                and source_kind == "denryu"
                and not denryu_finalist_filter_passes(game, finalists_by_event)
            ):
                stats.skipped_finalist += 1
                continue

            if not date_filter_passes(game.game_date, date_filter):
                stats.skipped_date += 1
                continue

            if not player_filters_pass(game.black, game.white, player_filters):
                stats.skipped_name += 1
                continue

            if not rating_passes(game, effective_min_rating):
                stats.skipped_rating += 1
                continue

            if not reversal_passes(game, reversal_threshold):
                stats.skipped_reversal += 1
                continue

            stats.selected += 1
            games.append(game)
            if log_target_files and game.path not in logged_target_files:
                print(f"target file: {game.path}", file=sys.stderr)
                logged_target_files.add(game.path)

    parse_progress.update(stats.scanned, force=True)

    return games, stats


def collect_games(
    input_dir: Path,
    player_filters: PlayerFilters,
    min_rating: float | None,
    *,
    source_kind: str | None,
    year_filter: YearFilter | None,
    date_filter: DateFilter | None,
    wcsc_finalists_only: bool,
    reversal_threshold: int | None,
    require_rating: bool,
    log_target_files: bool,
    verbose: bool,
) -> tuple[list[GameRecord], Stats]:
    with extracted_archive_roots(input_dir, year_filter=year_filter, verbose=verbose) as archive_roots:
        return collect_games_from_roots(
            [input_dir, *archive_roots],
            player_filters,
            min_rating,
            source_kind=source_kind,
            year_filter=year_filter,
            date_filter=date_filter,
            wcsc_finalists_only=wcsc_finalists_only,
            reversal_threshold=reversal_threshold,
            require_rating=require_rating,
            log_target_files=log_target_files,
            verbose=verbose,
        )


def write_position_lines(games: Sequence[GameRecord], output_path: Path) -> tuple[int, int]:
    seen: set[str] = set()
    written = 0
    skipped_duplicate = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as out:
        for game in games:
            line = "startpos moves"
            if game.moves:
                line += " " + " ".join(game.moves)
            if line in seen:
                skipped_duplicate += 1
                continue
            seen.add(line)
            out.write(line)
            out.write("\n")
            written += 1

    return written, skipped_duplicate


def run_extractor(
    input_dir: Path,
    output_path: Path,
    both_player_list: Path | None = None,
    either_player_list: Path | None = None,
    min_rating: float | None = None,
    *,
    source_kind: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    wcsc_finalists_only: bool = False,
    reversal_threshold: int | None = None,
    require_rating: bool = False,
    log_target_files: bool = False,
    verbose: bool = False,
) -> Stats:
    player_filters = PlayerFilters(
        load_player_patterns(both_player_list),
        load_player_patterns(either_player_list),
    )
    date_filter = make_date_filter(source_kind, start_date, end_date)
    year_filter = make_effective_year_filter(source_kind, start_year, end_year, date_filter)
    games, stats = collect_games(
        input_dir,
        player_filters,
        min_rating,
        source_kind=source_kind,
        year_filter=year_filter,
        date_filter=date_filter,
        wcsc_finalists_only=wcsc_finalists_only and source_kind in {"wcsc", "denryu"},
        reversal_threshold=reversal_threshold,
        require_rating=require_rating,
        log_target_files=log_target_files,
        verbose=verbose,
    )
    stats.selected, stats.skipped_duplicate = write_position_lines(games, output_path)
    return stats


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input_dir", type=Path, help="directory to scan recursively")
    parser.add_argument("output", type=Path, help="output text file")
    parser.add_argument(
        "--both-player-list",
        type=Path,
        default=None,
        help="regex list file. Both player names must match if specified.",
    )
    parser.add_argument(
        "--either-player-list",
        type=Path,
        default=None,
        help="regex list file. At least one player name must match if specified.",
    )
    parser.add_argument(
        "--reversal-threshold",
        type=int,
        default=None,
        help="extract only games where one player's own eval reached abs(X) and later crossed zero",
    )
    parser.add_argument("--verbose", action="store_true", help="print parse errors")


def add_year_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start-year", type=int, default=None, help="first year to extract")
    parser.add_argument("--end-year", type=int, default=None, help="last year to extract")


def add_date_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start-date", default=None, help="first game date to extract, YYYY-MM-DD or YYYY/MM/DD")
    parser.add_argument("--end-date", default=None, help="last game date to extract, YYYY-MM-DD or YYYY/MM/DD")


def print_stats(stats: Stats) -> None:
    print(
        "scanned={scanned} selected={selected} skipped_year={skipped_year} "
        "skipped_date={skipped_date} skipped_finalist={skipped_finalist} skipped_name={skipped_name} "
        "skipped_rating={skipped_rating} skipped_reversal={skipped_reversal} skipped_parse={skipped_parse} "
        "skipped_duplicate={skipped_duplicate}".format(**stats.__dict__)
    )
