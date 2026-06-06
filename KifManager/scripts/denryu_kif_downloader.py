#!/usr/bin/env python3
from __future__ import annotations

import gc
import html
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import URLError
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


DENRYU_LINK_INDEX_URL = "https://denryu-sen.jp/denryusen/dr_link/dr1_live.php"
DENRYU_DEFAULT_OUTPUT_DIR = "downloaded-kif/denryu"
USER_AGENT = "YaneuraOu-KifManager-Denryu-Kif-Downloader/1.0"
KIFULIST_RE = re.compile(
    r'<a\s+href=["\'](?:\./)?kifujs/([^"\']+?)\.html["\'][^>]*>(.*?)</a>',
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
UNSAFE_FILENAME_CHARS = '<>:"/\\|?*'


class DenryuDownloadError(Exception):
    pass


@dataclass(frozen=True)
class DenryuTournamentOption:
    key: str
    title: str
    live_url: str = ""
    archive_url: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.key} : {self.title}"

    @property
    def preferred_url(self) -> str:
        return self.live_url or self.archive_url


@dataclass(frozen=True)
class DenryuDownloadJob:
    source_url: str
    output_root: Path
    interval: float
    overwrite: bool = False
    timeout: float = 60.0
    use_live_page: bool = False
    archive_url: str = ""


@dataclass(frozen=True)
class DenryuDownloadStats:
    tournament: str
    mode: str
    source_url: str
    list_url: str
    output_dir: Path
    found: int
    downloaded: int
    skipped: int


class RateLimiter:
    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, interval)
        self.last_access: float | None = None

    def wait(self) -> None:
        if self.last_access is not None:
            elapsed = time.monotonic() - self.last_access
            remaining = self.interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self.last_access = time.monotonic()


def remove_tree_with_retries(
    path: Path,
    *,
    log: Callable[[str], None] | None = None,
    attempts: int = 5,
    delay_seconds: float = 0.2,
) -> bool:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            last_error = exc
            gc.collect()
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)

    if log is not None and last_error is not None:
        log(f"warning  : failed to remove tmp dir: {path}: {last_error}\n")
    return False


def remove_empty_directory(path: Path) -> None:
    with suppress(OSError):
        path.rmdir()


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
        self.links.append((clean_title("".join(self._text_parts)), self._href))
        self._href = None
        self._text_parts = []


def clean_title(value: str) -> str:
    title = html.unescape(TAG_RE.sub("", value))
    title = re.sub(r"\s+", " ", title).strip()
    return title or "(無題)"


def fetch_bytes(url: str, limiter: RateLimiter | None, timeout: float) -> bytes:
    if limiter is not None:
        limiter.wait()
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


def normalize_source_url(value: str) -> str:
    source = value.strip()
    if not source:
        raise DenryuDownloadError("大会URLを指定してください。")
    parsed = urlparse(source)
    if parsed.scheme and parsed.netloc:
        return source
    if re.fullmatch(r"[A-Za-z0-9_.-]+", source):
        return f"https://denryu-sen.jp/denryusen/{source}/dr1_live.php"
    raise DenryuDownloadError("大会URL、または dr6_production のような大会キーを指定してください。")


def denryu_tournament_key(source_url: str) -> str:
    parsed = urlparse(source_url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("denryusen")
    except ValueError as exc:
        raise DenryuDownloadError(f"電竜戦のURLではありません: {source_url}") from exc

    if index + 1 >= len(parts):
        raise DenryuDownloadError(f"大会キーをURLから取得できません: {source_url}")
    key = parts[index + 1]
    if key in {"dr_link", "supporter"}:
        raise DenryuDownloadError(f"大会ページのURLを指定してください: {source_url}")
    return key


def denryu_base_url(source_url: str) -> str:
    source_url = normalize_source_url(source_url)
    key = denryu_tournament_key(source_url)
    parsed = urlparse(source_url)
    prefix = parsed.path.split(f"/{key}/", 1)[0]
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/{key}/"


def fetch_denryu_tournament_options(timeout: float = 60.0) -> list[DenryuTournamentOption]:
    html_text = decode_text(fetch_bytes(DENRYU_LINK_INDEX_URL, None, timeout))
    parser = LinkExtractor()
    parser.feed(html_text)
    options: dict[str, DenryuTournamentOption] = {}

    for title, href in parser.links:
        url = urljoin(DENRYU_LINK_INDEX_URL, href)
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host not in {"denryu-sen.jp", "golan.sakura.ne.jp"}:
            continue
        if "/denryusen/" not in parsed.path:
            continue

        path_lower = parsed.path.lower()
        try:
            key = denryu_tournament_key(url)
        except DenryuDownloadError:
            continue

        current = options.get(key, DenryuTournamentOption(key, title))
        if path_lower.endswith(".zip"):
            current = DenryuTournamentOption(
                key,
                choose_title(current.title, title),
                current.live_url,
                url,
            )
        elif Path(parsed.path).name.lower() == "dr1_live.php":
            current = DenryuTournamentOption(
                key,
                choose_title(title, current.title),
                url,
                current.archive_url,
            )
        else:
            continue
        options[key] = current

    return sort_tournament_options(list(options.values()))


def fallback_denryu_tournament_options() -> list[DenryuTournamentOption]:
    raw_options = [
        ("dr7_tsec7", "第7回電竜戦TSECノー居飛車指定局面と先手持ち時間0戦", "https://denryu-sen.jp/denryusen/dr7_tsec7/dr1_live.php", ""),
        ("dr6_production", "文部科学大臣杯第6回電竜戦本戦", "https://denryu-sen.jp/denryusen/dr6_production/dr1_live.php", "https://denryu-sen.jp/denryusen/dr6_production/kifu_dr6production.zip"),
        ("dr6_tsec", "第6回電竜戦TSEC指定局面と香落ち戦", "https://denryu-sen.jp/denryusen/dr6_tsec/dr1_live.php", "https://denryu-sen.jp/denryusen/dr6_tsec/kifu_dr6tsec.zip"),
        ("dr5_hardware3", "第3回電竜戦ハードウェア統一戦", "https://denryu-sen.jp/denryusen/dr5_hardware3/dr1_live.php", "https://denryu-sen.jp/denryusen/dr5_hardware3/kifu_dr5hdw3.zip"),
        ("dr5_production", "文部科学大臣杯第5回電竜戦本戦", "https://denryu-sen.jp/denryusen/dr5_production/dr1_live.php", "https://denryu-sen.jp/denryusen/dr5_production/kif_dr5_production.zip"),
        ("dr5_tsec", "第5回電竜戦TSEC指定局面戦", "https://denryu-sen.jp/denryusen/dr5_tsec/dr1_live.php", "https://denryu-sen.jp/denryusen/dr5_tsec/kif_dr5_tsec.zip"),
        ("dr4_hardware2", "第2回マイナビニュース杯電竜戦ハードウェア統一戦", "https://denryu-sen.jp/denryusen/dr4_hardware2/dr1_live.php", "https://denryu-sen.jp/denryusen/dr4_hardware2/kif_dr4_hdw2.zip"),
        ("dr4_production", "第4回電竜戦本戦", "https://denryu-sen.jp/denryusen/dr4_production/dr1_live.php", "https://denryu-sen.jp/denryusen/dr4_production/kif_dr4prd.zip"),
        ("dr4_tsec", "第4回電竜戦TSEC指定局面戦", "https://denryu-sen.jp/denryusen/dr4_tsec/dr1_live.php", "https://denryu-sen.jp/denryusen/dr4_tsec/kif_dr4_tsec.zip"),
        ("dr4_sakura", "電竜戦さくらパイルール2023", "https://denryu-sen.jp/denryusen/dr4_sakura/dr1_live.php", "https://denryu-sen.jp/denryusen/dr4_sakura/kifu_dr4_sakura.zip"),
        ("dr3_hardware1", "第1回マイナビニュース杯電竜戦ハードウェア統一戦", "https://denryu-sen.jp/denryusen/dr3_hardware1/dr1_live.php", "https://denryu-sen.jp/denryusen/dr3_hardware1/dr3_hardware1_kifu.zip"),
        ("dr3_production", "第3回電竜戦本戦", "https://denryu-sen.jp/denryusen/dr3_production/dr1_live.php", "https://denryu-sen.jp/denryusen/dr3_production/kif_dr3.zip"),
        ("dr3_tsec", "第3回電竜戦TSEC指定局面戦", "https://denryu-sen.jp/denryusen/dr3_tsec/dr1_live.php", "https://denryu-sen.jp/denryusen/dr3_tsec/dr3_tsec_kifu.zip"),
        ("dr3_sakura", "電竜戦さくらリーグ2022", "https://golan.sakura.ne.jp/denryusen/dr3_sakura/dr1_live.php", "https://golan.sakura.ne.jp/denryusen/dr3_sakura/dr3sakura_kifu.zip"),
        ("dr2_production", "第2回電竜戦本戦", "https://golan.sakura.ne.jp/denryusen/dr2_production/dr1_live.php", "https://golan.sakura.ne.jp/denryusen/dr2_production/dr2_kifu.zip"),
        ("dr2_tsec", "第2回電竜戦TSEC", "https://golan.sakura.ne.jp/denryusen/dr2_tsec/dr1_live.php", "https://golan.sakura.ne.jp/denryusen/dr2_tsec/kif_tsec2.zip"),
        ("dr1_production", "第1回電竜戦本戦", "https://golan.sakura.ne.jp/denryusen/dr1_production/dr1_live.php", ""),
        ("dr1_tsec_p1", "第1回電竜戦TSEC", "https://golan.sakura.ne.jp/denryusen/dr1_tsec_p1/dr1_live.php", ""),
    ]
    return sort_tournament_options([DenryuTournamentOption(*item) for item in raw_options])


def choose_title(preferred: str, fallback: str) -> str:
    return preferred if preferred and preferred != "(無題)" else fallback


def sort_tournament_options(options: list[DenryuTournamentOption]) -> list[DenryuTournamentOption]:
    return sorted(options, key=lambda option: tournament_sort_key(option.key), reverse=True)


def tournament_sort_key(key: str) -> tuple[int, str]:
    match = re.match(r"dr(\d+)", key)
    number = int(match.group(1)) if match else 0
    return number, key


def find_archive_url(
    source_url: str,
    *,
    explicit_archive_url: str = "",
    limiter: RateLimiter,
    timeout: float,
) -> str:
    source_url = normalize_source_url(source_url)
    if explicit_archive_url:
        return explicit_archive_url
    if urlparse(source_url).path.lower().endswith(".zip"):
        return source_url

    key = denryu_tournament_key(source_url)
    candidate_pages = [source_url, urljoin(denryu_base_url(source_url), "dr1_live.php")]
    seen_pages: set[str] = set()
    for page_url in candidate_pages:
        if page_url in seen_pages or urlparse(page_url).path.lower().endswith(".zip"):
            continue
        seen_pages.add(page_url)
        try:
            archive_url = find_archive_url_in_page(page_url, key, limiter, timeout)
        except (URLError, DenryuDownloadError):
            continue
        if archive_url:
            return archive_url

    try:
        for option in fetch_denryu_tournament_options(timeout=timeout):
            if option.key == key and option.archive_url:
                return option.archive_url
    except Exception:
        pass

    raise DenryuDownloadError(
        f"{key} の一括ZIPが見つかりません。大会終了前なら live中継ページからダウンロードしてください。"
    )


def find_archive_url_in_page(page_url: str, key: str, limiter: RateLimiter, timeout: float) -> str:
    page_html = decode_text(fetch_bytes(page_url, limiter, timeout))
    parser = LinkExtractor()
    parser.feed(page_html)
    for _title, href in parser.links:
        archive_url = urljoin(page_url, href)
        parsed = urlparse(archive_url)
        if not parsed.path.lower().endswith(".zip"):
            continue
        try:
            if denryu_tournament_key(archive_url) != key:
                continue
        except DenryuDownloadError:
            continue
        return archive_url
    raise DenryuDownloadError(f"一括ZIPがページ内に見つかりません: {page_url}")


def fetch_live_game_list(base_url: str, limiter: RateLimiter, timeout: float) -> tuple[str, list[tuple[str, str]]]:
    list_url = urljoin(base_url, "kifulist.txt")
    list_text = decode_text(fetch_bytes(list_url, limiter, timeout))
    games: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in KIFULIST_RE.finditer(list_text):
        game_id = unquote(match.group(1))
        if game_id in seen:
            continue
        seen.add(game_id)
        game_name = clean_title(match.group(2)).replace("▲", "☗").replace("△", "☖")
        games.append((game_id, game_name))
    if not games:
        raise DenryuDownloadError(f"live棋譜リストに棋譜が見つかりません: {list_url}")
    return list_url, games


def csa_url_for_game(base_url: str, game_id: str) -> str:
    quoted_id = quote(game_id, safe="+._-")
    return urljoin(base_url, f"kifufiles/{quoted_id}.csa")


def download_denryu_kif(
    job: DenryuDownloadJob,
    *,
    log: Callable[[str], None] | None = None,
) -> DenryuDownloadStats:
    source_url = normalize_source_url(job.source_url)
    if job.interval < 0:
        raise DenryuDownloadError("アクセス間隔(秒)は 0 以上を指定してください。")
    if job.timeout <= 0:
        raise DenryuDownloadError("timeout は 0 より大きい値を指定してください。")

    key = denryu_tournament_key(source_url)
    output_dir = job.output_root.expanduser() / key
    limiter = RateLimiter(job.interval)

    if job.use_live_page:
        return download_denryu_from_live(
            key=key,
            source_url=source_url,
            output_dir=output_dir,
            limiter=limiter,
            overwrite=job.overwrite,
            timeout=job.timeout,
            log=log,
        )

    return download_denryu_from_archive(
        key=key,
        source_url=source_url,
        explicit_archive_url=job.archive_url,
        output_dir=output_dir,
        limiter=limiter,
        overwrite=job.overwrite,
        timeout=job.timeout,
        log=log,
    )


def download_denryu_from_live(
    *,
    key: str,
    source_url: str,
    output_dir: Path,
    limiter: RateLimiter,
    overwrite: bool,
    timeout: float,
    log: Callable[[str], None] | None,
) -> DenryuDownloadStats:
    base_url = denryu_base_url(source_url)
    list_url, games = fetch_live_game_list(base_url, limiter, timeout)
    output_dir.mkdir(parents=True, exist_ok=True)

    if log is not None:
        log("source   : live page\n")
        log(f"base url : {base_url}\n")
        log(f"list     : {list_url}\n")
        log(f"files    : {len(games)}\n")
        log(f"output   : {output_dir}\n")

    downloaded = 0
    skipped = 0
    for index, (game_id, _game_name) in enumerate(games, 1):
        destination = output_dir / sanitize_filename(f"{game_id}.csa")
        if destination.exists() and not overwrite:
            skipped += 1
            if log is not None:
                log(f"[{index}/{len(games)}] skip existing: {destination.name}\n")
            continue

        data = fetch_bytes(csa_url_for_game(base_url, game_id), limiter, timeout)
        destination.write_bytes(data)
        downloaded += 1
        if log is not None:
            log(f"[{index}/{len(games)}] downloaded: {destination.name}\n")

    return DenryuDownloadStats(
        tournament=key,
        mode="live",
        source_url=source_url,
        list_url=list_url,
        output_dir=output_dir,
        found=len(games),
        downloaded=downloaded,
        skipped=skipped,
    )


def download_denryu_from_archive(
    *,
    key: str,
    source_url: str,
    explicit_archive_url: str,
    output_dir: Path,
    limiter: RateLimiter,
    overwrite: bool,
    timeout: float,
    log: Callable[[str], None] | None,
) -> DenryuDownloadStats:
    try:
        archive_url = find_archive_url(
            source_url,
            explicit_archive_url=explicit_archive_url,
            limiter=limiter,
            timeout=timeout,
        )
    except DenryuDownloadError as exc:
        if explicit_archive_url or urlparse(source_url).path.lower().endswith(".zip"):
            raise
        if log is not None:
            log(f"archive  : not found ({exc})\n")
            log("fallback : live page\n")
        return download_denryu_from_live(
            key=key,
            source_url=source_url,
            output_dir=output_dir,
            limiter=limiter,
            overwrite=overwrite,
            timeout=timeout,
            log=log,
        )
    archive_filename = filename_from_url(archive_url)

    if log is not None:
        log("source   : official archive\n")
        log(f"archive  : {archive_url}\n")

    tmp_root = Path.cwd() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="denryu-kif-downloader-", dir=tmp_root))
    try:
        archive_path = work_dir / archive_filename
        archive_path.write_bytes(fetch_bytes(archive_url, limiter, timeout))

        output_dir.mkdir(parents=True, exist_ok=True)
        found, downloaded, skipped = extract_zip_archive(
            archive_path=archive_path,
            output_dir=output_dir,
            overwrite=overwrite,
            log=log,
        )
    finally:
        remove_tree_with_retries(work_dir, log=log)
        remove_empty_directory(tmp_root)

    return DenryuDownloadStats(
        tournament=key,
        mode="archive",
        source_url=source_url,
        list_url=archive_url,
        output_dir=output_dir,
        found=found,
        downloaded=downloaded,
        skipped=skipped,
    )


def extract_zip_archive(
    *,
    archive_path: Path,
    output_dir: Path,
    overwrite: bool,
    log: Callable[[str], None] | None,
) -> tuple[int, int, int]:
    with zipfile.ZipFile(archive_path) as archive:
        members = [
            (info, decode_zip_member_name(info))
            for info in archive.infolist()
            if not info.is_dir() and not should_skip_archive_member(decode_zip_member_name(info))
        ]
        if log is not None:
            log("extractor: zipfile\n")
            log(f"files    : {len(members)}\n")
            log(f"output   : {output_dir}\n")

        downloaded = 0
        skipped = 0
        for index, (info, member_name) in enumerate(members, 1):
            destination = safe_archive_member_path(output_dir, member_name)
            if destination.exists() and not overwrite:
                skipped += 1
                if log is not None:
                    log(f"[{index}/{len(members)}] skip existing: {member_name}\n")
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
            downloaded += 1
            if log is not None:
                log(f"[{index}/{len(members)}] extracted: {member_name}\n")

    return len(members), downloaded, skipped


def decode_zip_member_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        decoded_name = info.filename
    else:
        try:
            raw_name = info.filename.encode("cp437")
        except UnicodeEncodeError:
            decoded_name = info.filename
        else:
            for encoding in ("cp932", "shift_jis", "utf-8"):
                try:
                    decoded_name = raw_name.decode(encoding)
                    break
                except UnicodeDecodeError:
                    pass
            else:
                decoded_name = info.filename

    parts = PurePosixPath(decoded_name.replace("\\", "/")).parts
    return "/".join(sanitize_archive_path_part(part) for part in parts)


def should_skip_archive_member(member_name: str) -> bool:
    parts = PurePosixPath(member_name.replace("\\", "/")).parts
    return any(part == "__MACOSX" or part == ".DS_Store" or part.startswith("._") for part in parts)


def sanitize_archive_path_part(part: str) -> str:
    if part in {"", ".", ".."}:
        return part
    return "".join("_" if is_unsafe_archive_path_char(char) else char for char in part)


def is_unsafe_archive_path_char(char: str) -> bool:
    code = ord(char)
    return code < 32 or code == 127 or 0x80 <= code <= 0x9F or char in '<>:"|?*'


def safe_archive_member_path(output_dir: Path, member_name: str) -> Path:
    member_path = PurePosixPath(member_name.replace("\\", "/"))
    if member_path.is_absolute():
        raise DenryuDownloadError(f"アーカイブ内に絶対パスがあります: {member_name}")

    parts = [part for part in member_path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise DenryuDownloadError(f"アーカイブ内のパスが不正です: {member_name}")

    destination = output_dir.joinpath(*parts)
    try:
        destination.resolve().relative_to(output_dir.resolve())
    except ValueError as exc:
        raise DenryuDownloadError(f"アーカイブ内のパスが出力フォルダ外を指しています: {member_name}") from exc
    return destination


def sanitize_filename(value: str) -> str:
    filename = "".join("_" if char in UNSAFE_FILENAME_CHARS or ord(char) < 32 else char for char in value)
    if not filename:
        raise DenryuDownloadError("ファイル名を決定できません。")
    return filename


def filename_from_url(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    return sanitize_filename(name)
