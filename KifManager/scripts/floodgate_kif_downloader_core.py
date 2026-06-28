#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


FLOODGATE_TOP_URL = "https://wdoor.c.u-tokyo.ac.jp/shogi/"
FLOODGATE_DAILY_BASE_URL = "https://wdoor.c.u-tokyo.ac.jp/shogi/x/"
FLOODGATE_TODAY_URL = "https://wdoor.c.u-tokyo.ac.jp/shogi/x/today/"
USER_AGENT = "YaneuraOu-KifManager-Floodgate-Kif-Downloader/1.0"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
CSA_TERMINAL_MARKERS = {
    "%TORYO",
    "%CHUDAN",
    "%SENNICHITE",
    "%JISHOGI",
    "%KACHI",
    "%HIKIWAKE",
    "%MATTA",
    "%ILLEGAL_MOVE",
    "%TIME_UP",
}


class FloodgateDownloadError(Exception):
    pass


@dataclass(frozen=True)
class FloodgateDownloadJob:
    year: int
    output_dir: Path
    timeout: float = 60.0


@dataclass(frozen=True)
class FloodgateDailyDownloadJob:
    start_date: date
    end_date: date
    output_dir: Path
    timeout: float = 60.0


@dataclass(frozen=True)
class DailyDownloadStats:
    url: str
    destination_dir: Path
    found: int
    downloaded: int
    skipped: int
    failed: int
    bytes_written: int


@dataclass(frozen=True)
class FloodgateDownloadStats:
    year: int
    archive_url: str
    destination: Path
    bytes_written: int
    skipped: bool = False
    remote_bytes: int | None = None
    local_bytes: int | None = None


@dataclass(frozen=True)
class FloodgateDailyRangeDownloadStats:
    start_date: date
    end_date: date
    output_dir: Path
    days: tuple[DailyDownloadStats, ...]

    @property
    def found(self) -> int:
        return sum(day.found for day in self.days)

    @property
    def downloaded(self) -> int:
        return sum(day.downloaded for day in self.days)

    @property
    def skipped(self) -> int:
        return sum(day.skipped for day in self.days)

    @property
    def failed(self) -> int:
        return sum(day.failed for day in self.days)

    @property
    def bytes_written(self) -> int:
        return sum(day.bytes_written for day in self.days)


TodayDownloadStats = DailyDownloadStats


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def validate_year(value: int) -> int:
    if value < 2008:
        raise FloodgateDownloadError("2008以降の年を指定してください。")
    return value


def stop_requested(should_stop: Callable[[], bool] | None) -> bool:
    return should_stop is not None and should_stop()


def fetch_bytes(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_content_length(url: str, timeout: float) -> int | None:
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    with urlopen(request, timeout=timeout) as response:
        header = response.headers.get("Content-Length")
    if header is None:
        return None
    try:
        content_length = int(header)
    except ValueError:
        return None
    return content_length if content_length >= 0 else None


def download_to_file(
    url: str,
    destination: Path,
    timeout: float,
    should_stop: Callable[[], bool] | None,
) -> int:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    bytes_written = 0
    with urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
        while True:
            if stop_requested(should_stop):
                raise FloodgateDownloadError("停止要求を受け付けました。")
            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            bytes_written += len(chunk)
    return bytes_written


def download_changed_file(
    url: str,
    destination: Path,
    timeout: float,
    should_stop: Callable[[], bool] | None,
) -> tuple[int, bool, int | None, int | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_file():
        raise FloodgateDownloadError(f"出力先が通常ファイルではありません: {destination}")

    local_bytes = destination.stat().st_size if destination.exists() else None
    if destination.exists() and is_complete_csa_file(destination):
        return 0, True, None, local_bytes

    try:
        remote_bytes = fetch_content_length(url, timeout)
    except OSError:
        remote_bytes = None
    if remote_bytes is not None and local_bytes == remote_bytes:
        return 0, True, remote_bytes, local_bytes

    temporary = destination.with_name(destination.name + ".tmp")
    try:
        bytes_written = download_to_file(url, temporary, timeout, should_stop)
        if remote_bytes is not None and bytes_written != remote_bytes:
            raise FloodgateDownloadError(
                f"ダウンロードサイズが一致しません: expected={remote_bytes} actual={bytes_written}"
            )
        if remote_bytes is None and local_bytes == bytes_written:
            temporary.unlink(missing_ok=True)
            return bytes_written, True, remote_bytes, local_bytes
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    temporary.replace(destination)
    return bytes_written, False, remote_bytes, local_bytes


def is_complete_csa_file(path: Path) -> bool:
    if path.suffix.lower() != ".csa" or not path.is_file():
        return False
    try:
        text = decode_text(path.read_bytes())
    except OSError:
        return False
    for line in reversed(text.splitlines()):
        marker = line.strip().split(",", 1)[0]
        if not marker:
            continue
        if marker in CSA_TERMINAL_MARKERS:
            return True
    return False


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def find_archive_url(year: int, *, timeout: float = 60.0) -> str:
    year = validate_year(year)
    expected_name = f"wdoor{year}.7z"
    html = decode_text(fetch_bytes(FLOODGATE_TOP_URL, timeout))

    parser = LinkExtractor()
    parser.feed(html)
    for href in parser.hrefs:
        filename = unquote(Path(urlparse(href).path).name)
        if filename == expected_name:
            return urljoin(FLOODGATE_TOP_URL, href)

    direct_url = urljoin(FLOODGATE_TOP_URL, f"archive/{expected_name}")
    if re.search(rf'href=["\'][^"\']*{re.escape(expected_name)}["\']', html, re.IGNORECASE):
        return direct_url

    raise FloodgateDownloadError(f"floodgate棋譜アーカイブが見つかりません: {expected_name}")


def destination_filename(year: int) -> str:
    return f"wdoor{year}.7z"


def daily_folder_name_from_html(html: str, *, fallback_date: date | None = None) -> str:
    match = re.search(r"Folders and files:\s*(20\d{2})/(\d{2})/(\d{2})", html)
    if match:
        return "".join(match.groups())
    if fallback_date is None:
        fallback_date = date.today()
    return fallback_date.strftime("%Y%m%d")


def today_folder_name_from_html(html: str) -> str:
    return daily_folder_name_from_html(html, fallback_date=date.today())


def csa_filename_from_url(url: str) -> str:
    filename = unquote(Path(urlparse(url).path).name)
    if not filename.lower().endswith(".csa"):
        raise FloodgateDownloadError(f"CSAファイル名ではありません: {url}")
    return filename


def floodgate_daily_url(target_date: date) -> str:
    return urljoin(FLOODGATE_DAILY_BASE_URL, f"{target_date:%Y/%m/%d}/")


def extract_daily_csa_urls(html: str, *, base_url: str) -> list[str]:
    parser = LinkExtractor()
    parser.feed(html)
    urls: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        filename = unquote(Path(urlparse(href).path).name)
        if not filename.lower().endswith(".csa"):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def extract_today_csa_urls(html: str, *, base_url: str = FLOODGATE_TODAY_URL) -> list[str]:
    return extract_daily_csa_urls(html, base_url=base_url)


def download_daily_csa_files(
    output_dir: Path,
    *,
    page_url: str,
    label: str,
    fallback_date: date,
    timeout: float,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DailyDownloadStats:
    if stop_requested(should_stop):
        raise FloodgateDownloadError("停止要求を受け付けました。")

    html = decode_text(fetch_bytes(page_url, timeout))
    destination_dir = output_dir.expanduser() / daily_folder_name_from_html(html, fallback_date=fallback_date)
    csa_urls = extract_daily_csa_urls(html, base_url=page_url)
    if log is not None:
        log(f"{label} url: {page_url}\n")
        log(f"{label} output: {destination_dir}\n")
        log(f"{label} files: {len(csa_urls)}\n")

    downloaded = 0
    skipped = 0
    failed = 0
    bytes_written_total = 0
    for index, csa_url in enumerate(csa_urls, start=1):
        if stop_requested(should_stop):
            raise FloodgateDownloadError("停止要求を受け付けました。")
        filename = csa_filename_from_url(csa_url)
        destination = destination_dir / filename
        try:
            bytes_written, is_skipped, _remote_bytes, _local_bytes = download_changed_file(
                csa_url,
                destination,
                timeout,
                should_stop,
            )
        except Exception as exc:
            if stop_requested(should_stop):
                raise
            failed += 1
            if log is not None:
                log(f"[{label} {index}/{len(csa_urls)}] failed: {filename}: {exc}\n")
            continue

        bytes_written_total += bytes_written
        if is_skipped:
            skipped += 1
            if log is not None:
                log(f"[{label} {index}/{len(csa_urls)}] skipped: {filename}\n")
        else:
            downloaded += 1
            if log is not None:
                log(f"[{label} {index}/{len(csa_urls)}] downloaded: {filename} ({bytes_written} bytes)\n")

    return DailyDownloadStats(
        url=page_url,
        destination_dir=destination_dir,
        found=len(csa_urls),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        bytes_written=bytes_written_total,
    )


def download_yesterday_csa_files(
    output_dir: Path,
    *,
    timeout: float,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    target_date: date | None = None,
) -> DailyDownloadStats:
    if target_date is None:
        target_date = date.today() - timedelta(days=1)
    return download_daily_csa_files(
        output_dir,
        page_url=floodgate_daily_url(target_date),
        label="yesterday",
        fallback_date=target_date,
        timeout=timeout,
        log=log,
        should_stop=should_stop,
    )


def download_today_csa_files(
    output_dir: Path,
    *,
    timeout: float,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> DailyDownloadStats:
    return download_daily_csa_files(
        output_dir,
        page_url=FLOODGATE_TODAY_URL,
        label="today",
        fallback_date=date.today(),
        timeout=timeout,
        log=log,
        should_stop=should_stop,
    )


def iter_dates(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def download_floodgate_daily_kif(
    job: FloodgateDailyDownloadJob,
    *,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> FloodgateDailyRangeDownloadStats:
    if job.start_date > job.end_date:
        raise FloodgateDownloadError("開始日は終了日以下を指定してください。")
    if job.timeout <= 0:
        raise FloodgateDownloadError("timeout は 0 より大きい値を指定してください。")

    output_dir = job.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise FloodgateDownloadError(f"出力フォルダがフォルダではありません: {output_dir}")

    days: list[DailyDownloadStats] = []
    for target_date in iter_dates(job.start_date, job.end_date):
        if stop_requested(should_stop):
            raise FloodgateDownloadError("停止要求を受け付けました。")
        days.append(
            download_daily_csa_files(
                output_dir,
                page_url=floodgate_daily_url(target_date),
                label=target_date.strftime("%Y%m%d"),
                fallback_date=target_date,
                timeout=job.timeout,
                log=log,
                should_stop=should_stop,
            )
        )

    return FloodgateDailyRangeDownloadStats(
        start_date=job.start_date,
        end_date=job.end_date,
        output_dir=output_dir,
        days=tuple(days),
    )


def download_floodgate_kif(
    job: FloodgateDownloadJob,
    *,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> FloodgateDownloadStats:
    year = validate_year(job.year)
    if job.timeout <= 0:
        raise FloodgateDownloadError("timeout は 0 より大きい値を指定してください。")

    if stop_requested(should_stop):
        raise FloodgateDownloadError("停止要求を受け付けました。")
    archive_url = find_archive_url(year, timeout=job.timeout)
    if stop_requested(should_stop):
        raise FloodgateDownloadError("停止要求を受け付けました。")
    destination = job.output_dir.expanduser() / destination_filename(year)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not destination.is_file():
        raise FloodgateDownloadError(f"出力先が通常ファイルではありません: {destination}")

    if log is not None:
        log(f"url      : {archive_url}\n")
        log(f"output   : {destination}\n")

    if stop_requested(should_stop):
        raise FloodgateDownloadError("停止要求を受け付けました。")
    try:
        remote_bytes = fetch_content_length(archive_url, job.timeout)
    except OSError as exc:
        remote_bytes = None
        if log is not None:
            log(f"size     : unknown ({exc})\n")
    else:
        if log is not None:
            size_text = str(remote_bytes) if remote_bytes is not None else "unknown"
            log(f"size     : {size_text}\n")

    local_bytes = destination.stat().st_size if destination.exists() else None
    if remote_bytes is not None and local_bytes == remote_bytes:
        if log is not None:
            log(f"skip     : existing file has same size ({local_bytes} bytes)\n")
        return FloodgateDownloadStats(
            year=year,
            archive_url=archive_url,
            destination=destination,
            bytes_written=0,
            skipped=True,
            remote_bytes=remote_bytes,
            local_bytes=local_bytes,
        )

    temporary = destination.with_name(destination.name + ".tmp")
    try:
        bytes_written = download_to_file(archive_url, temporary, job.timeout, should_stop)
        if stop_requested(should_stop):
            raise FloodgateDownloadError("停止要求を受け付けました。")
        if remote_bytes is not None and bytes_written != remote_bytes:
            raise FloodgateDownloadError(
                f"ダウンロードサイズが一致しません: expected={remote_bytes} actual={bytes_written}"
            )
        if remote_bytes is None and local_bytes == bytes_written:
            if log is not None:
                log(f"skip     : downloaded file has same size as existing file ({local_bytes} bytes)\n")
            temporary.unlink(missing_ok=True)
            return FloodgateDownloadStats(
                year=year,
                archive_url=archive_url,
                destination=destination,
                bytes_written=bytes_written,
                skipped=True,
                remote_bytes=remote_bytes,
                local_bytes=local_bytes,
            )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    temporary.replace(destination)

    return FloodgateDownloadStats(
        year=year,
        archive_url=archive_url,
        destination=destination,
        bytes_written=bytes_written,
        skipped=False,
        remote_bytes=remote_bytes,
        local_bytes=local_bytes,
    )
