#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


INPUT_DEFAULT = "downloads"
FAILED_CONVERSIONS_DEFAULT = "failed_conversions.txt"


@dataclass
class ConversionJob:
    source: Path
    target: Path


@dataclass
class ConversionFailure:
    source: Path
    target: Path
    stage: str
    returncode: int
    error: str


@dataclass
class ConversionSummary:
    total: int
    converted: int
    skipped: int
    failures: list[ConversionFailure]


def ensure_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    raise SystemExit("ffmpeg not found. Install it first, for example: sudo apt install ffmpeg")


def find_mkv_files(input_path: Path) -> list[Path]:
    if input_path.is_file() and input_path.suffix.lower() == ".mkv":
        return [input_path]
    if not input_path.is_dir():
        raise SystemExit(f"Input path not found or not an .mkv file: {input_path}")
    return sorted(path for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() == ".mkv")


def target_path_for(source: Path, input_root: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return source.with_suffix(".mp4")
    if input_root.is_file():
        relative = source.name
    else:
        relative = source.relative_to(input_root)
    return (output_dir / relative).with_suffix(".mp4")


def build_jobs(input_path: Path, output_dir: Path | None = None) -> list[ConversionJob]:
    return [
        ConversionJob(source=source, target=target_path_for(source, input_path, output_dir))
        for source in find_mkv_files(input_path)
    ]


def is_existing_mp4(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def temp_target_for(target: Path) -> Path:
    return target.with_name(target.name + ".tmp.mp4")


def remux_command(ffmpeg: str, source: Path, temp_target: Path) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-i",
        str(source),
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(temp_target),
    ]


def transcode_command(ffmpeg: str, source: Path, temp_target: Path) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-i",
        str(source),
        "-map",
        "0:v:0?",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-y",
        str(temp_target),
    ]


def run_ffmpeg(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return result.returncode, output.strip()


def tail_error(output: str, max_lines: int = 20) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def convert_job(
    job: ConversionJob,
    ffmpeg: str,
    overwrite: bool = False,
    transcode_fallback: bool = True,
    delete_original: bool = False,
) -> ConversionFailure | None:
    if is_existing_mp4(job.target) and not overwrite:
        return None

    job.target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = temp_target_for(job.target)
    if temp_target.exists():
        temp_target.unlink()

    returncode, output = run_ffmpeg(remux_command(ffmpeg, job.source, temp_target))
    stage = "remux"
    if returncode != 0 and transcode_fallback:
        if temp_target.exists():
            temp_target.unlink()
        returncode, output = run_ffmpeg(transcode_command(ffmpeg, job.source, temp_target))
        stage = "transcode"

    if returncode != 0:
        if temp_target.exists():
            temp_target.unlink()
        return ConversionFailure(
            source=job.source,
            target=job.target,
            stage=stage,
            returncode=returncode,
            error=tail_error(output),
        )

    if not is_existing_mp4(temp_target):
        if temp_target.exists():
            temp_target.unlink()
        return ConversionFailure(
            source=job.source,
            target=job.target,
            stage=stage,
            returncode=1,
            error="ffmpeg finished without creating a non-empty output file",
        )

    temp_target.replace(job.target)
    if delete_original and job.target.exists():
        job.source.unlink()
    return None


def format_conversion_failures(failures: list[ConversionFailure]) -> str:
    if not failures:
        return "No failed conversions.\n"
    lines = ["Failed conversions:"]
    for failure in failures:
        lines.append(
            f"{failure.source}\t{failure.target}\tstage={failure.stage}\t"
            f"exit={failure.returncode}\t{failure.error}"
        )
    return "\n".join(lines) + "\n"


def save_conversion_failures(failures: list[ConversionFailure], failed_file: Path) -> None:
    failed_file.parent.mkdir(parents=True, exist_ok=True)
    failed_file.write_text(format_conversion_failures(failures), encoding="utf-8")


def default_failed_file(input_path: Path, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir / FAILED_CONVERSIONS_DEFAULT
    if input_path.is_file():
        return input_path.parent / FAILED_CONVERSIONS_DEFAULT
    return input_path / FAILED_CONVERSIONS_DEFAULT


def convert_all(args) -> ConversionSummary:
    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else None
    failed_file = Path(args.failed_file) if args.failed_file else default_failed_file(input_path, output_dir)
    jobs = build_jobs(input_path, output_dir)

    if not jobs:
        raise SystemExit(f"No .mkv files found in {input_path}")

    print(f"Found {len(jobs)} .mkv file(s).")
    if args.dry_run:
        for job in jobs:
            status = "skip" if is_existing_mp4(job.target) and not args.overwrite else "convert"
            print(f"{status}: {job.source} -> {job.target}")
        save_conversion_failures([], failed_file)
        return ConversionSummary(total=len(jobs), converted=0, skipped=0, failures=[])

    ffmpeg = ensure_ffmpeg()
    converted = 0
    skipped = 0
    failures: list[ConversionFailure] = []

    for index, job in enumerate(jobs, start=1):
        if is_existing_mp4(job.target) and not args.overwrite:
            skipped += 1
            print(f"[{index}/{len(jobs)}] Skip existing: {job.target}")
            continue

        print(f"[{index}/{len(jobs)}] Convert: {job.source} -> {job.target}")
        failure = convert_job(
            job,
            ffmpeg=ffmpeg,
            overwrite=args.overwrite,
            transcode_fallback=args.transcode_fallback,
            delete_original=args.delete_original,
        )
        if failure is None:
            converted += 1
        else:
            failures.append(failure)
            print(
                f"Failed conversion: {failure.source} -> {failure.target} "
                f"(stage={failure.stage}, exit={failure.returncode})",
                file=sys.stderr,
            )

    save_conversion_failures(failures, failed_file)
    print(f"Failed conversion list saved to {failed_file}")
    print(f"Done. Converted: {converted}. Skipped: {skipped}. Failed: {len(failures)}.")
    return ConversionSummary(
        total=len(jobs),
        converted=converted,
        skipped=skipped,
        failures=failures,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert .mkv videos to .mp4 using ffmpeg.")
    parser.add_argument(
        "--input",
        default=INPUT_DEFAULT,
        help=f"Input .mkv file or folder to scan recursively. Default: {INPUT_DEFAULT}",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output folder. Folder structure is mirrored from input.",
    )
    parser.add_argument(
        "--failed-file",
        default=None,
        help=f"Write failed conversions here. Default: <output-or-input>/{FAILED_CONVERSIONS_DEFAULT}",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing .mp4 files.")
    parser.add_argument(
        "--no-transcode-fallback",
        dest="transcode_fallback",
        action="store_false",
        help="Do not retry with H.264/AAC transcode if fast remux fails.",
    )
    parser.set_defaults(transcode_fallback=True)
    parser.add_argument(
        "--delete-original",
        action="store_true",
        help="Delete each .mkv only after its .mp4 conversion succeeds.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show planned conversions only.")
    return parser.parse_args()


def main() -> int:
    summary = convert_all(parse_args())
    return 1 if summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
