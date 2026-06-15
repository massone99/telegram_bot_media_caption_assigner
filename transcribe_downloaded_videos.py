#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


INPUT_DEFAULT = "downloads"
MODEL_DEFAULT = "medium"
LANGUAGE_DEFAULT = "en"
VIDEO_EXTENSIONS = {
    ".3gp",
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionFailure:
    media_path: Path
    error: str


def import_faster_whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper not found. Install it first: python -m pip install faster-whisper"
        ) from exc
    return WhisperModel


def find_media_files(input_dir: Path) -> list[Path]:
    if input_dir.is_file() and input_dir.suffix.lower() in VIDEO_EXTENSIONS:
        return [input_dir]
    if not input_dir.is_dir():
        raise SystemExit(f"Input path not found: {input_dir}")
    return sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def transcript_paths(media_path: Path, transcript_dir: Path | None, input_root: Path) -> tuple[Path, Path, Path]:
    if transcript_dir is None:
        base = media_path.with_suffix("")
    else:
        if input_root.is_file():
            relative = media_path.name
        else:
            relative = media_path.relative_to(input_root)
        base = (transcript_dir / relative).with_suffix("")
        base.parent.mkdir(parents=True, exist_ok=True)

    return (
        base.with_suffix(".txt"),
        base.with_suffix(".srt"),
        base.with_suffix(".json"),
    )


def format_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_txt(path: Path, segments: Iterable[TranscriptSegment]) -> None:
    text = "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def write_srt(path: Path, segments: Iterable[TranscriptSegment]) -> None:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}",
                    text,
                ]
            )
        )
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def write_json(path: Path, media_path: Path, segments: list[TranscriptSegment], info) -> None:
    payload = {
        "source": str(media_path),
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "segments": [asdict(segment) for segment in segments],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def should_skip(outputs: Iterable[Path], force: bool) -> bool:
    return not force and all(path.exists() for path in outputs)


def expected_outputs(paths: tuple[Path, Path, Path], write_json: bool) -> tuple[Path, ...]:
    txt_path, srt_path, json_path = paths
    if write_json:
        return txt_path, srt_path, json_path
    return txt_path, srt_path


def transcribe_file(model, media_path: Path, args) -> tuple[list[TranscriptSegment], object]:
    language = None if args.language.lower() == "auto" else args.language
    segments_iter, info = model.transcribe(
        str(media_path),
        language=language,
        task="transcribe",
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        condition_on_previous_text=args.condition_on_previous_text,
    )
    segments = [
        TranscriptSegment(
            start=float(segment.start),
            end=float(segment.end),
            text=segment.text.strip(),
        )
        for segment in segments_iter
    ]
    return segments, info


def print_transcription_failures(failures: list[TranscriptionFailure]) -> None:
    if not failures:
        return
    print("\nFailed transcriptions:", file=sys.stderr)
    for failure in failures:
        print(f"  {failure.media_path}: {failure.error}", file=sys.stderr)


def transcribe_all(args) -> int:
    input_path = Path(args.input)
    transcript_dir = Path(args.transcript_dir) if args.transcript_dir else None
    media_files = find_media_files(input_path)
    if not media_files:
        print(f"No media files found in {input_path}", file=sys.stderr)
        return 1

    selected = media_files[: args.limit] if args.limit else media_files
    outputs_by_file = [
        (media_path, transcript_paths(media_path, transcript_dir, input_path))
        for media_path in selected
    ]
    pending = [
        (media_path, paths)
        for media_path, paths in outputs_by_file
        if not should_skip(expected_outputs(paths, args.write_json), args.force)
    ]

    print(f"Found {len(media_files)} media file(s). Pending: {len(pending)}")
    if args.dry_run:
        for media_path, paths in outputs_by_file:
            outputs = expected_outputs(paths, args.write_json)
            status = "skip" if should_skip(outputs, args.force) else "pending"
            print(f"{status}: {media_path}")
        return 0

    if not pending:
        return 0

    WhisperModel = import_faster_whisper()
    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        cpu_threads=args.cpu_threads,
    )

    failures: list[TranscriptionFailure] = []
    for index, (media_path, (txt_path, srt_path, json_path)) in enumerate(pending, start=1):
        print(f"[{index}/{len(pending)}] {media_path}")
        try:
            segments, info = transcribe_file(model, media_path, args)
            write_txt(txt_path, segments)
            write_srt(srt_path, segments)
            if args.write_json:
                write_json(json_path, media_path, segments, info)
        except Exception as exc:
            failure = TranscriptionFailure(media_path=media_path, error=str(exc))
            failures.append(failure)
            print(f"Failed transcription: {media_path}: {exc}", file=sys.stderr)

    print_transcription_failures(failures)
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe downloaded videos with faster-whisper."
    )
    parser.add_argument(
        "--input",
        default=INPUT_DEFAULT,
        help=f"Downloads folder or one media file. Default: {INPUT_DEFAULT}",
    )
    parser.add_argument(
        "--transcript-dir",
        default=None,
        help="Write transcripts here, mirroring category folders. Default: next to each video.",
    )
    parser.add_argument(
        "--model",
        default=MODEL_DEFAULT,
        help=f"faster-whisper model name. Default: {MODEL_DEFAULT}",
    )
    parser.add_argument(
        "--language",
        default=LANGUAGE_DEFAULT,
        help=f"Language code, or 'auto'. Default: {LANGUAGE_DEFAULT}",
    )
    parser.add_argument("--device", default="auto", help="auto, cuda, cpu. Default: auto")
    parser.add_argument(
        "--compute-type",
        default="auto",
        help="auto, float16, int8_float16, int8, float32. Default: auto",
    )
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size. Default: 5")
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=0,
        help="CPU threads. 0 lets faster-whisper choose. Default: 0",
    )
    parser.add_argument(
        "--no-vad-filter",
        dest="vad_filter",
        action="store_false",
        help="Disable voice activity filtering.",
    )
    parser.set_defaults(vad_filter=True)
    parser.add_argument(
        "--condition-on-previous-text",
        action="store_true",
        help="Use previous text as prompt for next chunk. Can help continuity, can increase drift.",
    )
    parser.add_argument("--write-json", action="store_true", help="Also write segment metadata JSON.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .txt/.srt transcripts.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N files.")
    parser.add_argument("--dry-run", action="store_true", help="Print files that would be transcribed.")
    return parser.parse_args()


def main() -> int:
    return transcribe_all(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
