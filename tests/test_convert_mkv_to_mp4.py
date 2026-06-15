import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import convert_mkv_to_mp4 as convert


class JobPlanningTests(unittest.TestCase):
    def test_find_mkv_files_recurses_and_ignores_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            category = root / "HIPS"
            category.mkdir()
            mkv = category / "001 - Hip.mkv"
            mp4 = category / "001 - Hip.mp4"
            mkv.write_bytes(b"mkv")
            mp4.write_bytes(b"mp4")

            self.assertEqual(convert.find_mkv_files(root), [mkv])

    def test_build_jobs_mirrors_relative_paths_to_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "downloads" / "HIPS" / "001 - Hip.mkv"
            output = root / "mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"mkv")

            jobs = convert.build_jobs(root / "downloads", output)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].target, output / "HIPS" / "001 - Hip.mp4")

    def test_existing_mp4_requires_non_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp4 = Path(tmp) / "video.mp4"
            mp4.write_bytes(b"")
            self.assertFalse(convert.is_existing_mp4(mp4))

            mp4.write_bytes(b"mp4")
            self.assertTrue(convert.is_existing_mp4(mp4))


class ConvertJobTests(unittest.TestCase):
    @mock.patch("convert_mkv_to_mp4.run_ffmpeg")
    def test_convert_job_remuxes_to_temp_then_replaces_target(self, run_ffmpeg):
        def fake_run(command):
            Path(command[-1]).write_bytes(b"mp4")
            return 0, ""

        run_ffmpeg.side_effect = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mkv"
            target = root / "video.mp4"
            source.write_bytes(b"mkv")
            job = convert.ConversionJob(source, target)

            failure = convert.convert_job(job, ffmpeg="ffmpeg")

            self.assertIsNone(failure)
            self.assertEqual(target.read_bytes(), b"mp4")
            self.assertFalse(convert.temp_target_for(target).exists())

    @mock.patch("convert_mkv_to_mp4.run_ffmpeg")
    def test_convert_job_uses_transcode_fallback_after_remux_failure(self, run_ffmpeg):
        def fake_run(command):
            if "-c" in command and "copy" in command:
                return 1, "copy failed"
            Path(command[-1]).write_bytes(b"mp4")
            return 0, ""

        run_ffmpeg.side_effect = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mkv"
            target = root / "video.mp4"
            source.write_bytes(b"mkv")
            job = convert.ConversionJob(source, target)

            failure = convert.convert_job(job, ffmpeg="ffmpeg")

        self.assertIsNone(failure)
        self.assertEqual(run_ffmpeg.call_count, 2)

    @mock.patch("convert_mkv_to_mp4.run_ffmpeg")
    def test_convert_job_returns_failure_when_ffmpeg_fails(self, run_ffmpeg):
        run_ffmpeg.return_value = (9, "bad codec")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mkv"
            target = root / "video.mp4"
            source.write_bytes(b"mkv")
            job = convert.ConversionJob(source, target)

            failure = convert.convert_job(job, ffmpeg="ffmpeg", transcode_fallback=False)

        self.assertIsNotNone(failure)
        self.assertEqual(failure.returncode, 9)
        self.assertEqual(failure.stage, "remux")
        self.assertIn("bad codec", failure.error)

    @mock.patch("convert_mkv_to_mp4.run_ffmpeg")
    def test_convert_job_deletes_original_only_after_success(self, run_ffmpeg):
        def fake_run(command):
            Path(command[-1]).write_bytes(b"mp4")
            return 0, ""

        run_ffmpeg.side_effect = fake_run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mkv"
            target = root / "video.mp4"
            source.write_bytes(b"mkv")
            job = convert.ConversionJob(source, target)

            failure = convert.convert_job(job, ffmpeg="ffmpeg", delete_original=True)

            self.assertIsNone(failure)
            self.assertFalse(source.exists())


class ConvertAllTests(unittest.TestCase):
    @mock.patch("convert_mkv_to_mp4.ensure_ffmpeg", return_value="ffmpeg")
    @mock.patch("convert_mkv_to_mp4.convert_job")
    def test_convert_all_skips_existing_mp4(self, convert_job, ensure_ffmpeg):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkv = root / "video.mkv"
            mp4 = root / "video.mp4"
            mkv.write_bytes(b"mkv")
            mp4.write_bytes(b"mp4")
            args = mock.Mock(
                input=str(root),
                output=None,
                failed_file=None,
                dry_run=False,
                overwrite=False,
                transcode_fallback=True,
                delete_original=False,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                summary = convert.convert_all(args)

            self.assertEqual(summary.skipped, 1)
            self.assertEqual(summary.converted, 0)
            convert_job.assert_not_called()
            self.assertEqual(
                (root / "failed_conversions.txt").read_text(encoding="utf-8"),
                "No failed conversions.\n",
            )

    def test_save_conversion_failures_writes_file(self):
        failure = convert.ConversionFailure(
            source=Path("a.mkv"),
            target=Path("a.mp4"),
            stage="remux",
            returncode=3,
            error="broken",
        )
        with tempfile.TemporaryDirectory() as tmp:
            failed_file = Path(tmp) / "failed_conversions.txt"

            convert.save_conversion_failures([failure], failed_file)

            output = failed_file.read_text(encoding="utf-8")

        self.assertIn("a.mkv", output)
        self.assertIn("a.mp4", output)
        self.assertIn("stage=remux", output)
        self.assertIn("exit=3", output)
        self.assertIn("broken", output)


if __name__ == "__main__":
    unittest.main()
