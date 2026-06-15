#!/usr/bin/env python3
import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


DOCX_DEFAULT = "Joe Gambino MOTW.docx"
OUTPUT_DEFAULT = "downloads"
FAILED_DOWNLOADS_DEFAULT = "failed_downloads.txt"
IGNORED_EXISTING_SUFFIXES = {
    ".description",
    ".info.json",
    ".json",
    ".part",
    ".srt",
    ".temp",
    ".tmp",
    ".txt",
    ".vtt",
}

WORD_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}

URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"youtube\.com/watch\?v=[A-Za-z0-9_-]{11}(?:[&?][^\s<>\"]*)?|"
    r"youtu\.be/[A-Za-z0-9_-]{11}(?:\?[^\s<>\"]*)?|"
    r"youtube\.com/shorts/[A-Za-z0-9_-]{11}(?:\?[^\s<>\"]*)?"
    r")",
    re.IGNORECASE,
)
MOTW_PREFIX_RE = re.compile(r"Movement\s+of\s+the\s+Week\s*:\s*", re.IGNORECASE)


@dataclass
class Video:
    category: str
    title: str
    url: str


@dataclass
class DownloadFailure:
    index: int
    video: Video
    returncode: int


def clean_text(value: str) -> str:
    value = value.replace("\u200b", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def sanitize_path_part(value: str, fallback: str) -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:180] or fallback


def is_category(text: str) -> bool:
    text = clean_text(text)
    if not text or URL_RE.search(text) or MOTW_PREFIX_RE.search(text):
        return False
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and text.upper() == text and len(text) <= 80


def extract_title(text: str) -> str | None:
    text = URL_RE.sub("", clean_text(text))
    if not text:
        return None

    matches = list(MOTW_PREFIX_RE.finditer(text))
    if matches:
        text = text[matches[-1].end() :]

    text = text.replace("_", " ")
    text = re.sub(r"^[\s:;,\-–—]+|[\s:;,\-–—]+$", "", text)
    return clean_text(text) or None


def read_docx_relationships(docx: zipfile.ZipFile) -> dict[str, str]:
    try:
        rel_xml = docx.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}

    root = ET.fromstring(rel_xml)
    relationships: dict[str, str] = {}
    for rel in root.findall("rel:Relationship", REL_NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            relationships[rel_id] = target
    return relationships


def paragraph_text(paragraph: ET.Element) -> str:
    return clean_text("".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)))


def paragraph_hyperlinks(paragraph: ET.Element, relationships: dict[str, str]) -> list[str]:
    links: list[str] = []
    for hyperlink in paragraph.findall(".//w:hyperlink", WORD_NS):
        rel_id = hyperlink.attrib.get(f"{{{WORD_NS['r']}}}id")
        if rel_id and rel_id in relationships:
            links.append(relationships[rel_id])
    return links


def clean_url(url: str) -> str:
    return url.rstrip(".,);]")


def youtube_urls(urls: Iterable[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        match = URL_RE.search(clean_url(raw_url))
        if not match:
            continue
        url = clean_url(match.group(0))
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def extract_videos(docx_path: Path) -> list[Video]:
    videos: list[Video] = []
    current_category = "Uncategorized"
    pending_title: str | None = None

    with zipfile.ZipFile(docx_path) as docx:
        relationships = read_docx_relationships(docx)
        document = ET.fromstring(docx.read("word/document.xml"))

        for paragraph in document.findall(".//w:p", WORD_NS):
            text = paragraph_text(paragraph)
            embedded_urls = youtube_urls(match.group(0) for match in URL_RE.finditer(text))
            linked_urls = youtube_urls(paragraph_hyperlinks(paragraph, relationships))

            if is_category(text) and not embedded_urls and not linked_urls:
                current_category = text
                pending_title = None
                continue

            urls_in_paragraph = embedded_urls[:]
            for linked_url in linked_urls:
                if linked_url not in urls_in_paragraph:
                    urls_in_paragraph.append(linked_url)

            if not urls_in_paragraph:
                title = extract_title(text)
                if title:
                    pending_title = title
                continue

            position = 0
            for match in URL_RE.finditer(text):
                url = clean_url(match.group(0))
                if url not in urls_in_paragraph:
                    continue
                before = text[position : match.start()]
                title = extract_title(before) or pending_title
                if not title:
                    title = f"Movement {len(videos) + 1:03d}"
                videos.append(Video(current_category, title, url))
                pending_title = None
                position = match.end()

            for url in linked_urls:
                if url in embedded_urls:
                    continue
                title = extract_title(text) or pending_title
                if not title:
                    title = f"Movement {len(videos) + 1:03d}"
                videos.append(Video(current_category, title, url))
                pending_title = None

            trailing = URL_RE.split(text)[-1] if embedded_urls else ""
            title = extract_title(trailing)
            if title:
                pending_title = title

    return videos


def print_summary(videos: list[Video]) -> None:
    current_category = None
    for index, video in enumerate(videos, start=1):
        if video.category != current_category:
            current_category = video.category
            print(f"\n{current_category}")
        print(f"  {index:03d}. {video.title} -> {video.url}")


def ensure_yt_dlp() -> str:
    executable = shutil.which("yt-dlp")
    if executable:
        return executable
    raise SystemExit(
        "yt-dlp not found. Install it first, for example: python -m pip install yt-dlp"
    )


def video_output_stem(video: Video, index: int, output_dir: Path) -> Path:
    category_dir = output_dir / sanitize_path_part(video.category, "Uncategorized")
    filename = sanitize_path_part(f"{index:03d} - {video.title}", f"video-{index:03d}")
    return category_dir / filename


def existing_downloads_for_video(video: Video, index: int, output_dir: Path) -> list[Path]:
    output_stem = video_output_stem(video, index, output_dir)
    if not output_stem.parent.is_dir():
        return []
    existing = []
    for path in output_stem.parent.glob(output_stem.name + ".*"):
        suffixes = {suffix.lower() for suffix in path.suffixes}
        if suffixes & IGNORED_EXISTING_SUFFIXES:
            continue
        if path.is_file() and path.stat().st_size > 0:
            existing.append(path)
    return sorted(existing)


def is_video_already_downloaded(video: Video, index: int, output_dir: Path) -> bool:
    return bool(existing_downloads_for_video(video, index, output_dir))


def download_videos(
    videos: list[Video],
    output_dir: Path,
    archive_path: Path | None = None,
    skip_existing: bool = True,
) -> list[DownloadFailure]:
    yt_dlp = ensure_yt_dlp()
    output_dir.mkdir(parents=True, exist_ok=True)

    failures: list[DownloadFailure] = []
    for index, video in enumerate(videos, start=1):
        output_stem = video_output_stem(video, index, output_dir)
        category_dir = output_stem.parent
        category_dir.mkdir(parents=True, exist_ok=True)
        existing = existing_downloads_for_video(video, index, output_dir)
        if skip_existing and existing:
            print(f"[{index}/{len(videos)}] Skip existing: {existing[0]}")
            continue

        output_template = str(output_stem.with_name(output_stem.name + ".%(ext)s"))

        command = [
            yt_dlp,
            "--no-playlist",
            "--continue",
            "-o",
            output_template,
            video.url,
        ]
        if archive_path is not None:
            command[3:3] = ["--download-archive", str(archive_path)]
        print(f"[{index}/{len(videos)}] {video.category} / {video.title}")
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            failure = DownloadFailure(index=index, video=video, returncode=result.returncode)
            failures.append(failure)
            print(
                "Failed download: "
                f"{failure.index:03d}. {video.category} / {video.title} -> {video.url} "
                f"(exit {result.returncode})",
                file=sys.stderr,
            )

    return failures


def print_download_failures(failures: list[DownloadFailure]) -> None:
    if not failures:
        return
    print("\nFailed downloads:", file=sys.stderr)
    for failure in failures:
        video = failure.video
        print(
            f"  {failure.index:03d}. {video.category} / {video.title} -> {video.url} "
            f"(exit {failure.returncode})",
            file=sys.stderr,
        )


def format_download_failures(failures: list[DownloadFailure]) -> str:
    if not failures:
        return "No failed downloads.\n"
    lines = ["Failed downloads:"]
    for failure in failures:
        video = failure.video
        lines.append(
            f"{failure.index:03d}\t{video.category}\t{video.title}\t{video.url}\t"
            f"exit={failure.returncode}"
        )
    return "\n".join(lines) + "\n"


def save_download_failures(failures: list[DownloadFailure], failed_file: Path) -> None:
    failed_file.parent.mkdir(parents=True, exist_ok=True)
    failed_file.write_text(format_download_failures(failures), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download YouTube videos from Joe Gambino MOTW.docx into category folders."
    )
    parser.add_argument("--docx", default=DOCX_DEFAULT, help=f"DOCX file. Default: {DOCX_DEFAULT}")
    parser.add_argument(
        "--output",
        default=OUTPUT_DEFAULT,
        help=f"Download folder. Default: {OUTPUT_DEFAULT}",
    )
    parser.add_argument(
        "--archive",
        default=None,
        help=(
            "Optional yt-dlp archive file. Not enabled by default because a global "
            "archive skips duplicate URLs that should appear in multiple category folders."
        ),
    )
    parser.add_argument(
        "--failed-file",
        default=None,
        help=f"Write failed downloads here. Default: <output>/{FAILED_DOWNLOADS_DEFAULT}",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Redownload even if the expected output file already exists.",
    )
    parser.set_defaults(skip_existing=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print detected categories/videos. Do not download.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    docx_path = Path(args.docx)
    output_dir = Path(args.output)
    archive_path = Path(args.archive) if args.archive else None
    failed_file = Path(args.failed_file) if args.failed_file else output_dir / FAILED_DOWNLOADS_DEFAULT

    if not docx_path.is_file():
        print(f"DOCX not found: {docx_path}", file=sys.stderr)
        return 2

    videos = extract_videos(docx_path)
    if not videos:
        print(f"No YouTube videos found in {docx_path}", file=sys.stderr)
        return 1

    print(f"Found {len(videos)} YouTube videos in {docx_path}")
    print_summary(videos)

    if args.dry_run:
        return 0

    failures = download_videos(
        videos,
        output_dir,
        archive_path=archive_path,
        skip_existing=args.skip_existing,
    )
    save_download_failures(failures, failed_file)
    if failures:
        print_download_failures(failures)
        print(f"Failed download list saved to {failed_file}", file=sys.stderr)
        print(f"Done with {len(failures)} failed download(s).", file=sys.stderr)
        return 1

    print(f"Failed download list saved to {failed_file}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
