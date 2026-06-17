import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import exercise_doc_gui
except ImportError as exc:
    exercise_doc_gui = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(exercise_doc_gui is None, f"exercise_doc_gui import failed: {IMPORT_ERROR}")
class ExerciseDocGuiPlanTests(unittest.TestCase):
    def test_build_plan_marks_skip_when_outputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "lesson.json"
            transcript.write_text(
                '{"duration": 2, "segments": [{"start": 0, "end": 2, "text": "Start position."}]}',
                encoding="utf-8",
            )
            output_dir = root / "docs"
            output_dir.mkdir()
            (output_dir / "lesson.md").write_text("done", encoding="utf-8")
            (output_dir / "lesson_manifest.json").write_text("{}", encoding="utf-8")

            options = exercise_doc_gui.ExerciseDocOptions(
                output_dir=output_dir,
                media_root=None,
                screenshots_per_block=1,
                seconds_per_screenshot=45,
                max_screenshots_per_block=12,
                extract_images=False,
                overwrite_images=False,
                write_docx=False,
                write_pdf=False,
                split_on_pauses=False,
                pause_seconds=12,
                ffmpeg_bin="ffmpeg",
                force=False,
            )

            plan = exercise_doc_gui.build_plan_for_transcripts([transcript], options)

        self.assertEqual(plan[0].status, "skip")

    def test_expected_outputs_include_images_and_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "lesson.json"
            transcript.write_text(
                '{"duration": 4, "segments": [{"start": 0, "end": 4, "text": "Push and rotate."}]}',
                encoding="utf-8",
            )
            options = exercise_doc_gui.ExerciseDocOptions(
                output_dir=root / "docs",
                media_root=None,
                screenshots_per_block=0,
                seconds_per_screenshot=2,
                max_screenshots_per_block=12,
                extract_images=True,
                overwrite_images=False,
                write_docx=True,
                write_pdf=True,
                split_on_pauses=False,
                pause_seconds=12,
                ffmpeg_bin="ffmpeg",
                force=False,
            )
            document = exercise_doc_gui.docs.build_document_from_transcript(transcript)

            outputs = exercise_doc_gui.expected_outputs_for_document(document, options)

        self.assertIn(root / "docs" / "lesson.docx", outputs)
        self.assertIn(root / "docs" / "lesson.pdf", outputs)
        self.assertTrue(any(path.parent.name == "lesson_assets" for path in outputs))


if __name__ == "__main__":
    unittest.main()
