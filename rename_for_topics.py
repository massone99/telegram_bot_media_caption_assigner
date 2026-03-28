import os
import shutil
import threading
from typing import Optional
import customtkinter as ctk
from tkinter import messagebox


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DELIMITER = "##"


# ═══════════════════════════════════════════════════════════
#  Custom Folder Browser Dialog (pure CustomTkinter)
# ═══════════════════════════════════════════════════════════

class FolderBrowserDialog(ctk.CTkToplevel):
    """A modern folder browser built entirely with CustomTkinter."""

    def __init__(self, parent, title="Select Folder", start_path=None, callback=None):
        super().__init__(parent)

        self.callback = callback
        self.result = None
        self.current_path = os.path.abspath(start_path or os.path.expanduser("~"))

        self.title(title)
        self.geometry("600x520")
        self.minsize(450, 380)
        self.resizable(True, True)

        # ── Path bar ──
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.pack(fill="x", padx=16, pady=(16, 0))

        ctk.CTkLabel(
            path_frame, text="📍 Location:", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left", padx=(0, 6))

        self.path_var = ctk.StringVar(value=self.current_path)
        self.path_entry = ctk.CTkEntry(
            path_frame,
            textvariable=self.path_var,
            height=34,
            font=ctk.CTkFont(size=12),
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.path_entry.bind("<Return>", lambda e: self._navigate_to_entry())

        ctk.CTkButton(
            path_frame, text="Go", width=50, height=34, command=self._navigate_to_entry
        ).pack(side="right")

        # ── Quick-access bar ──
        quick_frame = ctk.CTkFrame(self, fg_color="transparent")
        quick_frame.pack(fill="x", padx=16, pady=(8, 0))

        home = os.path.expanduser("~")
        quick_paths = [
            ("🏠 Home", home),
            ("📥 Downloads", os.path.join(home, "Downloads")),
            ("🖥 Desktop", os.path.join(home, "Desktop")),
            ("📄 Documents", os.path.join(home, "Documents")),
        ]

        # Windows drive letters
        for drive_letter in "CDEF":
            dp = f"{drive_letter}:\\"
            if os.path.exists(dp):
                quick_paths.append((f"💾 {dp}", dp))

        for label, qpath in quick_paths:
            if os.path.isdir(qpath):
                ctk.CTkButton(
                    quick_frame,
                    text=label,
                    width=100,
                    height=30,
                    font=ctk.CTkFont(size=11),
                    fg_color="#333",
                    hover_color="#444",
                    command=lambda p=qpath: self._navigate(p),
                ).pack(side="left", padx=(0, 4))

        # ── Folder list (scrollable) ──
        self.scroll_frame = ctk.CTkScrollableFrame(
            self, fg_color="#1a1a1a", corner_radius=8
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        # ── Bottom: selected + buttons ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(10, 16))

        self.lbl_selected = ctk.CTkLabel(
            bottom,
            text=f"Selected:  {os.path.basename(self.current_path) or self.current_path}",
            font=ctk.CTkFont(size=12),
            text_color="#aaa",
            anchor="w",
        )
        self.lbl_selected.pack(fill="x", pady=(0, 8))

        btn_row = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row,
            text="✓  Select This Folder",
            width=180,
            height=38,
            fg_color="#1a6dd4",
            hover_color="#1558ab",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._confirm,
        ).pack(side="left")

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            width=90,
            height=38,
            fg_color="#444",
            hover_color="#555",
            command=self._cancel,
        ).pack(side="right")

        # Populate initial view
        self._populate(self.current_path)

        # Handle window close
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # CTkToplevel quirks: delay focus/grab so the window renders first
        self.after(150, self._setup_focus)

    def _setup_focus(self):
        """Delayed focus setup to avoid CTkToplevel rendering issues."""
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    # ── navigation ──

    def _navigate(self, path: str):
        path = os.path.abspath(path)
        if os.path.isdir(path):
            self.current_path = path
            self.path_var.set(path)
            self.lbl_selected.configure(
                text=f"Selected:  {os.path.basename(path) or path}"
            )
            self._populate(path)

    def _navigate_to_entry(self):
        entered = self.path_var.get().strip()
        if entered and os.path.isdir(entered):
            self._navigate(entered)
        else:
            messagebox.showwarning("Invalid path", f"'{entered}' is not a valid directory.", parent=self)

    def _populate(self, path: str):
        # clear old items
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        # parent (..) row
        parent_dir = os.path.dirname(path)
        if parent_dir and parent_dir != path:
            self._add_row("⬆  ..", parent_dir, is_parent=True)

        # list contents
        try:
            entries = sorted(os.listdir(path), key=lambda s: s.lower())
        except PermissionError:
            self._add_label("🔒  Permission denied")
            return

        dirs = [
            e for e in entries
            if os.path.isdir(os.path.join(path, e)) and not e.startswith(".")
        ]
        files = [
            e for e in entries
            if os.path.isfile(os.path.join(path, e)) and not e.startswith(".")
        ]

        if not dirs and not files:
            self._add_label("  (empty folder)")

        for d in dirs:
            full = os.path.join(path, d)
            self._add_row(f"📂  {d}", full)

        # show files (greyed out, not clickable) for context
        for f in files[:30]:
            self._add_file_row(f)

        if len(files) > 30:
            self._add_label(f"  … and {len(files) - 30} more files")

    def _add_row(self, label: str, target_path: str, is_parent: bool = False):
        btn = ctk.CTkButton(
            self.scroll_frame,
            text=label,
            anchor="w",
            height=32,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            hover_color="#2a2a2a",
            text_color="#80b0ff" if is_parent else "#e0e0e0",
            command=lambda p=target_path: self._navigate(p),
        )
        btn.pack(fill="x", padx=4, pady=1)

    def _add_file_row(self, name: str):
        lbl = ctk.CTkLabel(
            self.scroll_frame,
            text=f"   📄  {name}",
            anchor="w",
            height=26,
            font=ctk.CTkFont(size=12),
            text_color="#666",
        )
        lbl.pack(fill="x", padx=4, pady=0)

    def _add_label(self, text: str):
        ctk.CTkLabel(
            self.scroll_frame,
            text=text,
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color="#666",
        ).pack(fill="x", padx=8, pady=4)

    # ── confirm / cancel ──

    def _confirm(self):
        self.result = self.current_path
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.callback:
            self.callback(self.result)

    def _cancel(self):
        self.result = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self.callback:
            self.callback(None)


# ═══════════════════════════════════════════════════════════
#  Main Application
# ═══════════════════════════════════════════════════════════

class RenamerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Rename for Telegram Topics")
        self.geometry("720x560")
        self.minsize(600, 450)

        # ── Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            header,
            text="📂  Telegram Topic Renamer",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Prepend the top-level folder name to every file so the bot routes them into topics.",
            font=ctk.CTkFont(size=13),
            text_color="#aaa",
        ).pack(anchor="w", pady=(2, 0))

        # ── Folder Picker ──
        picker = ctk.CTkFrame(self, fg_color="transparent")
        picker.pack(fill="x", padx=24, pady=(16, 0))

        self.dir_var = ctk.StringVar(value="")

        self.dir_entry = ctk.CTkEntry(
            picker,
            textvariable=self.dir_var,
            placeholder_text="Select root folder…",
            height=38,
            font=ctk.CTkFont(size=13),
        )
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            picker, text="Browse", width=100, height=38, command=self._browse
        ).pack(side="right")

        # ── Naming mode ──
        mode_row = ctk.CTkFrame(self, fg_color="transparent")
        mode_row.pack(fill="x", padx=24, pady=(10, 0))

        self.use_parent_folder_var = ctk.BooleanVar(value=False)
        self.mode_switch = ctk.CTkSwitch(
            mode_row,
            text="Use immediate parent folder as prefix",
            variable=self.use_parent_folder_var,
            onvalue=True,
            offvalue=False,
            command=self._preview,
        )
        self.mode_switch.pack(side="left")

        # ── Stats Bar ──
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill="x", padx=24, pady=(12, 0))

        self.lbl_folders = ctk.CTkLabel(
            stats, text="Folders: —", font=ctk.CTkFont(size=12), text_color="#888"
        )
        self.lbl_folders.pack(side="left", padx=(0, 16))

        self.lbl_files = ctk.CTkLabel(
            stats, text="Files: —", font=ctk.CTkFont(size=12), text_color="#888"
        )
        self.lbl_files.pack(side="left")

        # ── Log Area ──
        self.log_box = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family="Consolas", size=12), state="disabled"
        )
        self.log_box.pack(fill="both", expand=True, padx=24, pady=(10, 0))

        # ── Bottom Buttons ──
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(12, 20))

        self.btn_preview = ctk.CTkButton(
            actions,
            text="👁  Preview",
            width=140,
            height=40,
            fg_color="#2d5a27",
            hover_color="#3a7a33",
            command=self._preview,
        )
        self.btn_preview.pack(side="left", padx=(0, 8))

        self.btn_rename = ctk.CTkButton(
            actions,
            text="🚀  Rename",
            width=140,
            height=40,
            fg_color="#1a6dd4",
            hover_color="#1558ab",
            command=self._rename,
        )
        self.btn_rename.pack(side="left")

        ctk.CTkButton(
            actions,
            text="Quit",
            width=80,
            height=40,
            fg_color="#444",
            hover_color="#555",
            command=self.quit,
        ).pack(side="right")

        self.progress = ctk.CTkProgressBar(actions, width=160, height=14)
        self.progress.pack(side="right", padx=(0, 12))
        self.progress.set(0)

    # ────────────── helpers ──────────────

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _log_clear(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _get_top_folders(self, root_dir: str):
        return sorted(
            d for d in os.listdir(root_dir) if os.path.isdir(os.path.join(root_dir, d))
        )

    def _build_plan(self, root_dir: str):
        plan = []
        top_folders = self._get_top_folders(root_dir)
        use_parent_folder = self.use_parent_folder_var.get()

        for top_folder in top_folders:
            top_path = os.path.join(root_dir, top_folder)
            for dirpath, _, filenames in os.walk(top_path):
                for fname in filenames:
                    old_path = os.path.join(dirpath, fname)
                    prefix_folder = os.path.basename(dirpath) if use_parent_folder else top_folder
                    new_name = f"{prefix_folder}{DELIMITER}{fname}"
                    new_path = os.path.join(root_dir, new_name)

                    if os.path.exists(new_path) or any(p[1] == new_path for p in plan):
                        base, ext = os.path.splitext(new_name)
                        i = 1
                        while os.path.exists(new_path) or any(
                            p[1] == new_path for p in plan
                        ):
                            new_path = os.path.join(root_dir, f"{base}_{i}{ext}")
                            i += 1

                    plan.append((old_path, new_path))

        return plan, top_folders

    def _validate_dir(self) -> Optional[str]:
        root_dir = self.dir_var.get().strip()
        if not root_dir or not os.path.isdir(root_dir):
            messagebox.showwarning("No folder", "Please select a valid folder first.")
            return None
        return root_dir

    # ────────────── actions ──────────────

    def _browse(self):
        # Use callback pattern — avoids wait_window deadlocks with CTkToplevel
        FolderBrowserDialog(
            self,
            title="Select Root Folder",
            start_path=self.dir_var.get() or None,
            callback=self._on_folder_selected,
        )

    def _on_folder_selected(self, path: Optional[str]):
        if path:
            self.dir_var.set(path)
            self._preview()

    def _preview(self):
        root_dir = self._validate_dir()
        if not root_dir:
            return

        self._log_clear()
        self.progress.set(0)

        plan, top_folders = self._build_plan(root_dir)

        self.lbl_folders.configure(text=f"Folders: {len(top_folders)}")
        self.lbl_files.configure(text=f"Files: {len(plan)}")

        self._log(f"📁  {root_dir}\n")
        mode_text = "parent folder" if self.use_parent_folder_var.get() else "top-level folder"
        self._log(f"⚙️  Mode: prefix from {mode_text}\n")
        for tf in top_folders:
            self._log(f"   📂 {tf}")
        self._log("")

        for old, new in plan:
            rel = os.path.relpath(old, root_dir)
            self._log(f"  {rel}  →  {os.path.basename(new)}")

        if not plan:
            self._log("⚠  No files found inside sub-folders.")
        else:
            self._log(f"\n✅  {len(plan)} file(s) ready — click Rename to execute.")

        self.progress.set(1)

    def _rename(self):
        root_dir = self._validate_dir()
        if not root_dir:
            return

        plan, top_folders = self._build_plan(root_dir)

        if not plan:
            messagebox.showinfo("Nothing to do", "No files found to rename.")
            return

        ok = messagebox.askyesno(
            "Confirm",
            f"Rename {len(plan)} file(s) and remove empty folder trees?\n\nThis cannot be undone.",
        )
        if not ok:
            return

        self.btn_preview.configure(state="disabled")
        self.btn_rename.configure(state="disabled")

        def work():
            self._log_clear()
            self._log("🚀  Renaming…\n")
            total = len(plan)

            for idx, (old, new) in enumerate(plan, 1):
                shutil.move(old, new)
                self._log(
                    f"  ✓  {os.path.relpath(old, root_dir)}  →  {os.path.basename(new)}"
                )
                self.progress.set(idx / total)

            self._log("")
            for tf in top_folders:
                tp = os.path.join(root_dir, tf)
                if os.path.isdir(tp):
                    shutil.rmtree(tp)
                    self._log(f"  🗑  Removed {tf}/")

            self._log(f"\n✅  Done! {total} file(s) renamed.")
            self.lbl_files.configure(text=f"Files: {total} ✓")
            self.btn_preview.configure(state="normal")
            self.btn_rename.configure(state="normal")
            messagebox.showinfo("Done", f"{total} file(s) renamed successfully!")

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    app = RenamerApp()
    app.mainloop()
