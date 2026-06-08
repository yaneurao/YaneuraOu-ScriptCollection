#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


FLOODGATE_TOP_URL = "https://wdoor.c.u-tokyo.ac.jp/shogi/"
USER_AGENT = "YaneuraOu-KifManager-Floodgate-Kif-Downloader/1.0"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


class FloodgateDownloadError(Exception):
    pass


@dataclass(frozen=True)
class FloodgateDownloadJob:
    year: int
    output_dir: Path
    timeout: float = 60.0


@dataclass(frozen=True)
class FloodgateDownloadStats:
    year: int
    archive_url: str
    destination: Path
    bytes_written: int
    skipped: bool = False
    remote_bytes: int | None = None
    local_bytes: int | None = None


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
