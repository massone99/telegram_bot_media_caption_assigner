#!/usr/bin/env python3
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import exercise_doc_core as docs


ROOT = Path(__file__).resolve().parent
TRANSCRIPT_FILTER = "Transcript JSON files (*.json)"


@dataclass(frozen=True)
class ExerciseDocOptions:
    output_dir: Path
    media_root: Path | None
    screenshots_per_block: int
    extract_images: bool
    overwrite_images: bool
    write_docx: bool
    split_on_pauses: bool
    pause_seconds: int
    ffmpeg_bin: str
    force: bool


@dataclass(frozen=True)
class ExerciseDocPlanItem:
    transcript_path: Path
    outputs: tuple[Path, ...]
    status: str


class ExerciseDocWorker(QThread):
    log = pyqtSignal(str)
    file_status = pyqtSignal(str, str)
    finished_with_code = pyqtSignal(int)

    def __init__(
        self,
        plan: list[ExerciseDocPlanItem],
        options: ExerciseDocOptions,
    ):
        super().__init__()
        self.plan = plan
        self.options = options
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self) -> None:
        failures = []
        total = len(self.plan)
        for index, item in enumerate(self.plan, start=1):
            if self.stop_requested:
                self.log.emit("Stop requested before next file.")
                break
            self.file_status.emit(str(item.transcript_path), "running")
            self.log.emit(f"[{index}/{total}] Start: {item.transcript_path}")
            try:
                build_one_document(item.transcript_path, self.options, self.log.emit)
            except Exception as exc:
                failures.append(f"{item.transcript_path}: {exc}")
                self.file_status.emit(str(item.transcript_path), "failed")
                self.log.emit(f"Failed: {item.transcript_path}: {exc}")
                continue
            self.file_status.emit(str(item.transcript_path), "done")
            self.log.emit(f"[{index}/{total}] Done: {item.transcript_path}")

        if failures:
            self.log.emit("")
            self.log.emit("Failures:")
            for failure in failures:
                self.log.emit(f"  {failure}")
            self.finished_with_code.emit(1)
            return
        self.finished_with_code.emit(0)


class ExerciseDocGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Build Exercise Documents")
        self.resize(1120, 760)

        self.files: list[Path] = []
        self.scan_root: Path | None = None
        self.worker: ExerciseDocWorker | None = None
        self.worker_exit_code: int | None = None
        self.close_when_worker_finishes = False

        self._create_widgets()
        self._set_busy(False)

    def _create_widgets(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        source_group = QGroupBox("Files")
        source_layout = QGridLayout(source_group)
        self.folder_edit = QLineEdit(str(ROOT / "downloads"))
        self.output_dir_edit = QLineEdit(str(ROOT / "exercise_docs"))
        self.media_root_edit = QLineEdit()
        source_layout.addWidget(QLabel("Folder"), 0, 0)
        source_layout.addWidget(self.folder_edit, 0, 1)
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.clicked.connect(self.scan_folder)
        source_layout.addWidget(self.scan_button, 0, 2)
        self.folder_button = QPushButton("Browse")
        self.folder_button.clicked.connect(self.pick_folder)
        source_layout.addWidget(self.folder_button, 0, 3)

        source_layout.addWidget(QLabel("Output Dir"), 1, 0)
        source_layout.addWidget(self.output_dir_edit, 1, 1)
        self.output_dir_button = QPushButton("Browse")
        self.output_dir_button.clicked.connect(self.pick_output_dir)
        source_layout.addWidget(self.output_dir_button, 1, 2)
        self.add_files_button = QPushButton("Add Files")
        self.add_files_button.clicked.connect(self.add_files)
        source_layout.addWidget(self.add_files_button, 1, 3)

        source_layout.addWidget(QLabel("Media Root"), 2, 0)
        source_layout.addWidget(self.media_root_edit, 2, 1)
        self.media_root_button = QPushButton("Browse")
        self.media_root_button.clicked.connect(self.pick_media_root)
        source_layout.addWidget(self.media_root_button, 2, 2)
        layout.addWidget(source_group)

        options_group = QGroupBox("Options")
        options_layout = QGridLayout(options_group)
        self.screenshots_spin = self._spin(1, 12, 3)
        self.pause_spin = self._spin(1, 120, 12)
        self.ffmpeg_edit = QLineEdit("ffmpeg")
        self.extract_check = QCheckBox("Extract")
        self.extract_check.setChecked(True)
        self.docx_check = QCheckBox("DOCX")
        self.split_check = QCheckBox("Split pauses")
        self.overwrite_images_check = QCheckBox("Overwrite images")
        self.overwrite_check = QCheckBox("Overwrite")

        controls = [
            ("Screenshots", self.screenshots_spin),
            ("Pause Seconds", self.pause_spin),
            ("FFmpeg", self.ffmpeg_edit),
        ]
        for column, (label, widget) in enumerate(controls):
            options_layout.addWidget(QLabel(label), 0, column)
            options_layout.addWidget(widget, 1, column)
        options_layout.addWidget(self.extract_check, 2, 0)
        options_layout.addWidget(self.docx_check, 2, 1)
        options_layout.addWidget(self.split_check, 2, 2)
        options_layout.addWidget(self.overwrite_images_check, 2, 3)
        options_layout.addWidget(self.overwrite_check, 2, 4)
        layout.addWidget(options_group)

        table_actions = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.select_all_button.clicked.connect(lambda: self.set_all_checked(True))
        table_actions.addWidget(self.select_all_button)
        self.select_none_button = QPushButton("Clear Selection")
        self.select_none_button.clicked.connect(lambda: self.set_all_checked(False))
        table_actions.addWidget(self.select_none_button)
        self.clear_button = QPushButton("Clear List")
        self.clear_button.clicked.connect(self.clear_files)
        table_actions.addWidget(self.clear_button)
        table_actions.addStretch(1)
        self.status_label = QLabel("Ready")
        table_actions.addWidget(self.status_label)
        layout.addLayout(table_actions)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("Use", "File", "Status", "Outputs"))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, stretch=1)

        run_actions = QHBoxLayout()
        self.preview_button = QPushButton("Preview Pending")
        self.preview_button.clicked.connect(self.preview_pending)
        run_actions.addWidget(self.preview_button)
        self.build_selected_button = QPushButton("Build Selected")
        self.build_selected_button.clicked.connect(self.build_selected)
        run_actions.addWidget(self.build_selected_button)
        self.build_all_button = QPushButton("Build All")
        self.build_all_button.clicked.connect(self.build_all)
        run_actions.addWidget(self.build_all_button)
        self.stop_button = QPushButton("Stop After Running Files")
        self.stop_button.clicked.connect(self.stop_after_current)
        run_actions.addWidget(self.stop_button)
        run_actions.addStretch(1)
        layout.addLayout(run_actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose transcript folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self.scan_folder()

    def pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", str(ROOT))
        if folder:
            self.output_dir_edit.setText(folder)

    def pick_media_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose media folder", str(ROOT))
        if folder:
            self.media_root_edit.setText(folder)

    def scan_folder(self) -> None:
        folder = Path(self.folder_edit.text()).expanduser()
        try:
            transcript_files = docs.discover_transcripts(folder)
        except ValueError as exc:
            QMessageBox.warning(self, "Folder scan failed", str(exc))
            return
        self.scan_root = folder
        self.files = transcript_files
        self.render_files()
        self.log_line(f"Scanned {folder}: {len(transcript_files)} transcript file(s)")

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add transcript files", str(ROOT), TRANSCRIPT_FILTER)
        if not paths:
            return
        existing = set(self.files)
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix.lower() == ".json" and path not in existing:
                self.files.append(path)
                existing.add(path)
        self.render_files()
        self.log_line(f"Added {len(paths)} selected file(s)")

    def clear_files(self) -> None:
        self.files = []
        self.scan_root = None
        self.render_files()

    def render_files(self) -> None:
        self.table.setRowCount(0)
        for path in self.files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check_item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(path)))
            self.table.setItem(row, 2, QTableWidgetItem("pending"))
            self.table.setItem(row, 3, QTableWidgetItem(""))
        self.status_label.setText(f"{len(self.files)} file(s)")

    def set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def selected_files(self) -> list[Path]:
        selected: list[Path] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected.append(Path(self.table.item(row, 1).text()))
        return selected

    def current_options(self) -> ExerciseDocOptions:
        media_root_raw = self.media_root_edit.text().strip()
        return ExerciseDocOptions(
            output_dir=Path(self.output_dir_edit.text()).expanduser(),
            media_root=Path(media_root_raw).expanduser() if media_root_raw else None,
            screenshots_per_block=self.screenshots_spin.value(),
            extract_images=self.extract_check.isChecked(),
            overwrite_images=self.overwrite_images_check.isChecked(),
            write_docx=self.docx_check.isChecked(),
            split_on_pauses=self.split_check.isChecked(),
            pause_seconds=self.pause_spin.value(),
            ffmpeg_bin=self.ffmpeg_edit.text().strip() or "ffmpeg",
            force=self.overwrite_check.isChecked(),
        )

    def build_plan(self, files: list[Path]) -> list[ExerciseDocPlanItem]:
        return build_plan_for_transcripts(files, self.current_options())

    def preview_pending(self) -> None:
        files = self.selected_files()
        if not files:
            QMessageBox.information(self, "No files selected", "Check at least one file.")
            return
        plan = self.build_plan(files)
        self.apply_plan_to_table(plan)
        pending = [item for item in plan if item.status == "pending"]
        self.log_line("")
        self.log_line(f"Preview: {len(pending)} pending, {len(plan) - len(pending)} skipped")
        for item in plan:
            outputs = ", ".join(str(path) for path in item.outputs)
            self.log_line(f"{item.status}: {item.transcript_path} -> {outputs}")

    def build_selected(self) -> None:
        self.start_build(self.selected_files())

    def build_all(self) -> None:
        self.start_build(list(self.files))

    def start_build(self, files: list[Path]) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A document job is already running.")
            return
        if not files:
            QMessageBox.information(self, "No files", "Add or scan at least one transcript file.")
            return

        options = self.current_options()
        plan = self.build_plan(files)
        self.apply_plan_to_table(plan)
        pending = [item for item in plan if item.status == "pending"]
        if not pending:
            self.log_line("No pending files.")
            return

        self.log_line("")
        self.log_line(f"Starting document build: {len(pending)} pending file(s)")
        self.worker = ExerciseDocWorker(pending, options)
        self.worker.log.connect(self.log_line)
        self.worker.file_status.connect(self.update_file_status)
        self.worker.finished_with_code.connect(self.worker_finished)
        self.worker.finished.connect(self.worker_thread_finished)
        self._set_busy(True)
        self.worker.start()

    def apply_plan_to_table(self, plan: list[ExerciseDocPlanItem]) -> None:
        by_path = {str(item.transcript_path): item for item in plan}
        for row in range(self.table.rowCount()):
            path = self.table.item(row, 1).text()
            item = by_path.get(path)
            if item:
                self.table.item(row, 2).setText(item.status)
                self.table.item(row, 3).setText(", ".join(str(output) for output in item.outputs))

    def update_file_status(self, path: str, status: str) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1).text() == path:
                self.table.item(row, 2).setText(status)
                return

    def worker_finished(self, code: int) -> None:
        self.worker_exit_code = code
        self._set_busy(False)
        self.status_label.setText("Done" if code == 0 else "Finished with failures")
        self.log_line(f"Exit code: {code}")

    def worker_thread_finished(self) -> None:
        self.worker = None
        if self.close_when_worker_finishes:
            self.close_when_worker_finishes = False
            self.close()

    def stop_after_current(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
        self.status_label.setText("Stopping after running files")
        self.log_line("Stop requested. Running file(s) will finish first.")

    def log_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _set_busy(self, busy: bool) -> None:
        for button in (
            self.scan_button,
            self.folder_button,
            self.output_dir_button,
            self.media_root_button,
            self.add_files_button,
            self.preview_button,
            self.build_selected_button,
            self.build_all_button,
            self.select_all_button,
            self.select_none_button,
            self.clear_button,
        ):
            button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.status_label.setText("Building" if busy else "Ready")

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Quit",
                "A document build is running. Stop after running file(s) and quit when finished?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_stop()
            self.close_when_worker_finishes = True
            event.ignore()
            return
        event.accept()


def build_plan_for_transcripts(
    transcript_paths: list[Path],
    options: ExerciseDocOptions,
) -> list[ExerciseDocPlanItem]:
    plan = []
    for transcript_path in transcript_paths:
        document = docs.build_document_from_transcript(
            transcript_path,
            media_root=options.media_root,
            split_on_pauses=options.split_on_pauses,
            pause_seconds=options.pause_seconds,
        )
        outputs = expected_outputs_for_document(document, options)
        status = "pending" if options.force or not all(path.exists() for path in outputs) else "skip"
        plan.append(
            ExerciseDocPlanItem(
                transcript_path=transcript_path,
                outputs=outputs,
                status=status,
            )
        )
    return plan


def expected_outputs_for_document(
    document: docs.TranscriptDocument,
    options: ExerciseDocOptions,
) -> tuple[Path, ...]:
    slug = docs.safe_slug(document.title)
    outputs: list[Path] = [
        options.output_dir / f"{slug}.md",
        options.output_dir / f"{slug}_manifest.json",
    ]
    if options.write_docx:
        outputs.append(options.output_dir / f"{slug}.docx")
    if options.extract_images:
        for block_index, block in enumerate(document.blocks, start=1):
            for cue_index, cue in enumerate(
                docs.screenshot_cues(block, count=options.screenshots_per_block),
                start=1,
            ):
                outputs.append(
                    options.output_dir
                    / f"{slug}_assets"
                    / docs.screenshot_filename(block_index, cue_index, cue)
                )
    return tuple(outputs)


def build_one_document(
    transcript_path: Path,
    options: ExerciseDocOptions,
    log,
) -> None:
    document = docs.build_document_from_transcript(
        transcript_path,
        media_root=options.media_root,
        split_on_pauses=options.split_on_pauses,
        pause_seconds=options.pause_seconds,
    )
    slug = docs.safe_slug(document.title)
    asset_dir = options.output_dir / f"{slug}_assets"
    image_paths = {}
    cues_by_block = {}

    for block_index, block in enumerate(document.blocks, start=1):
        cues = docs.screenshot_cues(block, count=options.screenshots_per_block)
        cues_by_block[block_index] = cues
        for cue_index, cue in enumerate(cues, start=1):
            image_path = asset_dir / docs.screenshot_filename(block_index, cue_index, cue)
            image_paths[(block_index, cue_index)] = image_path.relative_to(options.output_dir)
            log(
                f"Cue: block={block_index} cue={cue_index} "
                f"time={cue.time:.3f}s reason={cue.reason} score={cue.score}"
            )
            if options.extract_images:
                if document.media_path is None:
                    raise RuntimeError(f"Media file not found for transcript: {transcript_path}")
                docs.extract_screenshot(
                    document.media_path,
                    image_path,
                    cue.time,
                    ffmpeg_bin=options.ffmpeg_bin,
                    overwrite=options.overwrite_images,
                )
                log(f"Wrote image: {image_path}")

    markdown_path = options.output_dir / f"{slug}.md"
    manifest_path = options.output_dir / f"{slug}_manifest.json"
    docs.write_markdown_document(markdown_path, document, image_paths)
    docs.write_manifest(
        manifest_path,
        docs.manifest_for_document(document, cues_by_block, image_paths),
    )
    log(f"Wrote Markdown: {markdown_path}")
    log(f"Wrote manifest: {manifest_path}")
    if options.write_docx:
        docx_path = options.output_dir / f"{slug}.docx"
        docs.write_docx_document(docx_path, document, image_paths)
        log(f"Wrote DOCX: {docx_path}")


def main() -> int:
    app = QApplication(sys.argv)
    window = ExerciseDocGui()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
