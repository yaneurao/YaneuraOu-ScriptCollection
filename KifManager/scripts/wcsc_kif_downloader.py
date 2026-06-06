#!/usr/bin/env python3
from __future__ import annotations

import gc
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = "YaneuraOu-KifManager-WCSC-Kif-Downloader/1.0"
HISTORIC_KIF_INDEX_URL = "http://www2.computer-shogi.org/kifu/kifu.html"
KIF_URL_RE = re.compile(r"https?://[^\s]+?\.(?:csa|kif|kifu)(?:\?[^\s]*)?", re.IGNORECASE)
LEGACY_PATH_RE = re.compile(
    r"(?:https?://[^\s'\"<>]+|[A-Za-z0-9_./%+\-]+)\.(?:csa|kif|kifu|html)",
    re.IGNORECASE,
)
UNSAFE_FILENAME_CHARS = '<>:"/\\|?*'


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class WcscDownloadJob:
    tournament: str
    output_root: Path
    interval: float
    overwrite: bool = False
    timeout: float = 60.0
    use_live_page: bool = False


@dataclass(frozen=True)
class WcscDownloadStats:
    tournament: str
    list_url: str
    output_dir: Path
    found: int
    downloaded: int
    skipped: int


@dataclass(frozen=True)
class LhaMember:
    archive_name: str
    output_name: str


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
        log(f"warning      : failed to remove tmp dir: {path}: {last_error}\n")
    return False


def remove_empty_directory(path: Path) -> None:
    with suppress(OSError):
        path.rmdir()


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        for name, value in attrs:
            name = name.lower()
            if tag == "a" and name == "href" and value:
                self.hrefs.append(value)
            elif tag in {"frame", "iframe"} and name == "src" and value:
                self.hrefs.append(value)


def normalize_wcsc_name(value: str) -> tuple[str, str]:
    stripped = value.strip()
    if re.fullmatch(r"wcso1", stripped, re.IGNORECASE):
        return "wcso1", "http://live4.computer-shogi.org/wcso1/"

    match = re.fullmatch(r"wcsc(\d+)", stripped, re.IGNORECASE)
    if not match:
        raise DownloadError("大会名は WCSC36 または WCSO1 のように指定してください。")

    number = int(match.group(1))
    if number < 1:
        raise DownloadError("WCSC番号は1以上を指定してください。")
    if number == 30:
        raise DownloadError(
            "WCSC30は中止になりましたが、オンライン大会としてWCSO1が開催されました。"
            "WCSO1と指定すればその棋譜をダウンロードできます。"
        )
    return f"wcsc{number}", default_base_url(number)


def default_base_url(number: int) -> str:
    if number <= 16:
        return HISTORIC_KIF_INDEX_URL
    if number >= 36:
        return f"https://www.computer-shogi.org/live/wcsc{number}/"
    if number >= 25:
        return f"http://live4.computer-shogi.org/wcsc{number}/"
    return f"http://live2.computer-shogi.org/wcsc{number}/"


def live_base_url(normalized_name: str) -> str:
    if normalized_name == "wcso1":
        return "http://live4.computer-shogi.org/wcso1/"

    match = re.fullmatch(r"wcsc(\d+)", normalized_name)
    if not match:
        raise DownloadError("大会名は WCSC36 または WCSO1 のように指定してください。")

    number = int(match.group(1))
    if number <= 16:
        raise DownloadError("WCSC16以前はlive中継ページからの棋譜ダウンロードに対応していません。")
    return default_base_url(number)


def fetch_bytes(url: str, limiter: RateLimiter, timeout: float) -> bytes:
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


def fetch_list_text(base_url: str, limiter: RateLimiter, timeout: float) -> tuple[str, str]:
    direct_url = urljoin(base_url, "list.txt")
    try:
        return direct_url, decode_text(fetch_bytes(direct_url, limiter, timeout))
    except URLError:
        pass

    html = decode_text(fetch_bytes(base_url, limiter, timeout))
    parser = LinkExtractor()
    parser.feed(html)
    for href in parser.hrefs:
        if Path(urlparse(href).path).name.lower() == "list.txt":
            list_url = urljoin(base_url, href)
            return list_url, decode_text(fetch_bytes(list_url, limiter, timeout))

    raise DownloadError(f"棋譜リストが見つかりません: {base_url}")


def fetch_kifu_urls(base_url: str, limiter: RateLimiter, timeout: float) -> tuple[str, list[str]]:
    try:
        list_url, list_text = fetch_list_text(base_url, limiter, timeout)
        urls = normalize_list_kifu_urls(extract_kifu_urls(list_text), base_url)
        if urls:
            return list_url, urls
    except URLError:
        pass
    except DownloadError:
        pass

    return fetch_legacy_kifu_urls(base_url, limiter, timeout)


def download_wcsc_archive_from_index(
    *,
    normalized_name: str,
    output_dir: Path,
    limiter: RateLimiter,
    overwrite: bool,
    timeout: float,
    log: Callable[[str], None] | None,
) -> WcscDownloadStats:
    archive_url = fetch_wcsc_archive_url(normalized_name, limiter, timeout)
    archive_filename = filename_from_url(archive_url)

    if log is not None:
        log("source       : archive index\n")
        log(f"archive index: {HISTORIC_KIF_INDEX_URL}\n")
        log(f"archive      : {archive_url}\n")

    tmp_root = Path.cwd() / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="wcsc-kif-downloader-", dir=tmp_root))
    try:
        archive_path = work_dir / archive_filename
        archive_path.write_bytes(fetch_bytes(archive_url, limiter, timeout))

        output_dir.mkdir(parents=True, exist_ok=True)
        found, downloaded, skipped = extract_archive(
            archive_path=archive_path,
            output_dir=output_dir,
            work_dir=work_dir,
            overwrite=overwrite,
            log=log,
        )

        return WcscDownloadStats(
            tournament=normalized_name,
            list_url=archive_url,
            output_dir=output_dir,
            found=found,
            downloaded=downloaded,
            skipped=skipped,
        )
    finally:
        remove_tree_with_retries(work_dir, log=log)
        remove_empty_directory(tmp_root)


def extract_archive(
    *,
    archive_path: Path,
    output_dir: Path,
    work_dir: Path,
    overwrite: bool,
    log: Callable[[str], None] | None,
) -> tuple[int, int, int]:
    suffix = archive_path.suffix.lower()
    if suffix == ".zip":
        return extract_zip_archive(archive_path, output_dir, overwrite, log)
    if suffix == ".lzh":
        return extract_lha_archive(
            archive_path=archive_path,
            output_dir=output_dir,
            work_dir=work_dir,
            overwrite=overwrite,
            log=log,
        )
    raise DownloadError(f"未対応のアーカイブ形式です: {archive_path.name}")


def extract_zip_archive(
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
            log("extractor   : zipfile\n")
            log(f"files        : {len(members)}\n")
            log(f"output       : {output_dir}\n")

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


def extract_lha_archive(
    *,
    archive_path: Path,
    output_dir: Path,
    work_dir: Path,
    overwrite: bool,
    log: Callable[[str], None] | None,
) -> tuple[int, int, int]:
    try:
        return extract_lha_archive_with_lhafile(archive_path, output_dir, overwrite, log)
    except Exception as exc:
        if not should_try_external_lha_extractor(exc):
            raise
        if log is not None:
            log(f"lhafile     : {exc}\n")
            log("fallback    : external extractor\n")
        return extract_lha_archive_with_external(archive_path, output_dir, work_dir, overwrite, log)


def extract_lha_archive_with_lhafile(
    archive_path: Path,
    output_dir: Path,
    overwrite: bool,
    log: Callable[[str], None] | None,
) -> tuple[int, int, int]:
    members = list_lha_members(archive_path)
    if log is not None:
        log("extractor   : lhafile\n")
        log(f"files        : {len(members)}\n")
        log(f"output       : {output_dir}\n")

    downloaded = 0
    skipped = 0
    for index, member in enumerate(members, 1):
        destination = safe_archive_member_path(output_dir, member.output_name)
        if destination.exists() and not overwrite:
            skipped += 1
            if log is not None:
                log(f"[{index}/{len(members)}] skip existing: {member.output_name}\n")
            continue

        data = read_lha_member(archive_path, member.archive_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        downloaded += 1
        if log is not None:
            log(f"[{index}/{len(members)}] extracted: {member.output_name}\n")

    return len(members), downloaded, skipped


def should_try_external_lha_extractor(exc: Exception) -> bool:
    message = str(exc).lower()
    return "unsupported" in message or "-lh4-" in message


def extract_lha_archive_with_external(
    archive_path: Path,
    output_dir: Path,
    work_dir: Path,
    overwrite: bool,
    log: Callable[[str], None] | None,
) -> tuple[int, int, int]:
    staging_dir = work_dir / "external-extract"
    staging_dir.mkdir(parents=True, exist_ok=True)
    command = find_external_lha_extract_command(archive_path, staging_dir)
    if command is None:
        raise DownloadError(
            "このLZHアーカイブは lhafile では展開できない形式です。"
            "7-Zipをインストールして 7z をPATHに追加するか、bsdtar/unar を利用できるようにしてください。"
        )

    if log is not None:
        log(f"command     : {' '.join(command)}\n")
    completed = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise DownloadError(f"外部展開コマンドが失敗しました: {detail}")

    files = sorted(
        path
        for path in staging_dir.rglob("*")
        if path.is_file() and not should_skip_archive_member(path.relative_to(staging_dir).as_posix())
    )
    if log is not None:
        log(f"files        : {len(files)}\n")
        log(f"output       : {output_dir}\n")

    downloaded = 0
    skipped = 0
    for index, source in enumerate(files, 1):
        member_name = source.relative_to(staging_dir).as_posix()
        destination = safe_archive_member_path(output_dir, member_name)
        if destination.exists() and not overwrite:
            skipped += 1
            if log is not None:
                log(f"[{index}/{len(files)}] skip existing: {member_name}\n")
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        downloaded += 1
        if log is not None:
            log(f"[{index}/{len(files)}] extracted: {member_name}\n")

    return len(files), downloaded, skipped


def find_external_lha_extract_command(archive_path: Path, output_dir: Path) -> list[str] | None:
    for executable in external_lha_extractor_candidates():
        if Path(executable).name.lower().startswith("7z"):
            return [executable, "x", "-y", f"-o{output_dir}", str(archive_path)]
        if Path(executable).name.lower() == "bsdtar":
            return [executable, "-xf", str(archive_path), "-C", str(output_dir)]
        if Path(executable).name.lower() == "unar":
            return [executable, "-quiet", "-force-overwrite", "-output-directory", str(output_dir), str(archive_path)]
    return None


def external_lha_extractor_candidates() -> list[str]:
    names = ["7z", "7za", "7zz", "bsdtar", "unar"]
    candidates = [path for name in names if (path := shutil.which(name))]
    if os.name == "nt":
        for path in (Path(os.environ.get("ProgramFiles", "")) / "7-Zip" / "7z.exe",
                     Path(os.environ.get("ProgramFiles(x86)", "")) / "7-Zip" / "7z.exe"):
            if path.is_file():
                candidates.append(str(path))
    return deduplicate(candidates)


def fetch_wcsc_archive_url(normalized_name: str, limiter: RateLimiter, timeout: float) -> str:
    index_html = decode_text(fetch_bytes(HISTORIC_KIF_INDEX_URL, limiter, timeout))
    parser = LinkExtractor()
    parser.feed(index_html)

    candidates = wcsc_archive_filenames(normalized_name)
    for href in parser.hrefs:
        name = Path(urlparse(href).path).name.lower()
        if name in candidates:
            return urljoin(HISTORIC_KIF_INDEX_URL, href)

    raise DownloadError(f"{normalized_name.upper()} の棋譜アーカイブが見つかりません: {HISTORIC_KIF_INDEX_URL}")


def wcsc_archive_filenames(normalized_name: str) -> set[str]:
    if normalized_name == "wcso1":
        return {"wcso1_kifu.zip", "wcso1.zip"}

    match = re.fullmatch(r"wcsc(\d+)", normalized_name)
    if not match:
        raise DownloadError("大会名は WCSC36 または WCSO1 のように指定してください。")

    number = int(match.group(1))
    if 1 <= number <= 12:
        return {f"kifu{number}.lzh"}
    if 13 <= number <= 16:
        return {f"wcsc{number}_kifu.lzh"}
    if number == 17:
        return {"wcsc17.zip", "wcsc17_kifu.zip"}
    return {f"wcsc{number}_kifu.zip", f"wcsc{number}.zip"}


def import_lhafile_module():
    try:
        import lhafile
    except ImportError as exc:
        raise DownloadError(
            "WCSC16以前のLZH展開には lhafile が必要です。"
            "python3 -m pip install lhafile でインストールしてください。"
        ) from exc
    return lhafile


def list_lha_members(archive_path: Path) -> list[LhaMember]:
    lhafile = import_lhafile_module()
    archive = lhafile.LhaFile(io.BytesIO(archive_path.read_bytes()))
    names = archive.namelist()
    return [
        LhaMember(archive_name=name, output_name=decode_lha_member_name(name))
        for name in names
        if not name.endswith(("/", "\\"))
    ]


def read_lha_member(archive_path: Path, member_name: str) -> bytes:
    lhafile = import_lhafile_module()
    archive = lhafile.LhaFile(io.BytesIO(archive_path.read_bytes()))
    return archive.read(member_name)


def decode_lha_member_name(member_name: str) -> str:
    try:
        raw_name = member_name.encode("ISO-8859-1")
    except UnicodeEncodeError:
        decoded_name = member_name
    else:
        for encoding in ("cp932", "shift_jis", "utf-8"):
            try:
                decoded_name = raw_name.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        else:
            decoded_name = member_name

    parts = PurePosixPath(decoded_name.replace("\\", "/")).parts
    return "/".join(sanitize_archive_path_part(part) for part in parts)


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
        raise DownloadError(f"アーカイブ内に絶対パスがあります: {member_name}")

    parts = [part for part in member_path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise DownloadError(f"アーカイブ内のパスが不正です: {member_name}")

    destination = output_dir.joinpath(*parts)
    try:
        destination.resolve().relative_to(output_dir.resolve())
    except ValueError as exc:
        raise DownloadError(f"アーカイブ内のパスが出力フォルダ外を指しています: {member_name}") from exc
    return destination


def fetch_legacy_kifu_urls(base_url: str, limiter: RateLimiter, timeout: float) -> tuple[str, list[str]]:
    queue: list[str] = [base_url]
    visited: set[str] = set()
    seen_kifu_urls: set[str] = set()
    kifu_urls: list[str] = []
    source_url = base_url

    while queue and len(visited) < 100:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        try:
            html = decode_text(fetch_bytes(page_url, limiter, timeout))
        except URLError:
            continue

        source_url = page_url
        found_urls, next_pages = extract_legacy_urls(page_url, html, base_url)
        for url in found_urls:
            if url in seen_kifu_urls:
                continue
            seen_kifu_urls.add(url)
            kifu_urls.append(url)
        for url in next_pages:
            if url not in visited and url not in queue:
                queue.append(url)

    if not kifu_urls:
        raise DownloadError(f"棋譜URLが見つかりません: {base_url}")
    return source_url, kifu_urls


def extract_legacy_urls(page_url: str, html: str, base_url: str) -> tuple[list[str], list[str]]:
    kifu_urls: list[str] = []
    page_urls: list[str] = []

    candidates = list(LEGACY_PATH_RE.findall(html))
    parser = LinkExtractor()
    parser.feed(html)
    for href in parser.hrefs:
        candidates.append(href)
        if href.lower().startswith("javascript:"):
            candidates.extend(LEGACY_PATH_RE.findall(href))

    for candidate in candidates:
        if candidate.lower().startswith("javascript:"):
            continue

        url = urljoin(page_url, candidate)
        if not url.startswith(base_url):
            continue

        parsed = urlparse(url)
        path_lower = parsed.path.lower()
        if path_lower.endswith((".csa", ".kif", ".kifu")):
            if "/kifu/" in path_lower:
                kifu_urls.append(url)
            continue

        if path_lower.endswith(".html"):
            if "/kifu/" in path_lower:
                kifu_urls.append(url[: -len(".html")] + ".csa")
            elif should_crawl_legacy_page(url, base_url):
                page_urls.append(url)

    return deduplicate(kifu_urls), deduplicate(page_urls)


def should_crawl_legacy_page(url: str, base_url: str) -> bool:
    if not url.startswith(base_url):
        return False
    name = Path(urlparse(url).path).name.lower()
    return name not in {"live.html", "live_s.html", "live_flash.html", "kifujhelp.html"}


def deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def extract_kifu_urls(list_text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in KIF_URL_RE.finditer(list_text):
        url = match.group(0)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def normalize_list_kifu_urls(urls: list[str], base_url: str) -> list[str]:
    base = urlparse(base_url)
    normalized: list[str] = []
    for url in urls:
        absolute_url = urljoin(base_url, url)
        parsed = urlparse(absolute_url)
        if parsed.path.startswith(base.path):
            parsed = parsed._replace(scheme=base.scheme, netloc=base.netloc)
            absolute_url = urlunparse(parsed)
        normalized.append(absolute_url)
    return deduplicate(normalized)


def filename_from_url(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    name = "".join("_" if char in UNSAFE_FILENAME_CHARS else char for char in name)
    if not name:
        raise DownloadError(f"ファイル名を決定できません: {url}")
    return name


def download_wcsc_kif(
    job: WcscDownloadJob,
    *,
    log: Callable[[str], None] | None = None,
) -> WcscDownloadStats:
    normalized_name, _base_url = normalize_wcsc_name(job.tournament)
    if job.interval < 0:
        raise DownloadError("アクセス間隔(秒)は 0 以上を指定してください。")
    if job.timeout <= 0:
        raise DownloadError("timeout は 0 より大きい値を指定してください。")

    output_dir = job.output_root.expanduser() / normalized_name
    limiter = RateLimiter(job.interval)

    if not job.use_live_page:
        return download_wcsc_archive_from_index(
            normalized_name=normalized_name,
            output_dir=output_dir,
            limiter=limiter,
            overwrite=job.overwrite,
            timeout=job.timeout,
            log=log,
        )

    base_url = live_base_url(normalized_name)
    if log is not None:
        log("source   : live page\n")
        log(f"base url : {base_url}\n")

    list_url, urls = fetch_kifu_urls(base_url, limiter, job.timeout)

    output_dir.mkdir(parents=True, exist_ok=True)

    if log is not None:
        log(f"list     : {list_url}\n")
        log(f"files    : {len(urls)}\n")
        log(f"output   : {output_dir}\n")

    downloaded = 0
    skipped = 0
    for index, url in enumerate(urls, 1):
        destination = output_dir / filename_from_url(url)
        if destination.exists() and not job.overwrite:
            skipped += 1
            if log is not None:
                log(f"[{index}/{len(urls)}] skip existing: {destination.name}\n")
            continue

        data = fetch_bytes(url, limiter, job.timeout)
        destination.write_bytes(data)
        downloaded += 1
        if log is not None:
            log(f"[{index}/{len(urls)}] downloaded: {destination.name}\n")

    return WcscDownloadStats(
        tournament=normalized_name,
        list_url=list_url,
        output_dir=output_dir,
        found=len(urls),
        downloaded=downloaded,
        skipped=skipped,
    )
