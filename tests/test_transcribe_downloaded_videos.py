import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transcribe_downloaded_videos as transcribe


class FindMediaFilesTests(unittest.TestCase):
    def test_find_media_files_recurses_and_ignores_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            category = root / "HIPS"
            category.mkdir()
            video = category / "001 - Hip.mp4"
            audio = category / "002 - Audio.m4a"
            transcript = category / "001 - Hip.txt"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            transcript.write_text("already done", encoding="utf-8")

            self.assertEqual(
                transcribe.find_media_files(root),
                [video, audio],
            )

    def test_find_media_files_accepts_single_media_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "video.webm"
            video.write_bytes(b"video")

            self.assertEqual(transcribe.find_media_files(video), [video])


class OutputPathTests(unittest.TestCase):
    def test_transcript_paths_next_to_video_by_default(self):
        media = Path("/tmp/downloads/HIPS/001 - Hip.mp4")

        txt, srt, json_path = transcribe.transcript_paths(media, None, Path("/tmp/downloads"))

        self.assertEqual(txt, Path("/tmp/downloads/HIPS/001 - Hip.txt"))
        self.assertEqual(srt, Path("/tmp/downloads/HIPS/001 - Hip.srt"))
        self.assertEqual(json_path, Path("/tmp/downloads/HIPS/001 - Hip.json"))

    def test_transcript_paths_mirror_categories_in_transcript_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "downloads" / "HIPS" / "001 - Hip.mp4"
            transcript_dir = root / "transcripts"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"video")

            txt, srt, json_path = transcribe.transcript_paths(
                media, transcript_dir, root / "downloads"
            )

            self.assertEqual(txt, transcript_dir / "HIPS" / "001 - Hip.txt")
            self.assertEqual(srt, transcript_dir / "HIPS" / "001 - Hip.srt")
            self.assertEqual(json_path, transcript_dir / "HIPS" / "001 - Hip.json")
            self.assertTrue((transcript_dir / "HIPS").is_dir())


class TranscriptionPlanTests(unittest.TestCase):
    def test_plan_preserves_explicit_file_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "b.mp4"
            second = root / "a.mp4"
            first.write_bytes(b"video")
            second.write_bytes(b"video")

            plan = transcribe.transcribe_plan_for_files(
                [first, second],
                transcribe.TranscriptionOptions(),
            )

            self.assertEqual([item.media_path for item in plan], [first, second])

    def test_plan_marks_skip_unless_overwrite_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "video.mp4"
            media.write_bytes(b"video")
            media.with_suffix(".txt").write_text("done", encoding="utf-8")
            media.with_suffix(".srt").write_text("1\n", encoding="utf-8")

            skipped = transcribe.transcribe_plan_for_files(
                [media],
                transcribe.TranscriptionOptions(force=False),
            )
            pending = transcribe.transcribe_plan_for_files(
                [media],
                transcribe.TranscriptionOptions(force=True),
            )

            self.assertEqual(skipped[0].status, "skip")
            self.assertEqual(pending[0].status, "pending")

    def test_plan_json_option_requires_json_output_to_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "video.mp4"
            media.write_bytes(b"video")
            media.with_suffix(".txt").write_text("done", encoding="utf-8")
            media.with_suffix(".srt").write_text("1\n", encoding="utf-8")

            plan = transcribe.transcribe_plan_for_files(
                [media],
                transcribe.TranscriptionOptions(write_json=True),
            )

            self.assertEqual(
                plan[0].outputs,
                (
                    media.with_suffix(".txt"),
                    media.with_suffix(".srt"),
                    media.with_suffix(".json"),
                ),
            )
            self.assertEqual(plan[0].status, "pending")

    def test_plan_mirrors_transcript_dir_for_folder_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = root / "downloads"
            media = downloads / "HIPS" / "001 - Hip.mp4"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"video")
            transcript_dir = root / "transcripts"

            plan = transcribe.transcribe_plan_for_files(
                [media],
                transcribe.TranscriptionOptions(),
                transcript_dir=transcript_dir,
                input_root=downloads,
            )

            self.assertEqual(plan[0].paths[0], transcript_dir / "HIPS" / "001 - Hip.txt")
            self.assertEqual(plan[0].paths[1], transcript_dir / "HIPS" / "001 - Hip.srt")

    def test_plan_handles_arbitrary_multi_file_selection_with_transcript_dir(self):
        with tempfile.TemporaryDirectory() as left_tmp, tempfile.TemporaryDirectory() as right_tmp:
            left = Path(left_tmp) / "left.mp4"
            right = Path(right_tmp) / "right.mp4"
            left.write_bytes(b"video")
            right.write_bytes(b"video")
            transcript_dir = Path(left_tmp) / "transcripts"

            plan = transcribe.transcribe_plan_for_files(
                [left, right],
                transcribe.TranscriptionOptions(),
                transcript_dir=transcript_dir,
            )

            self.assertEqual(plan[0].paths[0], transcript_dir / "left.txt")
            self.assertEqual(plan[1].paths[0], transcript_dir / "right.txt")


class FormattingTests(unittest.TestCase):
    def test_format_timestamp_uses_srt_format(self):
        self.assertEqual(transcribe.format_timestamp(3723.4567), "01:02:03,457")

    def test_write_srt_and_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            segments = [
                transcribe.TranscriptSegment(0.0, 1.5, " First line "),
                transcribe.TranscriptSegment(1.5, 3.0, "Second line"),
            ]

            txt = root / "out.txt"
            srt = root / "out.srt"
            transcribe.write_txt(txt, segments)
            transcribe.write_srt(srt, segments)

            self.assertEqual(txt.read_text(encoding="utf-8"), "First line\nSecond line\n")
            self.assertIn("00:00:00,000 --> 00:00:01,500", srt.read_text(encoding="utf-8"))
            self.assertIn("Second line", srt.read_text(encoding="utf-8"))

    def test_expected_outputs_include_json_only_when_requested(self):
        paths = (Path("a.txt"), Path("a.srt"), Path("a.json"))

        self.assertEqual(transcribe.expected_outputs(paths, False), paths[:2])
        self.assertEqual(transcribe.expected_outputs(paths, True), paths)

    def test_print_transcription_failures_lists_failed_file_and_error(self):
        stderr = io.StringIO()
        failure = transcribe.TranscriptionFailure(
            media_path=Path("downloads/HIPS/001 - Hip.mp4"),
            error="decode failed",
        )

        with contextlib.redirect_stderr(stderr):
            transcribe.print_transcription_failures([failure])

        output = stderr.getvalue()
        self.assertIn("downloads/HIPS/001 - Hip.mp4", output)
        self.assertIn("decode failed", output)


if __name__ == "__main__":
    unittest.main()
