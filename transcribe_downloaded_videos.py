#!/usr/bin/env python3
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ctypes
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


INPUT_DEFAULT = "downloads"
MODEL_DEFAULT = "medium"
LANGUAGE_DEFAULT = "en"
DEVICE_DEFAULT = "cuda"
COMPUTE_TYPE_DEFAULT = "float16"
PREFETCH_MODELS_DEFAULT = ("medium", "large-v3")
WORKERS_DEFAULT = 1
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


@dataclass
class TranscriptionOptions:
    model: str = MODEL_DEFAULT
    language: str = LANGUAGE_DEFAULT
    device: str = DEVICE_DEFAULT
    compute_type: str = COMPUTE_TYPE_DEFAULT
    beam_size: int = 5
    cpu_threads: int = 0
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    write_json: bool = False
    force: bool = False
    workers: int = WORKERS_DEFAULT
    model_cache_dir: str | None = None


@dataclass
class TranscriptionPlanItem:
    media_path: Path
    paths: tuple[Path, Path, Path]
    outputs: tuple[Path, ...]
    status: str


LogCallback = Callable[[str], None]
StatusCallback = Callable[[Path, str], None]
StopCallback = Callable[[], bool]


def import_faster_whisper():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper not found. Install it first: python -m pip install faster-whisper"
        ) from exc
    return WhisperModel


def import_download_model():
    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:
        raise SystemExit(
            "faster-whisper not found. Install it first: python -m pip install faster-whisper"
        ) from exc
    return download_model


def cuda_device_count() -> int:
    try:
        import ctranslate2
    except ImportError:
        return 0
    return ctranslate2.get_cuda_device_count()


def cuda_runtime_libraries() -> list[Path]:
    package_roots = []
    for raw_path in sys.path:
        if not raw_path:
            continue
        nvidia_root = Path(raw_path) / "nvidia"
        if nvidia_root.is_dir():
            package_roots.append(nvidia_root)

    library_names = (
        "cuda_nvrtc/lib/libnvrtc.so.12",
        "cublas/lib/libcublas.so.12",
        "cublas/lib/libcublasLt.so.12",
        "cudnn/lib/libcudnn.so.9",
        "cudnn/lib/libcudnn_ops.so.9",
        "cudnn/lib/libcudnn_cnn.so.9",
        "cudnn/lib/libcudnn_adv.so.9",
    )

    libraries: list[Path] = []
    for root in package_roots:
        for library_name in library_names:
            path = root / library_name
            if path.exists() and path not in libraries:
                libraries.append(path)
    return libraries


def preload_cuda_runtime(log: LogCallback | None = None) -> None:
    libraries = cuda_runtime_libraries()
    if not any(path.name == "libcublas.so.12" for path in libraries):
        raise SystemExit(
            "CUDA runtime libraries not found in this venv. Run: "
            "python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
        )

    library_dirs = sorted({str(path.parent) for path in libraries})
    current_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        library_dirs + ([current_library_path] if current_library_path else [])
    )

    for path in libraries:
        try:
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            raise SystemExit(f"Could not load CUDA runtime library {path}: {exc}") from exc
    emit_log(log, f"CUDA runtime libraries loaded: {', '.join(library_dirs)}")


def emit_log(log: LogCallback | None, message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    if log:
        log(line)
        return
    print(line, flush=True)


def download_models(
    model_names: Iterable[str],
    model_cache_dir: str | None = None,
    log: LogCallback | None = None,
) -> list[Path]:
    download_model = import_download_model()
    downloaded: list[Path] = []
    for model_name in model_names:
        started = time.monotonic()
        emit_log(log, f"Downloading model: {model_name}")
        path = Path(download_model(model_name, cache_dir=model_cache_dir))
        downloaded.append(path)
        emit_log(
            log,
            f"Model ready: {model_name} -> {path} ({time.monotonic() - started:.1f}s)",
        )
    return downloaded


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
            try:
                relative = media_path.relative_to(input_root)
            except ValueError:
                relative = media_path.name
        base = (transcript_dir / relative).with_suffix("")
        base.parent.mkdir(parents=True, exist_ok=True)

    return (
        transcript_output_path(base, ".txt"),
        transcript_output_path(base, ".srt"),
        transcript_output_path(base, ".json"),
    )


def transcript_output_path(base: Path, suffix: str) -> Path:
    return base.with_name(f"{base.name}{suffix}")


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


def transcription_options_from_args(args) -> TranscriptionOptions:
    return TranscriptionOptions(
        model=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        cpu_threads=args.cpu_threads,
        vad_filter=args.vad_filter,
        condition_on_previous_text=args.condition_on_previous_text,
        write_json=args.write_json,
        force=args.force,
        workers=args.workers,
        model_cache_dir=args.model_cache_dir,
    )


def transcribe_plan_for_files(
    media_files: Iterable[Path],
    options: TranscriptionOptions,
    transcript_dir: Path | None = None,
    input_root: Path | None = None,
    limit: int = 0,
) -> list[TranscriptionPlanItem]:
    selected = [Path(media_path) for media_path in media_files]
    if limit:
        selected = selected[:limit]
    if input_root is None:
        input_root = selected[0].parent if len(selected) == 1 else Path()

    plan: list[TranscriptionPlanItem] = []
    for media_path in selected:
        paths = transcript_paths(media_path, transcript_dir, input_root)
        outputs = expected_outputs(paths, options.write_json)
        status = "skip" if should_skip(outputs, options.force) else "pending"
        plan.append(
            TranscriptionPlanItem(
                media_path=media_path,
                paths=paths,
                outputs=outputs,
                status=status,
            )
        )
    return plan


def pending_transcriptions(plan: Iterable[TranscriptionPlanItem]) -> list[TranscriptionPlanItem]:
    return [item for item in plan if item.status == "pending"]


def transcribe_file(
    model,
    media_path: Path,
    args,
    log: LogCallback | None = None,
) -> tuple[list[TranscriptSegment], object]:
    language = None if args.language.lower() == "auto" else args.language
    emit_log(log, f"Decoder start: {media_path} language={language or 'auto'}")
    segments_iter, info = model.transcribe(
        str(media_path),
        language=language,
        task="transcribe",
        beam_size=args.beam_size,
        vad_filter=args.vad_filter,
        condition_on_previous_text=args.condition_on_previous_text,
    )
    segments = []
    for index, segment in enumerate(segments_iter, start=1):
        transcript_segment = TranscriptSegment(
            start=float(segment.start),
            end=float(segment.end),
            text=segment.text.strip(),
        )
        segments.append(transcript_segment)
        if index == 1 or index % 10 == 0:
            emit_log(
                log,
                (
                    f"Decoded segment {index}: {media_path.name} "
                    f"{format_timestamp(transcript_segment.start)} -> "
                    f"{format_timestamp(transcript_segment.end)}"
                ),
            )
    emit_log(log, f"Decoder done: {media_path} segments={len(segments)}")
    return segments, info


def print_transcription_failures(failures: list[TranscriptionFailure]) -> None:
    if not failures:
        return
    print("\nFailed transcriptions:", file=sys.stderr)
    for failure in failures:
        print(f"  {failure.media_path}: {failure.error}", file=sys.stderr)


def transcribe_all(args) -> int:
    if args.download_models is not None:
        models = args.download_models or list(PREFETCH_MODELS_DEFAULT)
        download_models(models, model_cache_dir=args.model_cache_dir)
        return 0

    input_path = Path(args.input)
    transcript_dir = Path(args.transcript_dir) if args.transcript_dir else None
    options = transcription_options_from_args(args)
    media_files = find_media_files(input_path)
    if not media_files:
        print(f"No media files found in {input_path}", file=sys.stderr)
        return 1

    plan = transcribe_plan_for_files(
        media_files,
        options=options,
        transcript_dir=transcript_dir,
        input_root=input_path,
        limit=args.limit,
    )
    pending = pending_transcriptions(plan)

    emit_log(
        None,
        (
            f"Found {len(media_files)} media file(s). Pending: {len(pending)}. "
            f"Workers: {options.workers}"
        ),
    )
    if args.dry_run:
        for item in plan:
            emit_log(None, f"{item.status}: {item.media_path}")
        return 0

    return transcribe_plan(pending, options)


def load_model(options: TranscriptionOptions, log: LogCallback | None = None):
    WhisperModel = import_faster_whisper()
    if options.device == "cuda":
        device_count = cuda_device_count()
        if device_count < 1:
            raise SystemExit(
                "CUDA requested but no NVIDIA GPU is visible. "
                "Check NVIDIA driver with nvidia-smi, then retry."
            )
        emit_log(log, f"CUDA device(s) visible: {device_count}")
        preload_cuda_runtime(log=log)

    started = time.monotonic()
    emit_log(
        log,
        (
            f"Loading model: {options.model} device={options.device} "
            f"compute={options.compute_type} cpu_threads={options.cpu_threads} "
            f"workers={options.workers}"
        ),
    )
    model = WhisperModel(
        options.model,
        device=options.device,
        compute_type=options.compute_type,
        cpu_threads=options.cpu_threads,
        num_workers=max(1, options.workers),
        download_root=options.model_cache_dir,
    )
    emit_log(log, f"Model loaded: {options.model} ({time.monotonic() - started:.1f}s)")
    return model


def transcribe_one_plan_item(
    model,
    item: TranscriptionPlanItem,
    options: TranscriptionOptions,
    index: int,
    total: int,
    log: LogCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> TranscriptionFailure | None:
    media_path = item.media_path
    txt_path, srt_path, json_path = item.paths
    if status_callback:
        status_callback(media_path, "running")
    emit_log(log, f"[{index}/{total}] Start: {media_path}")
    emit_log(log, f"Outputs: {', '.join(str(path) for path in item.outputs)}")
    started = time.monotonic()
    try:
        segments, info = transcribe_file(model, media_path, options, log=log)
        write_txt(txt_path, segments)
        emit_log(log, f"Wrote TXT: {txt_path}")
        write_srt(srt_path, segments)
        emit_log(log, f"Wrote SRT: {srt_path}")
        if options.write_json:
            write_json(json_path, media_path, segments, info)
            emit_log(log, f"Wrote JSON: {json_path}")
    except Exception as exc:
        if status_callback:
            status_callback(media_path, "failed")
        emit_log(log, f"Failed transcription: {media_path}: {exc}")
        return TranscriptionFailure(media_path=media_path, error=str(exc))

    if status_callback:
        status_callback(media_path, "done")
    emit_log(log, f"[{index}/{total}] Done: {media_path} ({time.monotonic() - started:.1f}s)")
    return None


def transcribe_plan(
    plan: Iterable[TranscriptionPlanItem],
    options: TranscriptionOptions,
    log: LogCallback | None = None,
    status_callback: StatusCallback | None = None,
    stop_requested: StopCallback | None = None,
) -> int:
    pending = list(plan)
    if not pending:
        emit_log(log, "No pending files.")
        return 0

    model = load_model(options, log=log)

    failures: list[TranscriptionFailure] = []
    total = len(pending)
    workers = max(1, min(options.workers, total))
    emit_log(log, f"Transcription queue start: files={total} workers={workers}")

    if workers == 1:
        for index, item in enumerate(pending, start=1):
            if stop_requested and stop_requested():
                emit_log(log, "Stop requested before next file.")
                break
            failure = transcribe_one_plan_item(
                model,
                item,
                options,
                index,
                total,
                log=log,
                status_callback=status_callback,
            )
            if failure:
                failures.append(failure)
    else:
        indexed_items = list(enumerate(pending, start=1))
        next_index = 0
        futures = {}

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="transcribe") as executor:
            while next_index < total and len(futures) < workers:
                index, item = indexed_items[next_index]
                next_index += 1
                futures[
                    executor.submit(
                        transcribe_one_plan_item,
                        model,
                        item,
                        options,
                        index,
                        total,
                        log,
                        status_callback,
                    )
                ] = item

            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    item = futures.pop(future)
                    try:
                        failure = future.result()
                    except Exception as exc:
                        failure = TranscriptionFailure(item.media_path, str(exc))
                        if status_callback:
                            status_callback(item.media_path, "failed")
                        emit_log(log, f"Worker failed: {item.media_path}: {exc}")
                    if failure:
                        failures.append(failure)

                while (
                    next_index < total
                    and len(futures) < workers
                    and not (stop_requested and stop_requested())
                ):
                    index, item = indexed_items[next_index]
                    next_index += 1
                    futures[
                        executor.submit(
                            transcribe_one_plan_item,
                            model,
                            item,
                            options,
                            index,
                            total,
                            log,
                            status_callback,
                        )
                    ] = item

                if stop_requested and stop_requested() and next_index < total:
                    emit_log(log, "Stop requested. Waiting for running file(s) to finish.")
                    next_index = total

    print_transcription_failures(failures)
    emit_log(log, f"Transcription queue done: failures={len(failures)}")
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
        "--download-models",
        nargs="*",
        default=None,
        metavar="MODEL",
        help=(
            "Download model(s) then exit. If no names are passed, downloads: "
            + ", ".join(PREFETCH_MODELS_DEFAULT)
        ),
    )
    parser.add_argument(
        "--model-cache-dir",
        default=None,
        help="Optional faster-whisper/Hugging Face model cache directory.",
    )
    parser.add_argument(
        "--language",
        default=LANGUAGE_DEFAULT,
        help=f"Language code, or 'auto'. Default: {LANGUAGE_DEFAULT}",
    )
    parser.add_argument(
        "--device",
        default=DEVICE_DEFAULT,
        help=f"auto, cuda, cpu. Default: {DEVICE_DEFAULT}",
    )
    parser.add_argument(
        "--compute-type",
        default=COMPUTE_TYPE_DEFAULT,
        help=f"auto, float16, int8_float16, int8, float32. Default: {COMPUTE_TYPE_DEFAULT}",
    )
    parser.add_argument("--beam-size", type=int, default=5, help="Beam size. Default: 5")
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=0,
        help="CPU threads. 0 lets faster-whisper choose. Default: 0",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=WORKERS_DEFAULT,
        help=f"Concurrent files to transcribe. Default: {WORKERS_DEFAULT}",
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
