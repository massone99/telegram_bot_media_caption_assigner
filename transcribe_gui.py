#!/usr/bin/env python3
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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

import transcribe_downloaded_videos as transcribe


ROOT = Path(__file__).resolve().parent
MEDIA_FILTER = (
    "Media files ("
    + " ".join(f"*{ext}" for ext in sorted(transcribe.VIDEO_EXTENSIONS))
    + ")"
)


class TranscriptionWorker(QThread):
    log = pyqtSignal(str)
    file_status = pyqtSignal(str, str)
    finished_with_code = pyqtSignal(int)

    def __init__(
        self,
        plan: list[transcribe.TranscriptionPlanItem],
        options: transcribe.TranscriptionOptions,
    ):
        super().__init__()
        self.plan = plan
        self.options = options
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def run(self) -> None:
        try:
            code = transcribe.transcribe_plan(
                self.plan,
                self.options,
                log=self.log.emit,
                status_callback=lambda path, status: self.file_status.emit(str(path), status),
                stop_requested=lambda: self.stop_requested,
            )
        except SystemExit as exc:
            self.log.emit(str(exc))
            self.finished_with_code.emit(1)
            return
        except Exception as exc:
            self.log.emit(f"Transcription failed: {exc}")
            self.finished_with_code.emit(1)
            return
        self.finished_with_code.emit(code)


class TranscribeGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Transcribe Videos")
        self.resize(1120, 760)

        self.files: list[Path] = []
        self.scan_root: Path | None = None
        self.worker: TranscriptionWorker | None = None
        self.worker_exit_code: int | None = None
        self.close_when_worker_finishes = False
        self.settings = QSettings("bot_rinomina_video", "transcribe_gui")

        self._create_widgets()
        self._restore_settings()
        self._connect_settings_signals()
        self._set_busy(False)

    def _create_widgets(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        source_group = QGroupBox("Files")
        source_layout = QGridLayout(source_group)
        self.folder_edit = QLineEdit(str(ROOT / "downloads"))
        self.transcript_dir_edit = QLineEdit()
        self.model_cache_dir_edit = QLineEdit()
        source_layout.addWidget(QLabel("Folder"), 0, 0)
        source_layout.addWidget(self.folder_edit, 0, 1)
        self.scan_button = QPushButton("Scan Folder")
        self.scan_button.clicked.connect(self.scan_folder)
        source_layout.addWidget(self.scan_button, 0, 2)
        self.folder_button = QPushButton("Browse")
        self.folder_button.clicked.connect(self.pick_folder)
        source_layout.addWidget(self.folder_button, 0, 3)

        source_layout.addWidget(QLabel("Output Dir"), 1, 0)
        source_layout.addWidget(self.transcript_dir_edit, 1, 1)
        self.transcript_dir_button = QPushButton("Browse")
        self.transcript_dir_button.clicked.connect(self.pick_transcript_dir)
        source_layout.addWidget(self.transcript_dir_button, 1, 2)
        self.add_files_button = QPushButton("Add Files")
        self.add_files_button.clicked.connect(self.add_files)
        source_layout.addWidget(self.add_files_button, 1, 3)

        source_layout.addWidget(QLabel("Model Cache Dir"), 2, 0)
        source_layout.addWidget(self.model_cache_dir_edit, 2, 1)
        self.model_cache_dir_button = QPushButton("Browse")
        self.model_cache_dir_button.clicked.connect(self.pick_model_cache_dir)
        source_layout.addWidget(self.model_cache_dir_button, 2, 2)
        layout.addWidget(source_group)

        options_group = QGroupBox("Options")
        options_layout = QGridLayout(options_group)
        self.model_combo = self._combo(
            ("small", "medium", "large-v3", "distil-large-v3"),
            "medium",
        )
        self.language_edit = QLineEdit("en")
        self.device_combo = self._combo(("cuda", "auto", "cpu"), transcribe.DEVICE_DEFAULT)
        self.compute_combo = self._combo(
            ("float16", "int8_float16", "auto", "int8", "float32"),
            transcribe.COMPUTE_TYPE_DEFAULT,
        )
        self.beam_spin = self._spin(1, 20, 5)
        self.cpu_threads_spin = self._spin(0, 128, 0)
        self.workers_spin = self._spin(1, 32, transcribe.WORKERS_DEFAULT)
        self.vad_check = QCheckBox("VAD")
        self.vad_check.setChecked(True)
        self.previous_text_check = QCheckBox("Previous text")
        self.json_check = QCheckBox("JSON")
        self.json_check.setChecked(True)
        self.overwrite_check = QCheckBox("Overwrite")

        controls = [
            ("Model", self.model_combo),
            ("Language", self.language_edit),
            ("Device", self.device_combo),
            ("Compute", self.compute_combo),
            ("Beam", self.beam_spin),
            ("CPU Threads", self.cpu_threads_spin),
            ("Workers", self.workers_spin),
        ]
        for column, (label, widget) in enumerate(controls):
            options_layout.addWidget(QLabel(label), 0, column)
            options_layout.addWidget(widget, 1, column)
        options_layout.addWidget(self.vad_check, 2, 0)
        options_layout.addWidget(self.previous_text_check, 2, 1)
        options_layout.addWidget(self.json_check, 2, 2)
        options_layout.addWidget(self.overwrite_check, 2, 3)
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
        self.transcribe_selected_button = QPushButton("Transcribe Selected")
        self.transcribe_selected_button.clicked.connect(self.transcribe_selected)
        run_actions.addWidget(self.transcribe_selected_button)
        self.transcribe_all_button = QPushButton("Transcribe All")
        self.transcribe_all_button.clicked.connect(self.transcribe_all)
        run_actions.addWidget(self.transcribe_all_button)
        self.stop_button = QPushButton("Stop After Running Files")
        self.stop_button.clicked.connect(self.stop_after_current)
        run_actions.addWidget(self.stop_button)
        run_actions.addStretch(1)
        layout.addLayout(run_actions)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

    def _combo(self, values: tuple[str, ...], current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setEditable(True)
        combo.setCurrentText(current)
        return combo

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose media folder",
            self.folder_edit.text() or self.settings.value("lastMediaFolder", str(ROOT)),
        )
        if folder:
            self.folder_edit.setText(folder)
            self.settings.setValue("lastMediaFolder", folder)
            self.scan_folder()

    def pick_transcript_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose output folder",
            self.transcript_dir_edit.text() or self.settings.value("lastOutputFolder", str(ROOT)),
        )
        if folder:
            self.transcript_dir_edit.setText(folder)
            self.settings.setValue("lastOutputFolder", folder)

    def pick_model_cache_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose model cache folder",
            self.model_cache_dir_edit.text() or self.settings.value("lastModelCacheFolder", str(ROOT)),
        )
        if folder:
            self.model_cache_dir_edit.setText(folder)
            self.settings.setValue("lastModelCacheFolder", folder)

    def scan_folder(self) -> None:
        folder = Path(self.folder_edit.text()).expanduser()
        try:
            media_files = transcribe.find_media_files(folder)
        except SystemExit as exc:
            QMessageBox.warning(self, "Folder scan failed", str(exc))
            return
        self.scan_root = folder
        self.settings.setValue("lastMediaFolder", str(folder))
        self.files = media_files
        self.render_files()
        self.log_line(f"Scanned {folder}: {len(media_files)} media file(s)")

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add media files",
            self.settings.value("lastMediaFolder", str(ROOT)),
            MEDIA_FILTER,
        )
        if not paths:
            return
        existing = set(self.files)
        for raw_path in paths:
            path = Path(raw_path)
            if path.suffix.lower() in transcribe.VIDEO_EXTENSIONS and path not in existing:
                self.files.append(path)
                existing.add(path)
        if paths:
            self.settings.setValue("lastMediaFolder", str(Path(paths[0]).parent))
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

    def current_options(self) -> transcribe.TranscriptionOptions:
        return transcribe.TranscriptionOptions(
            model=self.model_combo.currentText().strip() or transcribe.MODEL_DEFAULT,
            language=self.language_edit.text().strip() or transcribe.LANGUAGE_DEFAULT,
            device=self.device_combo.currentText().strip() or transcribe.DEVICE_DEFAULT,
            compute_type=self.compute_combo.currentText().strip() or transcribe.COMPUTE_TYPE_DEFAULT,
            beam_size=self.beam_spin.value(),
            cpu_threads=self.cpu_threads_spin.value(),
            workers=self.workers_spin.value(),
            vad_filter=self.vad_check.isChecked(),
            condition_on_previous_text=self.previous_text_check.isChecked(),
            write_json=self.json_check.isChecked(),
            force=self.overwrite_check.isChecked(),
            model_cache_dir=self.model_cache_dir_edit.text().strip() or None,
        )

    def transcript_dir(self) -> Path | None:
        raw = self.transcript_dir_edit.text().strip()
        return Path(raw).expanduser() if raw else None

    def input_root_for(self, files: list[Path]) -> Path | None:
        if not self.scan_root:
            return None
        for path in files:
            try:
                path.relative_to(self.scan_root)
            except ValueError:
                return None
        return self.scan_root

    def build_plan(self, files: list[Path]) -> list[transcribe.TranscriptionPlanItem]:
        return transcribe.transcribe_plan_for_files(
            files,
            options=self.current_options(),
            transcript_dir=self.transcript_dir(),
            input_root=self.input_root_for(files),
        )

    def preview_pending(self) -> None:
        files = self.selected_files()
        if not files:
            QMessageBox.information(self, "No files selected", "Check at least one file.")
            return
        plan = self.build_plan(files)
        self.apply_plan_to_table(plan)
        pending = transcribe.pending_transcriptions(plan)
        self.log_line("")
        self.log_line(f"Preview: {len(pending)} pending, {len(plan) - len(pending)} skipped")
        for item in plan:
            outputs = ", ".join(str(path) for path in item.outputs)
            self.log_line(f"{item.status}: {item.media_path} -> {outputs}")

    def transcribe_selected(self) -> None:
        self.start_transcription(self.selected_files())

    def transcribe_all(self) -> None:
        self.start_transcription(list(self.files))

    def start_transcription(self, files: list[Path]) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A transcription job is already running.")
            return
        if not files:
            QMessageBox.information(self, "No files", "Add or scan at least one media file.")
            return

        options = self.current_options()
        plan = self.build_plan(files)
        self.apply_plan_to_table(plan)
        pending = transcribe.pending_transcriptions(plan)
        if not pending:
            self.log_line("No pending files.")
            return

        self.log_line("")
        self.log_line(
            f"Starting transcription: {len(pending)} pending file(s), workers={options.workers}"
        )
        self.worker = TranscriptionWorker(pending, options)
        self.worker.log.connect(self.log_line)
        self.worker.file_status.connect(self.update_file_status)
        self.worker.finished_with_code.connect(self.worker_finished)
        self.worker.finished.connect(self.worker_thread_finished)
        self._set_busy(True)
        self.worker.start()

    def apply_plan_to_table(self, plan: list[transcribe.TranscriptionPlanItem]) -> None:
        by_path = {str(item.media_path): item for item in plan}
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
            self.transcript_dir_button,
            self.model_cache_dir_button,
            self.add_files_button,
            self.preview_button,
            self.transcribe_selected_button,
            self.transcribe_all_button,
            self.select_all_button,
            self.select_none_button,
            self.clear_button,
        ):
            button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.status_label.setText("Transcribing" if busy else "Ready")

    def _restore_settings(self) -> None:
        self.folder_edit.setText(self.settings.value("folder", str(ROOT / "downloads")))
        self.transcript_dir_edit.setText(
            self.settings.value("outputDir", self.settings.value("transcriptDir", ""))
        )
        self.model_cache_dir_edit.setText(self.settings.value("modelCacheDir", ""))
        self.model_combo.setCurrentText(self.settings.value("model", "medium"))
        self.language_edit.setText(self.settings.value("language", "en"))
        self.device_combo.setCurrentText(self.settings.value("device", transcribe.DEVICE_DEFAULT))
        self.compute_combo.setCurrentText(self.settings.value("computeType", transcribe.COMPUTE_TYPE_DEFAULT))
        self.beam_spin.setValue(int(self.settings.value("beamSize", 5)))
        self.cpu_threads_spin.setValue(int(self.settings.value("cpuThreads", 0)))
        self.workers_spin.setValue(int(self.settings.value("workers", transcribe.WORKERS_DEFAULT)))
        self.vad_check.setChecked(self.settings.value("vadFilter", "true") == "true")
        self.previous_text_check.setChecked(self.settings.value("previousText", "false") == "true")
        self.json_check.setChecked(self.settings.value("writeJson", "true") == "true")
        self.overwrite_check.setChecked(self.settings.value("force", "false") == "true")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _save_settings(self) -> None:
        self.settings.setValue("folder", self.folder_edit.text())
        self.settings.setValue("transcriptDir", self.transcript_dir_edit.text())
        self.settings.setValue("outputDir", self.transcript_dir_edit.text())
        self.settings.setValue("modelCacheDir", self.model_cache_dir_edit.text())
        self.settings.setValue("model", self.model_combo.currentText())
        self.settings.setValue("language", self.language_edit.text())
        self.settings.setValue("device", self.device_combo.currentText())
        self.settings.setValue("computeType", self.compute_combo.currentText())
        self.settings.setValue("beamSize", self.beam_spin.value())
        self.settings.setValue("cpuThreads", self.cpu_threads_spin.value())
        self.settings.setValue("workers", self.workers_spin.value())
        self.settings.setValue("vadFilter", "true" if self.vad_check.isChecked() else "false")
        self.settings.setValue("previousText", "true" if self.previous_text_check.isChecked() else "false")
        self.settings.setValue("writeJson", "true" if self.json_check.isChecked() else "false")
        self.settings.setValue("force", "true" if self.overwrite_check.isChecked() else "false")
        self.settings.setValue("geometry", self.saveGeometry())

    def _connect_settings_signals(self) -> None:
        for line_edit in (
            self.folder_edit,
            self.transcript_dir_edit,
            self.model_cache_dir_edit,
            self.language_edit,
        ):
            line_edit.textChanged.connect(self._save_settings)
        for combo in (
            self.model_combo,
            self.device_combo,
            self.compute_combo,
        ):
            combo.currentTextChanged.connect(self._save_settings)
        for spin in (
            self.beam_spin,
            self.cpu_threads_spin,
            self.workers_spin,
        ):
            spin.valueChanged.connect(self._save_settings)
        for checkbox in (
            self.vad_check,
            self.previous_text_check,
            self.json_check,
            self.overwrite_check,
        ):
            checkbox.toggled.connect(self._save_settings)

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Quit",
                "A transcription is running. Stop after running file(s) and quit when finished?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.request_stop()
            self.close_when_worker_finishes = True
            event.ignore()
            return
        self._save_settings()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = TranscribeGui()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
