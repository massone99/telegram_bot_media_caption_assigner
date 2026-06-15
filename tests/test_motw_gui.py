import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import motw_gui


class CommandBuilderTests(unittest.TestCase):
    def test_build_download_command_includes_archive_and_dry_run(self):
        command = motw_gui.build_download_command(
            docx="Joe Gambino MOTW.docx",
            output="downloads",
            archive="downloads/downloaded.txt",
            failed_file="downloads/failed_downloads.txt",
            skip_existing=False,
            dry_run=True,
        )

        self.assertEqual(command[:3], [sys.executable, "-u", str(motw_gui.DOWNLOAD_SCRIPT)])
        self.assertIn("--docx", command)
        self.assertIn("Joe Gambino MOTW.docx", command)
        self.assertIn("--output", command)
        self.assertIn("downloads", command)
        self.assertIn("--archive", command)
        self.assertIn("downloads/downloaded.txt", command)
        self.assertIn("--failed-file", command)
        self.assertIn("downloads/failed_downloads.txt", command)
        self.assertIn("--no-skip-existing", command)
        self.assertIn("--dry-run", command)

    def test_build_transcribe_command_includes_accuracy_options(self):
        command = motw_gui.build_transcribe_command(
            input_path="downloads",
            transcript_dir="transcripts",
            model="large-v3",
            language="en",
            device="cuda",
            compute_type="float16",
            beam_size=6,
            cpu_threads=4,
            vad_filter=False,
            condition_on_previous_text=True,
            write_json=True,
            force=True,
            limit=3,
            dry_run=True,
        )

        self.assertEqual(command[:3], [sys.executable, "-u", str(motw_gui.TRANSCRIBE_SCRIPT)])
        self.assertIn("--input", command)
        self.assertIn("downloads", command)
        self.assertIn("--transcript-dir", command)
        self.assertIn("transcripts", command)
        self.assertIn("large-v3", command)
        self.assertIn("cuda", command)
        self.assertIn("float16", command)
        self.assertIn("--no-vad-filter", command)
        self.assertIn("--condition-on-previous-text", command)
        self.assertIn("--write-json", command)
        self.assertIn("--force", command)
        self.assertIn("--limit", command)
        self.assertIn("3", command)
        self.assertIn("--dry-run", command)

    def test_build_convert_command_includes_robust_options(self):
        command = motw_gui.build_convert_command(
            input_path="downloads",
            output="mp4",
            failed_file="mp4/failed_conversions.txt",
            overwrite=True,
            transcode_fallback=False,
            delete_original=True,
            dry_run=True,
        )

        self.assertEqual(command[:3], [sys.executable, "-u", str(motw_gui.CONVERT_SCRIPT)])
        self.assertIn("--input", command)
        self.assertIn("downloads", command)
        self.assertIn("--output", command)
        self.assertIn("mp4", command)
        self.assertIn("--failed-file", command)
        self.assertIn("mp4/failed_conversions.txt", command)
        self.assertIn("--overwrite", command)
        self.assertIn("--no-transcode-fallback", command)
        self.assertIn("--delete-original", command)
        self.assertIn("--dry-run", command)


if __name__ == "__main__":
    unittest.main()
