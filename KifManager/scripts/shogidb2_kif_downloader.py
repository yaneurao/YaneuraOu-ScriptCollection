#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


SHOGIDB2_BASE_URL = "https://shogidb2.com"
SHOGIDB2_TOURNAMENTS_URL = f"{SHOGIDB2_BASE_URL}/tournaments"
SHOGIDB2_DEFAULT_OUTPUT_DIR = "downloaded-kif/shogidb2"
USER_AGENT = "YaneuraOu-KifManager-ShogiDB2-Kif-Downloader/1.0"
GAME_PATH_RE = re.compile(r"^/games/([0-9a-fA-F]+)$")
UNSAFE_FILENAME_CHARS = '<>:"/\\|?*'


class ShogiDb2DownloadError(Exception):
    pass


@dataclass(frozen=True)
class ShogiDb2TournamentOption:
    name: str
    url: str
    count: int | None = None

    @property
    def display_name(self) -> str:
        if self.count is None:
            return self.name
        return f"{self.name} : {self.count}件"


@dataclass(frozen=True)
class ShogiDb2DownloadJob:
    tournament_url: str
    output_root: Path
    start_page: int = 1
    end_page: int | None = 1
    interval: float = 2.0
    overwrite: bool = False
    stop_after_skipped: int | None = None
    timeout: float = 60.0
    headless: bool = True


@dataclass(frozen=True)
class ShogiDb2DownloadStats:
    tournament: str
    output_dir: Path
    pages_scanned: int
    found: int
    downloaded: int
    skipped: int
    failed: int


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self._href = value
                self._text_parts = []
                return

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._text_parts)).strip()
        self.links.append((self._href, text))
        self._href = None
        self._text_parts = []


def stop_requested(should_stop: Callable[[], bool] | None) -> bool:
    return should_stop is not None and should_stop()


def sleep_with_stop(seconds: float, should_stop: Callable[[], bool] | None) -> bool:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if stop_requested(should_stop):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return stop_requested(should_stop)
        time.sleep(min(0.2, remaining))


class RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, interval)
        self.last_access: float | None = None

    def wait(self, should_stop: Callable[[], bool] | None = None) -> bool:
        if self.last_access is not None:
            elapsed = time.monotonic() - self.last_access
            remaining = self.interval - elapsed
            if remaining > 0 and sleep_with_stop(remaining, should_stop):
                return False
        self.last_access = time.monotonic()
        return not stop_requested(should_stop)


def fetch_text(
    url: str,
    timeout: float,
    limiter: RateLimiter | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> str:
    if limiter is not None:
        if not limiter.wait(should_stop):
            return ""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        data = response.read()
    return data.decode("utf-8", errors="replace")


def fetch_shogidb2_tournament_options(timeout: float = 60.0) -> list[ShogiDb2TournamentOption]:
    html = fetch_text(SHOGIDB2_TOURNAMENTS_URL, timeout)
    parser = LinkExtractor()
    parser.feed(html)
    options: dict[str, ShogiDb2TournamentOption] = {}

    for href, text in parser.links:
        parsed = urlparse(urljoin(SHOGIDB2_TOURNAMENTS_URL, href))
        if parsed.netloc != "shogidb2.com":
            continue
        if not parsed.path.startswith("/tournament/"):
            continue

        name = unquote(parsed.path.removeprefix("/tournament/")).strip()
        if not name or name in options:
            continue

        count = None
        match = re.search(r"([0-9,]+)\s*件", text)
        if match:
            count = int(match.group(1).replace(",", ""))

        options[name] = ShogiDb2TournamentOption(
            name=name,
            url=f"{SHOGIDB2_BASE_URL}/tournament/{quote(name, safe='')}",
            count=count,
        )

    return list(options.values())


def normalize_tournament_url(value: str) -> tuple[str, str]:
    source = value.strip()
    if not source:
        raise ShogiDb2DownloadError("棋戦を指定してください。")

    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        if parsed.netloc != "shogidb2.com" or not parsed.path.startswith("/tournament/"):
            raise ShogiDb2DownloadError(f"shogidb2の棋戦URLを指定してください: {source}")
        name = unquote(parsed.path.removeprefix("/tournament/"))
        return name, f"{SHOGIDB2_BASE_URL}/tournament/{quote(name, safe='')}"

    name = source
    return name, f"{SHOGIDB2_BASE_URL}/tournament/{quote(name, safe='')}"


def tournament_page_url(tournament_url: str, page: int) -> str:
    return f"{tournament_url}?q=&page={page}"


def collect_game_urls_from_page(
    tournament_url: str,
    page: int,
    *,
    timeout: float,
    limiter: RateLimiter,
    should_stop: Callable[[], bool] | None = None,
) -> list[str]:
    if stop_requested(should_stop):
        return []
    html = fetch_text(tournament_page_url(tournament_url, page), timeout, limiter, should_stop)
    if stop_requested(should_stop):
        return []
    parser = LinkExtractor()
    parser.feed(html)

    urls: list[str] = []
    seen: set[str] = set()
    for href, _text in parser.links:
        parsed = urlparse(urljoin(SHOGIDB2_BASE_URL, href))
        match = GAME_PATH_RE.match(parsed.path)
        if not match:
            continue
        url = f"{SHOGIDB2_BASE_URL}{parsed.path}"
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


class ShogiDb2KifBrowser:
    def __init__(self, *, headless: bool, timeout: float) -> None:
        self.timeout = timeout
        self.driver = self._create_driver(headless=headless)

    def close(self) -> None:
        self.driver.quit()

    def export_kif(self, game_url: str) -> str:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self.driver.set_page_load_timeout(self.timeout)
        self.driver.get(game_url)
        wait = WebDriverWait(self.driver, self.timeout)
        wait.until(
            lambda driver: driver.execute_script(
                "return !!window.liveSocket && !!document.querySelector('a[phx-click=\"kif\"]')"
            )
        )
        self.driver.execute_script("document.querySelector('a[phx-click=\"kif\"]').click()")

        def textarea_value(driver):
            element = driver.find_element(By.CSS_SELECTOR, "#kifu-modal textarea")
            value = element.get_attribute("value") or element.get_attribute("innerHTML") or ""
            return value if len(value) > 10 else False

        kif = wait.until(textarea_value)
        if not isinstance(kif, str) or "手数----指手" not in kif:
            raise ShogiDb2DownloadError(f"KIFを書き出せませんでした: {game_url}")
        return kif.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"

    def _create_driver(self, *, headless: bool):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError as exc:
            raise ShogiDb2DownloadError("shogidb2ダウンロードには selenium が必要です。") from exc

        options = Options()
        chromium_binary = find_chromium_binary()
        if chromium_binary:
            options.binary_location = chromium_binary
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1200,1000")
        options.add_argument(f"--user-agent={USER_AGENT}")
        return webdriver.Chrome(options=options)


def find_chromium_binary() -> str:
    candidates = (
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return ""


def game_id_from_url(game_url: str) -> str:
    match = GAME_PATH_RE.match(urlparse(game_url).path)
    if not match:
        raise ShogiDb2DownloadError(f"ゲームURLではありません: {game_url}")
    return match.group(1)


def sanitize_path_part(value: str) -> str:
    sanitized = "".join("_" if char in UNSAFE_FILENAME_CHARS else char for char in value).strip()
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized or "shogidb2"


def download_shogidb2_kif(
    job: ShogiDb2DownloadJob,
    *,
    log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ShogiDb2DownloadStats:
    if job.start_page < 1:
        raise ShogiDb2DownloadError("開始ページは1以上を指定してください。")
    if job.end_page is not None and job.end_page < job.start_page:
        raise ShogiDb2DownloadError("終了ページは開始ページ以上を指定してください。")
    if job.interval < 0:
        raise ShogiDb2DownloadError("アクセス間隔(秒)は0以上を指定してください。")
    if job.timeout <= 0:
        raise ShogiDb2DownloadError("timeout は 0 より大きい値を指定してください。")
    if job.stop_after_skipped is not None and job.stop_after_skipped < 1:
        raise ShogiDb2DownloadError("skipped停止件数は1以上を指定してください。")

    tournament_name, tournament_url = normalize_tournament_url(job.tournament_url)
    output_dir = job.output_root.expanduser() / sanitize_path_part(tournament_name)
    output_dir.mkdir(parents=True, exist_ok=True)

    limiter = RateLimiter(job.interval)
    pages_scanned = 0
    found = 0
    downloaded = 0
    skipped = 0
    failed = 0

    browser = ShogiDb2KifBrowser(headless=job.headless, timeout=job.timeout)
    try:
        page = job.start_page
        while True:
            if stop_requested(should_stop):
                if log is not None:
                    log("stop requested\n")
                break
            if job.end_page is not None and page > job.end_page:
                break

            game_urls = collect_game_urls_from_page(
                tournament_url,
                page,
                timeout=job.timeout,
                limiter=limiter,
                should_stop=should_stop,
            )
            if stop_requested(should_stop):
                if log is not None:
                    log("stop requested\n")
                break
            pages_scanned += 1
            if log is not None:
                log(f"page {page}: {len(game_urls)} games\n")
            if not game_urls:
                break

            for index, game_url in enumerate(game_urls, 1):
                if stop_requested(should_stop):
                    if log is not None:
                        log("stop requested\n")
                    break
                found += 1
                game_id = game_id_from_url(game_url)
                destination = output_dir / f"{game_id}.kif"
                if destination.exists() and not job.overwrite:
                    skipped += 1
                    if log is not None:
                        log(f"[page {page} {index}/{len(game_urls)}] skipped: {destination.name}\n")
                    if job.stop_after_skipped is not None and skipped >= job.stop_after_skipped:
                        if log is not None:
                            log(f"stop: skipped reached {job.stop_after_skipped}\n")
                        break
                    continue

                try:
                    if not limiter.wait(should_stop):
                        if log is not None:
                            log("stop requested\n")
                        break
                    kif = browser.export_kif(game_url)
                    if stop_requested(should_stop):
                        if log is not None:
                            log("stop requested\n")
                        break
                    temporary = destination.with_name(destination.name + ".tmp")
                    temporary.write_text(kif, encoding="utf-8", newline="\n")
                    temporary.replace(destination)
                    downloaded += 1
                    if log is not None:
                        log(f"[page {page} {index}/{len(game_urls)}] downloaded: {destination.name}\n")
                except Exception as exc:
                    failed += 1
                    if log is not None:
                        log(f"[page {page} {index}/{len(game_urls)}] failed: {game_url}: {exc}\n")

            if stop_requested(should_stop):
                break
            if job.stop_after_skipped is not None and skipped >= job.stop_after_skipped:
                break
            page += 1
    finally:
        browser.close()

    return ShogiDb2DownloadStats(
        tournament=tournament_name,
        output_dir=output_dir,
        pages_scanned=pages_scanned,
        found=found,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
    )


def parse_page(value: str) -> int:
    try:
        page = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("page must be an integer.") from exc
    if page < 1:
        raise argparse.ArgumentTypeError("page must be 1 or greater.")
    return page


def parse_positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer.") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("value must be 1 or greater.")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download KIF files from shogidb2.")
    parser.add_argument("tournament", nargs="?", help="tournament name or shogidb2 tournament URL")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path(SHOGIDB2_DEFAULT_OUTPUT_DIR))
    parser.add_argument("--start-page", type=parse_page, default=1)
    parser.add_argument("--end-page", type=parse_page, default=1)
    parser.add_argument("--until-empty", action="store_true", help="ignore --end-page and continue until an empty page")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--stop-after-skipped",
        type=parse_positive_int,
        default=None,
        help="stop after skipped reaches this count. Disabled by default.",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--list-tournaments", action="store_true")
    args = parser.parse_args(argv)

    if args.list_tournaments:
        for option in fetch_shogidb2_tournament_options(timeout=args.timeout):
            print(f"{option.name}\t{option.count if option.count is not None else ''}\t{option.url}")
        return 0

    if not args.tournament:
        parser.error("tournament is required unless --list-tournaments is specified.")

    try:
        stats = download_shogidb2_kif(
            ShogiDb2DownloadJob(
                tournament_url=args.tournament,
                output_root=args.output_dir,
                start_page=args.start_page,
                end_page=None if args.until_empty else args.end_page,
                interval=args.interval,
                overwrite=args.overwrite,
                stop_after_skipped=args.stop_after_skipped,
                timeout=args.timeout,
                headless=not args.no_headless,
            ),
            log=lambda text: print(text, end=""),
        )
    except (ShogiDb2DownloadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"tournament={stats.tournament} pages={stats.pages_scanned} found={stats.found} "
        f"downloaded={stats.downloaded} skipped={stats.skipped} failed={stats.failed} "
        f"output={stats.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
