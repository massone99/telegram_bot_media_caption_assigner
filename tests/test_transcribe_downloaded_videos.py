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
