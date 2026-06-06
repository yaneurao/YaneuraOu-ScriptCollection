#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


FLOODGATE_TOP_URL = "https://wdoor.c.u-tokyo.ac.jp/shogi/"
USER_AGENT = "YaneuraOu-KifManager-Floodgate-Kif-Downloader/1.0"


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


def fetch_bytes(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


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


def destination_filename(year: int, *, today: date | None = None) -> str:
    current_date = today or date.today()
    if year == current_date.year:
        return f"wdoor{year}-{current_date:%Y%m%d}.7z"
    return f"wdoor{year}.7z"


def download_floodgate_kif(
    job: FloodgateDownloadJob,
    *,
    log: Callable[[str], None] | None = None,
) -> FloodgateDownloadStats:
    year = validate_year(job.year)
    if job.timeout <= 0:
        raise FloodgateDownloadError("timeout は 0 より大きい値を指定してください。")

    archive_url = find_archive_url(year, timeout=job.timeout)
    destination = job.output_dir.expanduser() / destination_filename(year)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if log is not None:
        log(f"url      : {archive_url}\n")
        log(f"output   : {destination}\n")

    data = fetch_bytes(archive_url, job.timeout)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)

    return FloodgateDownloadStats(
        year=year,
        archive_url=archive_url,
        destination=destination,
        bytes_written=len(data),
    )
