#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from exercise_doc_core import (
    build_document_from_transcript,
    discover_transcripts,
    extract_screenshot,
    manifest_for_document,
    safe_slug,
    screenshot_cues,
    screenshot_filename,
    write_docx_document,
    write_manifest,
    write_markdown_document,
    write_pdf_document,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build exercise documents from Whisper JSON transcripts and video screenshots."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="downloads",
        help="Transcript .json file or directory to scan recursively.",
    )
    parser.add_argument(
        "--media-root",
        help="Optional media directory used when transcript JSON source paths are stale.",
    )
    parser.add_argument(
        "--output-dir",
        default="exercise_docs",
        help="Directory for documents, manifests, and extracted images.",
    )
    parser.add_argument(
        "--screenshots-per-block",
        type=int,
        default=0,
        help="Fixed screenshot count per block. Use 0 for automatic duration-based count.",
    )
    parser.add_argument(
        "--seconds-per-screenshot",
        type=int,
        default=45,
        help="Automatic mode ratio: one screenshot every N seconds.",
    )
    parser.add_argument(
        "--max-screenshots-per-block",
        type=int,
        default=12,
        help="Maximum screenshots per block in automatic mode. Use 0 for no limit.",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Run ffmpeg and write screenshot image files.",
    )
    parser.add_argument(
        "--overwrite-images",
        action="store_true",
        help="Overwrite existing screenshot files.",
    )
    parser.add_argument(
        "--ffmpeg-bin",
        default="ffmpeg",
        help="ffmpeg executable path.",
    )
    parser.add_argument(
        "--split-on-pauses",
        action="store_true",
        help="Split one transcript into multiple exercise blocks when long silent gaps exist.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=12.0,
        help="Silent gap used by --split-on-pauses.",
    )
    parser.add_argument(
        "--docx",
        action="store_true",
        help="Also write .docx files. Requires python-docx.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also write .pdf files. Requires reportlab.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit transcript count for testing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned cues without writing documents or screenshots.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    media_root = Path(args.media_root) if args.media_root else None

    try:
        transcripts = discover_transcripts(input_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.limit:
        transcripts = transcripts[: args.limit]
    if not transcripts:
        print(f"No JSON transcripts found in {input_path}", file=sys.stderr)
        return 1

    failures: list[str] = []
    for transcript_path in transcripts:
        try:
            document = build_document_from_transcript(
                transcript_path,
                media_root=media_root,
                split_on_pauses=args.split_on_pauses,
                pause_seconds=args.pause_seconds,
            )
            slug = safe_slug(document.title)
            asset_dir = output_dir / f"{slug}_assets"
            image_paths = {}
            cues_by_block = {}

            for block_index, block in enumerate(document.blocks, start=1):
                cues = screenshot_cues(
                    block,
                    count=args.screenshots_per_block,
                    seconds_per_screenshot=args.seconds_per_screenshot,
                    max_count=args.max_screenshots_per_block,
                )
                cues_by_block[block_index] = cues
                for cue_index, cue in enumerate(cues, start=1):
                    image_path = asset_dir / screenshot_filename(block_index, cue_index, cue)
                    image_paths[(block_index, cue_index)] = image_path.relative_to(output_dir)
                    if args.dry_run:
                        print(
                            f"{transcript_path}: block={block_index} cue={cue_index} "
                            f"time={cue.time:.3f}s reason={cue.reason} score={cue.score}"
                        )
                        continue
                    if args.extract:
                        if document.media_path is None:
                            raise RuntimeError(f"Media file not found for transcript: {transcript_path}")
                        extract_screenshot(
                            document.media_path,
                            output_dir / image_paths[(block_index, cue_index)],
                            cue.time,
                            ffmpeg_bin=args.ffmpeg_bin,
                            overwrite=args.overwrite_images,
                        )

            if args.dry_run:
                continue

            write_markdown_document(output_dir / f"{slug}.md", document, image_paths)
            write_manifest(
                output_dir / f"{slug}_manifest.json",
                manifest_for_document(document, cues_by_block, image_paths),
            )
            if args.docx:
                write_docx_document(output_dir / f"{slug}.docx", document, image_paths)
            if args.pdf:
                write_pdf_document(output_dir / f"{slug}.pdf", document, image_paths)
            print(f"Wrote: {output_dir / f'{slug}.md'}")
        except Exception as exc:
            failures.append(f"{transcript_path}: {exc}")
            print(f"Failed: {transcript_path}: {exc}", file=sys.stderr)

    if failures:
        print("\nFailures:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
