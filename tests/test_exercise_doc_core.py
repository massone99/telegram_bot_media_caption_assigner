import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import exercise_doc_core as docs


class TranscriptParsingTests(unittest.TestCase):
    def test_parse_segments_skips_empty_text(self):
        payload = {
            "segments": [
                {"start": 0, "end": 1, "text": " First cue "},
                {"start": 2, "end": 3, "text": "   "},
            ]
        }

        segments = docs.parse_segments(payload)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "First cue")

    def test_infer_media_path_uses_json_source_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "video.mp4"
            media.write_bytes(b"video")
            transcript = root / "video.json"

            self.assertEqual(
                docs.infer_media_path({"source": str(media)}, transcript),
                media,
            )

    def test_infer_media_path_falls_back_to_media_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_root = root / "media"
            media_root.mkdir()
            media = media_root / "lesson.mp4"
            media.write_bytes(b"video")
            transcript = root / "transcripts" / "lesson.json"
            transcript.parent.mkdir()

            self.assertEqual(
                docs.infer_media_path({"source": "/missing/lesson.mp4"}, transcript, media_root),
                media,
            )


class BlockTests(unittest.TestCase):
    def test_build_blocks_defaults_to_one_block(self):
        segments = [
            docs.TranscriptSegment(0, 2, "start here"),
            docs.TranscriptSegment(40, 42, "finish there"),
        ]

        blocks = docs.build_blocks(segments, title="Hip CARs")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].title, "Hip CARs")
        self.assertEqual(blocks[0].start, 0)
        self.assertEqual(blocks[0].end, 42)

    def test_build_blocks_can_split_on_long_pauses(self):
        segments = [
            docs.TranscriptSegment(0, 25, "first exercise"),
            docs.TranscriptSegment(45, 70, "second exercise"),
        ]

        blocks = docs.build_blocks(
            segments,
            title="Class",
            split_on_pauses=True,
            pause_seconds=12,
            min_block_seconds=20,
        )

        self.assertEqual([block.title for block in blocks], ["Class - Part 1", "Class - Part 2"])


class ScreenshotCueTests(unittest.TestCase):
    def test_screenshot_cues_prefers_transcript_cues_then_fallback(self):
        block = docs.ExerciseBlock(
            title="Block",
            start=0,
            end=60,
            segments=(
                docs.TranscriptSegment(5, 8, "Set up your position and hold."),
                docs.TranscriptSegment(25, 28, "Random talking without keywords."),
                docs.TranscriptSegment(40, 43, "Push and rotate through the hip."),
            ),
        )

        cues = docs.screenshot_cues(block, count=3)

        self.assertEqual(len(cues), 3)
        self.assertTrue(any(cue.reason == "transcript-cue" for cue in cues))
        self.assertEqual(cues, tuple(sorted(cues, key=lambda cue: cue.time)))

    def test_seconds_to_timestamp_is_ffmpeg_friendly(self):
        self.assertEqual(docs.seconds_to_timestamp(3723.4567), "01:02:03.457")


class RenderingTests(unittest.TestCase):
    def test_render_markdown_includes_images_and_transcript(self):
        document = docs.TranscriptDocument(
            transcript_path=Path("lesson.json"),
            media_path=Path("lesson.mp4"),
            title="Lesson",
            duration=10,
            blocks=(
                docs.ExerciseBlock(
                    title="Lesson",
                    start=0,
                    end=10,
                    segments=(docs.TranscriptSegment(0, 2, "Start position."),),
                ),
            ),
        )

        rendered = docs.render_markdown(document, {(1, 1): Path("lesson_assets/cue.jpg")})

        self.assertIn("# Lesson", rendered)
        self.assertIn("![Cue 1](lesson_assets/cue.jpg)", rendered)
        self.assertIn("Start position.", rendered)

    def test_extract_screenshot_runs_expected_ffmpeg_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "video.mp4"
            output = root / "out" / "shot.jpg"
            media.write_bytes(b"video")

            with mock.patch("exercise_doc_core.subprocess.run") as run:
                docs.extract_screenshot(media, output, 12.345, ffmpeg_bin="ffmpeg-test")

        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:6], ["ffmpeg-test", "-hide_banner", "-loglevel", "error", "-ss", "12.345"])
        self.assertIn(str(media), command)
        self.assertEqual(command[-1], str(output))

    def test_manifest_lists_relative_image_paths(self):
        document = docs.TranscriptDocument(
            transcript_path=Path("lesson.json"),
            media_path=None,
            title="Lesson",
            duration=None,
            blocks=(
                docs.ExerciseBlock(
                    title="Lesson",
                    start=0,
                    end=10,
                    segments=(docs.TranscriptSegment(0, 2, "Start position."),),
                ),
            ),
        )
        cue = docs.ScreenshotCue(time=1, reason="fallback", score=0, text="")

        manifest = docs.manifest_for_document(
            document,
            {1: (cue,)},
            {(1, 1): Path("lesson_assets/cue.jpg")},
        )

        self.assertEqual(manifest["blocks"][0]["cues"][0]["image"], "lesson_assets/cue.jpg")


if __name__ == "__main__":
    unittest.main()
