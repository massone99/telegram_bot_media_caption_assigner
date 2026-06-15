import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape, quoteattr
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import download_motw_videos as motw


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def paragraph(text="", link_rel_id=None, link_text=None):
    if link_rel_id:
        return (
            f"<w:p><w:hyperlink r:id={quoteattr(link_rel_id)}>"
            f"<w:r><w:t>{escape(link_text or text)}</w:t></w:r>"
            "</w:hyperlink></w:p>"
        )
    return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def write_docx(path, paragraphs, relationships=None):
    rel_entries = []
    for rel_id, target in (relationships or {}).items():
        rel_entries.append(
            f"<Relationship Id={quoteattr(rel_id)} "
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f"Target={quoteattr(target)} TargetMode=\"External\"/>"
        )

    document = (
        f'<w:document xmlns:w="{WORD_NS}" xmlns:r="{R_NS}">'
        f"<w:body>{''.join(paragraphs)}</w:body></w:document>"
    )
    rels = f'<Relationships xmlns="{REL_NS}">{"".join(rel_entries)}</Relationships>'

    with zipfile.ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document)
        docx.writestr("word/_rels/document.xml.rels", rels)


class ExtractVideosTests(unittest.TestCase):
    def test_extracts_categories_links_raw_urls_and_fused_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = Path(tmp) / "motw.docx"
            write_docx(
                docx_path,
                [
                    paragraph("HIPS"),
                    paragraph(
                        "Movement of the Week: Hip 90/90",
                        link_rel_id="rId1",
                        link_text="Movement of the Week: Hip 90/90",
                    ),
                    paragraph("Movement of the Week: Hinge and Reachhttps://youtu.be/icxvJH65a_MMovement of the Week: Supine Hip Flexion"),
                    paragraph("https://youtu.be/GvLjXNKt9tE"),
                    paragraph("SHOULDER & SCAPULA"),
                    paragraph("Movement of the Week: Wall Angels"),
                    paragraph("https://youtu.be/LGHn7eFqL0M"),
                ],
                {"rId1": "https://www.youtube.com/watch?v=PFR7bNYV4h8"},
            )

            videos = motw.extract_videos(docx_path)

        self.assertEqual(
            videos,
            [
                motw.Video(
                    "HIPS",
                    "Hip 90/90",
                    "https://www.youtube.com/watch?v=PFR7bNYV4h8",
                ),
                motw.Video("HIPS", "Hinge and Reach", "https://youtu.be/icxvJH65a_M"),
                motw.Video(
                    "HIPS", "Supine Hip Flexion", "https://youtu.be/GvLjXNKt9tE"
                ),
                motw.Video(
                    "SHOULDER & SCAPULA",
                    "Wall Angels",
                    "https://youtu.be/LGHn7eFqL0M",
                ),
            ],
        )

    def test_missing_title_uses_movement_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx_path = Path(tmp) / "motw.docx"
            write_docx(
                docx_path,
                [
                    paragraph("HIPS"),
                    paragraph("Movement of the Week:"),
                    paragraph("https://youtu.be/hAIdtQnujbo"),
                ],
            )

            videos = motw.extract_videos(docx_path)

        self.assertEqual(videos[0].title, "Movement 001")


class DownloadVideosTests(unittest.TestCase):
    @mock.patch("download_motw_videos.subprocess.run")
    @mock.patch("download_motw_videos.shutil.which", return_value="/usr/bin/yt-dlp")
    def test_download_creates_category_folder_and_uses_archive(self, which, run):
        run.return_value = mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "downloads"
            archive = output / "downloaded.txt"

            with contextlib.redirect_stdout(io.StringIO()):
                failures = motw.download_videos(
                    [
                        motw.Video(
                            "ELBOW & WRIST",
                            "Wrist Flexion PAILs/RAILs",
                            "https://youtu.be/YWSiy9ka76M",
                        )
                    ],
                    output,
                    archive,
                )

            self.assertEqual(failures, [])
            self.assertTrue((output / "ELBOW & WRIST").is_dir())

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/yt-dlp")
        self.assertIn("--download-archive", command)
        self.assertIn(str(archive), command)
        self.assertTrue(
            any(part.endswith("001 - Wrist Flexion PAILs_RAILs.%(ext)s") for part in command)
        )
        self.assertEqual(command[-1], "https://youtu.be/YWSiy9ka76M")

    @mock.patch("download_motw_videos.subprocess.run")
    @mock.patch("download_motw_videos.shutil.which", return_value="/usr/bin/yt-dlp")
    def test_download_does_not_use_archive_by_default(self, which, run):
        run.return_value = mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                failures = motw.download_videos(
                    [motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")],
                    Path(tmp) / "downloads",
                )

        self.assertEqual(failures, [])
        command = run.call_args.args[0]
        self.assertNotIn("--download-archive", command)

    @mock.patch("download_motw_videos.subprocess.run")
    @mock.patch("download_motw_videos.shutil.which", return_value="/usr/bin/yt-dlp")
    def test_download_skips_existing_video_file(self, which, run):
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "downloads"
            existing = output / "HIPS" / "001 - Hip 90_90.mp4"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"video")

            with contextlib.redirect_stdout(io.StringIO()):
                failures = motw.download_videos([video], output)

        self.assertEqual(failures, [])
        run.assert_not_called()

    @mock.patch("download_motw_videos.subprocess.run")
    @mock.patch("download_motw_videos.shutil.which", return_value="/usr/bin/yt-dlp")
    def test_download_can_force_existing_video_file(self, which, run):
        run.return_value = mock.Mock(returncode=0)
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "downloads"
            existing = output / "HIPS" / "001 - Hip 90_90.mp4"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"video")

            with contextlib.redirect_stdout(io.StringIO()):
                failures = motw.download_videos([video], output, skip_existing=False)

        self.assertEqual(failures, [])
        run.assert_called_once()

    def test_existing_downloads_ignore_transcripts_and_partial_files(self):
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "downloads"
            category = output / "HIPS"
            category.mkdir(parents=True)
            (category / "001 - Hip 90_90.srt").write_text("subtitle", encoding="utf-8")
            (category / "001 - Hip 90_90.mp4.part").write_bytes(b"partial")

            self.assertFalse(motw.is_video_already_downloaded(video, 1, output))

            (category / "001 - Hip 90_90.mkv").write_bytes(b"video")

            self.assertTrue(motw.is_video_already_downloaded(video, 1, output))

    @mock.patch("download_motw_videos.subprocess.run")
    @mock.patch("download_motw_videos.shutil.which", return_value="/usr/bin/yt-dlp")
    def test_download_reports_failed_video_details(self, which, run):
        run.return_value = mock.Mock(returncode=7)
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                failures = motw.download_videos(
                    [video],
                    Path(tmp) / "downloads",
                    Path(tmp) / "downloads" / "downloaded.txt",
                )

        self.assertEqual(failures, [motw.DownloadFailure(1, video, 7)])

    def test_print_download_failures_lists_failed_title_and_url(self):
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")
        failures = [motw.DownloadFailure(1, video, 7)]
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            motw.print_download_failures(failures)

        output = stderr.getvalue()
        self.assertIn("001. HIPS / Hip 90/90", output)
        self.assertIn("https://youtu.be/PFR7bNYV4h8", output)
        self.assertIn("(exit 7)", output)

    def test_save_download_failures_writes_failure_file(self):
        video = motw.Video("HIPS", "Hip 90/90", "https://youtu.be/PFR7bNYV4h8")
        failures = [motw.DownloadFailure(1, video, 7)]
        with tempfile.TemporaryDirectory() as tmp:
            failed_file = Path(tmp) / "logs" / "failed_downloads.txt"

            motw.save_download_failures(failures, failed_file)

            output = failed_file.read_text(encoding="utf-8")

        self.assertIn("001\tHIPS\tHip 90/90", output)
        self.assertIn("https://youtu.be/PFR7bNYV4h8", output)
        self.assertIn("exit=7", output)

    def test_save_download_failures_writes_empty_success_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            failed_file = Path(tmp) / "failed_downloads.txt"

            motw.save_download_failures([], failed_file)

            self.assertEqual(
                failed_file.read_text(encoding="utf-8"),
                "No failed downloads.\n",
            )

    @mock.patch("download_motw_videos.shutil.which", return_value=None)
    def test_download_requires_yt_dlp(self, which):
        with self.assertRaises(SystemExit) as error:
            motw.ensure_yt_dlp()

        self.assertIn("yt-dlp not found", str(error.exception))


class UtilityTests(unittest.TestCase):
    def test_sanitize_path_part_removes_invalid_filename_chars(self):
        self.assertEqual(
            motw.sanitize_path_part('A/B:C*D?"E', "fallback"),
            "A_B_C_D__E",
        )

    def test_sanitize_path_part_uses_fallback_for_blank_names(self):
        self.assertEqual(motw.sanitize_path_part("   ...   ", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
