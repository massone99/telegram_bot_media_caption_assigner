#!/usr/bin/env python3
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent
DOWNLOAD_SCRIPT = ROOT / "download_motw_videos.py"
TRANSCRIBE_SCRIPT = ROOT / "transcribe_downloaded_videos.py"
CONVERT_SCRIPT = ROOT / "convert_mkv_to_mp4.py"


def build_download_command(
    docx: str,
    output: str,
    archive: str = "",
    failed_file: str = "",
    skip_existing: bool = True,
    dry_run: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(DOWNLOAD_SCRIPT),
        "--docx",
        docx,
        "--output",
        output,
    ]
    if archive:
        command.extend(["--archive", archive])
    if failed_file:
        command.extend(["--failed-file", failed_file])
    if not skip_existing:
        command.append("--no-skip-existing")
    if dry_run:
        command.append("--dry-run")
    return command


def build_transcribe_command(
    input_path: str,
    transcript_dir: str = "",
    model: str = "medium",
    language: str = "en",
    device: str = "auto",
    compute_type: str = "auto",
    beam_size: int = 5,
    cpu_threads: int = 0,
    vad_filter: bool = True,
    condition_on_previous_text: bool = False,
    write_json: bool = False,
    force: bool = False,
    limit: int = 0,
    dry_run: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(TRANSCRIBE_SCRIPT),
        "--input",
        input_path,
        "--model",
        model,
        "--language",
        language,
        "--device",
        device,
        "--compute-type",
        compute_type,
        "--beam-size",
        str(beam_size),
        "--cpu-threads",
        str(cpu_threads),
    ]
    if transcript_dir:
        command.extend(["--transcript-dir", transcript_dir])
    if not vad_filter:
        command.append("--no-vad-filter")
    if condition_on_previous_text:
        command.append("--condition-on-previous-text")
    if write_json:
        command.append("--write-json")
    if force:
        command.append("--force")
    if limit:
        command.extend(["--limit", str(limit)])
    if dry_run:
        command.append("--dry-run")
    return command


def build_convert_command(
    input_path: str,
    output: str = "",
    failed_file: str = "",
    overwrite: bool = False,
    transcode_fallback: bool = True,
    delete_original: bool = False,
    dry_run: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(CONVERT_SCRIPT),
        "--input",
        input_path,
    ]
    if output:
        command.extend(["--output", output])
    if failed_file:
        command.extend(["--failed-file", failed_file])
    if overwrite:
        command.append("--overwrite")
    if not transcode_fallback:
        command.append("--no-transcode-fallback")
    if delete_original:
        command.append("--delete-original")
    if dry_run:
        command.append("--dry-run")
    return command


class MotwGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Joe Gambino MOTW Tools")
        self.geometry("920x680")
        self.minsize(760, 560)

        self.output_queue: queue.Queue[tuple[str, str | int]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen | None = None

        self._create_variables()
        self._create_widgets()
        self._poll_output()

    def _create_variables(self) -> None:
        self.docx_var = tk.StringVar(value=str(ROOT / "Joe Gambino MOTW.docx"))
        self.download_output_var = tk.StringVar(value=str(ROOT / "downloads"))
        self.archive_var = tk.StringVar(value="")
        self.failed_file_var = tk.StringVar(value=str(ROOT / "downloads" / "failed_downloads.txt"))
        self.skip_existing_var = tk.BooleanVar(value=True)

        self.transcribe_input_var = tk.StringVar(value=str(ROOT / "downloads"))
        self.transcript_dir_var = tk.StringVar(value="")
        self.model_var = tk.StringVar(value="medium")
        self.language_var = tk.StringVar(value="en")
        self.device_var = tk.StringVar(value="auto")
        self.compute_type_var = tk.StringVar(value="auto")
        self.beam_size_var = tk.IntVar(value=5)
        self.cpu_threads_var = tk.IntVar(value=0)
        self.limit_var = tk.IntVar(value=0)
        self.vad_filter_var = tk.BooleanVar(value=True)
        self.condition_var = tk.BooleanVar(value=False)
        self.write_json_var = tk.BooleanVar(value=False)
        self.force_var = tk.BooleanVar(value=False)

        self.convert_input_var = tk.StringVar(value=str(ROOT / "downloads"))
        self.convert_output_var = tk.StringVar(value="")
        self.convert_failed_file_var = tk.StringVar(value=str(ROOT / "downloads" / "failed_conversions.txt"))
        self.convert_overwrite_var = tk.BooleanVar(value=False)
        self.convert_fallback_var = tk.BooleanVar(value=True)
        self.convert_delete_original_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Ready")

    def _create_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(16, 14, 16, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Joe Gambino MOTW Tools", font=("TkDefaultFont", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=1, sticky="e")

        body = ttk.PanedWindow(self, orient=tk.VERTICAL)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))

        notebook = ttk.Notebook(body)
        notebook.add(self._download_tab(notebook), text="Download")
        notebook.add(self._convert_tab(notebook), text="Convert")
        notebook.add(self._transcribe_tab(notebook), text="Transcribe")
        body.add(notebook, weight=2)

        log_frame = ttk.Frame(body)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=14, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        body.add(log_frame, weight=3)

        footer = ttk.Frame(self, padding=(16, 0, 16, 14))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.clear_button = ttk.Button(footer, text="Clear Log", command=self._clear_log)
        self.clear_button.grid(row=0, column=1, padx=(8, 0))
        self.stop_button = ttk.Button(footer, text="Stop", command=self._stop_process, state="disabled")
        self.stop_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(footer, text="Quit", command=self._quit).grid(row=0, column=3, padx=(8, 0))

    def _download_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=14)
        frame.columnconfigure(1, weight=1)

        self._path_row(
            frame,
            0,
            "DOCX",
            self.docx_var,
            lambda: self._pick_file(self.docx_var, [("Word documents", "*.docx"), ("All files", "*.*")]),
        )
        self._path_row(
            frame,
            1,
            "Output",
            self.download_output_var,
            lambda: self._pick_folder(self.download_output_var),
        )
        self._path_row(
            frame,
            2,
            "Archive",
            self.archive_var,
            lambda: self._pick_save_file(self.archive_var, "downloaded.txt"),
        )
        self._path_row(
            frame,
            3,
            "Failed File",
            self.failed_file_var,
            lambda: self._pick_save_file(self.failed_file_var, "failed_downloads.txt"),
        )

        checks = ttk.Frame(frame)
        checks.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(checks, text="Skip existing videos", variable=self.skip_existing_var).pack(
            side="left"
        )

        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        self.download_dry_button = ttk.Button(
            actions, text="Preview Links", command=lambda: self._run_download(dry_run=True)
        )
        self.download_dry_button.pack(side="left")
        self.download_button = ttk.Button(
            actions, text="Download Videos", command=lambda: self._run_download(dry_run=False)
        )
        self.download_button.pack(side="left", padx=(8, 0))

        return frame

    def _convert_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=14)
        frame.columnconfigure(1, weight=1)

        self._path_row(
            frame,
            0,
            "Input",
            self.convert_input_var,
            lambda: self._pick_folder(self.convert_input_var),
        )
        self._path_row(
            frame,
            1,
            "Output",
            self.convert_output_var,
            lambda: self._pick_folder(self.convert_output_var),
        )
        self._path_row(
            frame,
            2,
            "Failed File",
            self.convert_failed_file_var,
            lambda: self._pick_save_file(self.convert_failed_file_var, "failed_conversions.txt"),
        )

        checks = ttk.Frame(frame)
        checks.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(checks, text="Overwrite MP4", variable=self.convert_overwrite_var).pack(
            side="left"
        )
        ttk.Checkbutton(checks, text="Transcode fallback", variable=self.convert_fallback_var).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(checks, text="Delete MKV after success", variable=self.convert_delete_original_var).pack(
            side="left", padx=(12, 0)
        )

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        self.convert_dry_button = ttk.Button(
            actions, text="Preview Conversions", command=lambda: self._run_convert(dry_run=True)
        )
        self.convert_dry_button.pack(side="left")
        self.convert_button = ttk.Button(
            actions, text="Convert MKV to MP4", command=lambda: self._run_convert(dry_run=False)
        )
        self.convert_button.pack(side="left", padx=(8, 0))

        return frame

    def _transcribe_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=14)
        frame.columnconfigure(1, weight=1)

        self._path_row(
            frame,
            0,
            "Input",
            self.transcribe_input_var,
            lambda: self._pick_folder(self.transcribe_input_var),
        )
        self._path_row(
            frame,
            1,
            "Transcript Dir",
            self.transcript_dir_var,
            lambda: self._pick_folder(self.transcript_dir_var),
        )

        ttk.Label(frame, text="Model").grid(row=2, column=0, sticky="w", pady=5)
        model = ttk.Combobox(
            frame,
            textvariable=self.model_var,
            values=("small", "medium", "large-v3", "distil-large-v3"),
        )
        model.grid(row=2, column=1, sticky="ew", pady=5)

        ttk.Label(frame, text="Language").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.language_var, width=12).grid(row=3, column=1, sticky="w", pady=5)

        options = ttk.Frame(frame)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for index in range(6):
            options.columnconfigure(index, weight=1)

        ttk.Label(options, text="Device").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options,
            textvariable=self.device_var,
            values=("auto", "cuda", "cpu"),
            width=12,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(options, text="Compute").grid(row=0, column=1, sticky="w")
        ttk.Combobox(
            options,
            textvariable=self.compute_type_var,
            values=("auto", "float16", "int8_float16", "int8", "float32"),
            width=14,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(options, text="Beam").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, from_=1, to=10, textvariable=self.beam_size_var, width=8).grid(
            row=1, column=2, sticky="ew", padx=(0, 8)
        )

        ttk.Label(options, text="CPU Threads").grid(row=0, column=3, sticky="w")
        ttk.Spinbox(options, from_=0, to=64, textvariable=self.cpu_threads_var, width=8).grid(
            row=1, column=3, sticky="ew", padx=(0, 8)
        )

        ttk.Label(options, text="Limit").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(options, from_=0, to=10000, textvariable=self.limit_var, width=8).grid(
            row=1, column=4, sticky="ew"
        )

        checks = ttk.Frame(frame)
        checks.grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Checkbutton(checks, text="VAD filter", variable=self.vad_filter_var).pack(side="left")
        ttk.Checkbutton(checks, text="Use previous text", variable=self.condition_var).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(checks, text="JSON", variable=self.write_json_var).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(checks, text="Overwrite", variable=self.force_var).pack(side="left", padx=(12, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        self.transcribe_dry_button = ttk.Button(
            actions, text="Preview Files", command=lambda: self._run_transcribe(dry_run=True)
        )
        self.transcribe_dry_button.pack(side="left")
        self.transcribe_button = ttk.Button(
            actions, text="Transcribe", command=lambda: self._run_transcribe(dry_run=False)
        )
        self.transcribe_button.pack(side="left", padx=(8, 0))

        return frame

    def _path_row(self, frame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=5)
        ttk.Button(frame, text="Browse", command=command).grid(row=row, column=2, sticky="e", pady=5)

    def _pick_file(self, variable: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(initialdir=ROOT, filetypes=filetypes)
        if path:
            variable.set(path)

    def _pick_save_file(self, variable: tk.StringVar, initialfile: str) -> None:
        path = filedialog.asksaveasfilename(initialdir=ROOT, initialfile=initialfile)
        if path:
            variable.set(path)

    def _pick_folder(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=ROOT)
        if path:
            variable.set(path)

    def _run_download(self, dry_run: bool) -> None:
        docx = self.docx_var.get().strip()
        output = self.download_output_var.get().strip()
        if not docx:
            messagebox.showwarning("Missing DOCX", "Select the MOTW .docx file.")
            return
        if not output:
            messagebox.showwarning("Missing output", "Select a download folder.")
            return
        command = build_download_command(
            docx=docx,
            output=output,
            archive=self.archive_var.get().strip(),
            failed_file=self.failed_file_var.get().strip(),
            skip_existing=self.skip_existing_var.get(),
            dry_run=dry_run,
        )
        self._start_process(command, "Previewing links" if dry_run else "Downloading videos")

    def _run_convert(self, dry_run: bool) -> None:
        input_path = self.convert_input_var.get().strip()
        if not input_path:
            messagebox.showwarning("Missing input", "Select a folder containing .mkv files.")
            return
        command = build_convert_command(
            input_path=input_path,
            output=self.convert_output_var.get().strip(),
            failed_file=self.convert_failed_file_var.get().strip(),
            overwrite=self.convert_overwrite_var.get(),
            transcode_fallback=self.convert_fallback_var.get(),
            delete_original=self.convert_delete_original_var.get(),
            dry_run=dry_run,
        )
        self._start_process(command, "Previewing conversions" if dry_run else "Converting MKV")

    def _run_transcribe(self, dry_run: bool) -> None:
        input_path = self.transcribe_input_var.get().strip()
        if not input_path:
            messagebox.showwarning("Missing input", "Select downloads folder or media file.")
            return
        command = build_transcribe_command(
            input_path=input_path,
            transcript_dir=self.transcript_dir_var.get().strip(),
            model=self.model_var.get().strip() or "medium",
            language=self.language_var.get().strip() or "en",
            device=self.device_var.get().strip() or "auto",
            compute_type=self.compute_type_var.get().strip() or "auto",
            beam_size=max(1, self.beam_size_var.get()),
            cpu_threads=max(0, self.cpu_threads_var.get()),
            vad_filter=self.vad_filter_var.get(),
            condition_on_previous_text=self.condition_var.get(),
            write_json=self.write_json_var.get(),
            force=self.force_var.get(),
            limit=max(0, self.limit_var.get()),
            dry_run=dry_run,
        )
        self._start_process(command, "Previewing files" if dry_run else "Transcribing")

    def _start_process(self, command: list[str], status: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A job is already running.")
            return
        self._set_busy(True)
        self.status_var.set(status)
        self._log_line("")
        self._log_line("$ " + " ".join(command))

        self.worker = threading.Thread(target=self._process_worker, args=(command,), daemon=True)
        self.worker.start()

    def _process_worker(self, command: list[str]) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.output_queue.put(("line", line.rstrip("\n")))
            return_code = self.process.wait()
            self.output_queue.put(("done", return_code))
        except Exception as exc:
            self.output_queue.put(("line", f"Failed to start: {exc}"))
            self.output_queue.put(("done", 1))

    def _poll_output(self) -> None:
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "line":
                    self._log_line(str(payload))
                elif kind == "done":
                    code = int(payload)
                    self.status_var.set("Done" if code == 0 else f"Failed ({code})")
                    self._log_line(f"Exit code: {code}")
                    self.process = None
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll_output)

    def _log_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (
            self.download_dry_button,
            self.download_button,
            self.convert_dry_button,
            self.convert_button,
            self.transcribe_dry_button,
            self.transcribe_button,
        ):
            button.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _stop_process(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status_var.set("Stopping")

    def _quit(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Quit", "A job is running. Stop it and quit?"):
                return
            self.process.terminate()
        self.destroy()


def main() -> int:
    app = MotwGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
