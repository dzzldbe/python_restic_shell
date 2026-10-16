
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

# --- Directory & File Resolution ---
if getattr(sys, "frozen", False):
    SCRIPTS_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPTS_DIR = Path(__file__).resolve().parent

SOURCE_DIR = SCRIPTS_DIR / "source"
RESTORE_BASE_DIR = SCRIPTS_DIR / "restore"

SOURCE_DIR.mkdir(parents=True, exist_ok=True)
RESTORE_BASE_DIR.mkdir(parents=True, exist_ok=True)

RESTIC_BIN = SOURCE_DIR / ("restic.exe" if os.name == "nt" else "restic")
PW_FILE = SOURCE_DIR / "password.txt"
CONFIG_FILE = SOURCE_DIR / "config.json"
LOG_FILE = SOURCE_DIR / "backup.log"

# Prevent black console windows from flashing on Windows when spawning restic.exe
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# --- Core Helpers ---
def is_admin() -> bool:
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jobs": []}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def normalize_include_path(raw: str) -> str:
    """Converts Windows paths like 'D:\\Work\\Taxes' into Restic's '/D/Work/Taxes' format."""
    trimmed = raw.strip()
    if not trimmed:
        return ""
    slashed = trimmed.replace("\\", "/")
    if len(slashed) >= 2 and slashed[1] == ":":
        drive = slashed[0]
        rest = slashed[2:].lstrip("/")
        return f"/{drive}/{rest}" if rest else f"/{drive}"
    return slashed


def write_log_header(title: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n===================================================\n[{ts}] {title}\n===================================================\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(header)
    except Exception:
        pass


def append_log_file(text: str) -> None:
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


# --- Headless CLI Mode for Windows Task Scheduler ---
def run_headless_backup_all() -> int:
    if not RESTIC_BIN.is_file():
        print(f"[ERROR] restic binary not found at {RESTIC_BIN}")
        return 1
    if not PW_FILE.is_file():
        print(f"[ERROR] Password file not found at {PW_FILE}")
        return 1

    cfg = load_config()
    jobs = cfg.get("jobs", [])
    if not jobs:
        print("[ERROR] No profiles configured in config.json")
        return 1

    all_ok = True
    for job in jobs:
        name = job["name"]
        src = Path(job["source_dir"])
        repo = Path(job["repo_dir"])

        write_log_header(f"SCHEDULED BACKUP [{name}]: {src} -> {repo}")
        print(f"\n--- Running Backup: [{name}] ---")

        if not (repo / "config").is_file():
            msg = f"[ERROR] Repository not found at {repo}\n"
            print(msg, end="")
            append_log_file(msg)
            all_ok = False
            continue

        if not src.is_dir():
            msg = f"[ERROR] Source directory not found at {src}\n"
            print(msg, end="")
            append_log_file(msg)
            all_ok = False
            continue

        args = [str(RESTIC_BIN), "-r", str(repo), "--password-file", str(PW_FILE), "backup", str(src)]
        if is_admin() and os.name == "nt":
            args.append("--use-fs-snapshot")

        proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
        if proc.stdout is not None:
                    for line in proc.stdout:
                        print(line, end="")
                        append_log_file(line)
        proc.wait()
#         proc = subprocess.Popen(
#             args,
#             stdout=subprocess.PIPE,
#             stderr=subprocess.STDOUT,
#             text=True,
#             encoding="utf-8",
#             errors="replace",
#             creationflags=CREATE_NO_WINDOW,
#         )
#         for line in proc.stdout:
#             print(line, end="")
#             append_log_file(line)
#         proc.wait()
#
#         if proc.returncode != 0:
#             all_ok = False
#
    return 0 if all_ok else 1


# --- Native Desktop GUI (Tkinter) ---
class ResticManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Restic Windows Backup Manager")
        self.geometry("1040x740")
        self.minsize(900, 620)
        self.configure(bg="#0f172a")

        self.config_data = load_config()
        self.log_queue = queue.Queue()
        self.active_proc = None
        self.task_running = False
        self.cancel_requested = False
        self.current_snapshots = []

        self._setup_styles()
        self._build_ui()
        self._refresh_profiles_ui()
        self.after(100, self._poll_queue)

        # Prompt for password on first launch if missing
        if not PW_FILE.is_file():
            self.after(300, self._prompt_password)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        bg_dark = "#0f172a"
        panel_bg = "#1e293b"
        text_fg = "#f8fafc"
        accent = "#3b82f6"
        border = "#334155"

        style.configure(".", background=bg_dark, foreground=text_fg, font=("Segoe UI", 10))
        style.configure("TFrame", background=bg_dark)
        style.configure("Card.TFrame", background=panel_bg, relief="flat")
        style.configure("TLabel", background=bg_dark, foreground=text_fg)
        style.configure("Card.TLabel", background=panel_bg, foreground=text_fg)
        style.configure("Header.TLabel", background=panel_bg, foreground=text_fg, font=("Segoe UI", 13, "bold"))

        style.configure("TNotebook", background=bg_dark, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel_bg, foreground="#94a3b8", padding=(16, 8), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", accent)], foreground=[("selected", "#ffffff")])

        style.configure("TButton", background=accent, foreground="#ffffff", borderwidth=0, padding=(12, 6), font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#2563eb"), ("disabled", "#475569")])

        style.configure("Success.TButton", background="#10b981", foreground="#ffffff")
        style.map("Success.TButton", background=[("active", "#059669")])

        style.configure("Danger.TButton", background="#ef4444", foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#dc2626")])

        style.configure("Secondary.TButton", background="#334155", foreground="#ffffff")
        style.map("Secondary.TButton", background=[("active", "#475569")])

        style.configure("TEntry", fieldbackground=bg_dark, foreground=text_fg, insertcolor=text_fg, bordercolor=border, padding=6)
        style.configure("TCombobox", fieldbackground=bg_dark, background=panel_bg, foreground=text_fg, arrowcolor=text_fg, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", bg_dark)], selectbackground=[("readonly", accent)])

        style.configure("Treeview", background=bg_dark, fieldbackground=bg_dark, foreground=text_fg, rowheight=28, borderwidth=0)
        style.configure("Treeview.Heading", background="#334155", foreground=text_fg, font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", accent)], foreground=[("selected", "#ffffff")])

    def _build_ui(self):
        # Top Header Bar
        header = ttk.Frame(self, style="Card.TFrame", padding=(16, 10))
        header.pack(fill="x", side="top")

        ttk.Label(header, text="Restic Backup Manager", style="Header.TLabel").pack(side="left", padx=(0, 14))

        admin_ok = is_admin()
        admin_text = "Admin / VSS Enabled" if admin_ok else "No Admin (VSS Disabled)"
        admin_bg = "#065f46" if admin_ok else "#78350f"
        tk.Label(header, text=admin_text, bg=admin_bg, fg="#ffffff", font=("Segoe UI", 9, "bold"), padx=10, pady=3).pack(side="left", padx=4)

        self.restic_badge = tk.Label(header, font=("Segoe UI", 9, "bold"), padx=10, pady=3)
        self.restic_badge.pack(side="left", padx=4)
        self._update_status_badges()

        ttk.Button(header, text="Set / Change Password", style="Secondary.TButton", command=self._prompt_password).pack(side="right", padx=4)
        ttk.Button(header, text="▶ Run ALL Backups", style="Success.TButton", command=self.run_all_backups).pack(side="right", padx=4)

        # Main Split: Tabs on top, Live Console on bottom
        paned = tk.PanedWindow(self, orient="vertical", bg="#334155", sashwidth=4, bd=0)
        paned.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, minsize=360)

        self.tab_profiles = ttk.Frame(self.notebook, padding=14)
        self.tab_restore = ttk.Frame(self.notebook, padding=14)
        self.tab_inspect = ttk.Frame(self.notebook, padding=14)

        self.notebook.add(self.tab_profiles, text="1. Profiles & Backup")
        self.notebook.add(self.tab_restore, text="2. Restore Snapshot")
        self.notebook.add(self.tab_inspect, text="3. Inspect, Stats & Prune")

        self._build_profiles_tab()
        self._build_restore_tab()
        self._build_inspect_tab()

        # Bottom Live Console
        console_container = ttk.Frame(paned)
        paned.add(console_container, minsize=180)

        console_bar = tk.Frame(console_container, bg="#131c2e", padx=12, pady=5)
        console_bar.pack(fill="x")

        self.status_lbl = tk.Label(console_bar, text="Idle — Ready", bg="#131c2e", fg="#94a3b8", font=("Segoe UI", 9, "bold"))
        self.status_lbl.pack(side="left")

        self.cancel_btn = ttk.Button(console_bar, text="Stop / Cancel Task", style="Danger.TButton", command=self.cancel_active_task)
        self.cancel_btn.pack(side="right")
        self.cancel_btn.state(["disabled"])

        ttk.Button(console_bar, text="Clear Log", style="Secondary.TButton", command=self._clear_console).pack(side="right", padx=6)

        self.console_text = tk.Text(
            console_container,
            bg="#090d16",
            fg="#e2e8f0",
            font=("Consolas", 10),
            wrap="word",
            bd=0,
            padx=12,
            pady=8,
            state="disabled",
        )
        scroll = ttk.Scrollbar(console_container, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.console_text.pack(side="left", fill="both", expand=True)

    # --- TAB 1: Profiles & Backup ---
    def _build_profiles_tab(self):
        list_card = ttk.Frame(self.tab_profiles, style="Card.TFrame", padding=12)
        list_card.pack(fill="both", expand=True, pady=(0, 10))

        top_row = ttk.Frame(list_card, style="Card.TFrame")
        top_row.pack(fill="x", pady=(0, 8))
        ttk.Label(top_row, text="Configured Backup Profiles", style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top_row, text="✕ Remove Selected", style="Danger.TButton", command=self.delete_selected_profile).pack(side="right", padx=4)
        ttk.Button(top_row, text="▶ Backup Selected", command=self.run_selected_backup).pack(side="right", padx=4)
        ttk.Button(top_row, text="↻ Refresh Status", style="Secondary.TButton", command=self._refresh_profiles_ui).pack(side="right", padx=4)

        cols = ("name", "source", "repo", "status")
        self.profiles_tree = ttk.Treeview(list_card, columns=cols, show="headings", height=6)
        self.profiles_tree.heading("name", text="Profile Name")
        self.profiles_tree.heading("source", text="Source Folder")
        self.profiles_tree.heading("repo", text="Target Repository")
        self.profiles_tree.heading("status", text="Status")
        self.profiles_tree.column("name", width=160)
        self.profiles_tree.column("source", width=320)
        self.profiles_tree.column("repo", width=320)
        self.profiles_tree.column("status", width=150)
        self.profiles_tree.pack(fill="both", expand=True)

        # Add New Profile Form
        add_card = ttk.Frame(self.tab_profiles, style="Card.TFrame", padding=12)
        add_card.pack(fill="x")
        ttk.Label(add_card, text="Add & Initialize New Backup Profile", style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))

        grid = ttk.Frame(add_card, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Profile Name:", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self.entry_name = ttk.Entry(grid)
        self.entry_name.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(grid, text="Source Folder:", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 8))
        self.entry_source = ttk.Entry(grid)
        self.entry_source.grid(row=1, column=1, sticky="ew", pady=4, padx=(0, 6))
        ttk.Button(grid, text="Browse...", style="Secondary.TButton", command=lambda: self._browse_into(self.entry_source, "Select Source Folder to Back Up")).grid(row=1, column=2, pady=4)

        ttk.Label(grid, text="Target Repo:", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 8))
        self.entry_repo = ttk.Entry(grid)
        self.entry_repo.grid(row=2, column=1, sticky="ew", pady=4, padx=(0, 6))
        ttk.Button(grid, text="Browse...", style="Secondary.TButton", command=lambda: self._browse_into(self.entry_repo, "Select or Create Target Repository Folder")).grid(row=2, column=2, pady=4)

        ttk.Button(add_card, text="+ Initialize & Save Profile", command=self.add_profile).pack(anchor="w", pady=(8, 0))

    # --- TAB 2: Restore Snapshot ---
    def _build_restore_tab(self):
        card = ttk.Frame(self.tab_restore, style="Card.TFrame", padding=12)
        card.pack(fill="both", expand=True)

        row1 = ttk.Frame(card, style="Card.TFrame")
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="Select Profile:", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.restore_combo = ttk.Combobox(row1, state="readonly", width=45)
        self.restore_combo.pack(side="left", padx=(0, 8))
        self.restore_combo.bind("<<ComboboxSelected>>", lambda e: self.load_snapshots("restore"))
        ttk.Button(row1, text="↻ Load Snapshots", style="Secondary.TButton", command=lambda: self.load_snapshots("restore")).pack(side="left")
        ttk.Button(row1, text="⚡ Restore 'latest'", style="Success.TButton", command=lambda: self.execute_restore("latest")).pack(side="right")
        ttk.Button(row1, text="▶ Restore Selected Snapshot", command=self.restore_selected_snapshot).pack(side="right", padx=6)

        row2 = ttk.Frame(card, style="Card.TFrame")
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Subfolder/File Filter (optional):", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.entry_restore_include = ttk.Entry(row2)
        self.entry_restore_include.pack(side="left", fill="x", expand=True)

        row3 = ttk.Frame(card, style="Card.TFrame")
        row3.pack(fill="x", pady=(4, 10))
        ttk.Label(row3, text="Custom Destination (optional):", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.entry_restore_target = ttk.Entry(row3)
        self.entry_restore_target.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(row3, text="Browse...", style="Secondary.TButton", command=lambda: self._browse_into(self.entry_restore_target, "Select Destination Folder for Restored Files")).pack(side="left")

        cols = ("num", "time", "short_id", "paths")
        self.restore_tree = ttk.Treeview(card, columns=cols, show="headings")
        self.restore_tree.heading("num", text="#")
        self.restore_tree.heading("time", text="Timestamp")
        self.restore_tree.heading("short_id", text="Snapshot ID")
        self.restore_tree.heading("paths", text="Backed Up Paths")
        self.restore_tree.column("num", width=50, anchor="center")
        self.restore_tree.column("time", width=200)
        self.restore_tree.column("short_id", width=130)
        self.restore_tree.column("paths", width=520)
        self.restore_tree.pack(fill="both", expand=True)

    # --- TAB 3: Inspect, Stats & Prune ---
    def _build_inspect_tab(self):
        card = ttk.Frame(self.tab_inspect, style="Card.TFrame", padding=12)
        card.pack(fill="both", expand=True)

        row1 = ttk.Frame(card, style="Card.TFrame")
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="Select Profile:", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.inspect_combo = ttk.Combobox(row1, state="readonly", width=40)
        self.inspect_combo.pack(side="left", padx=(0, 8))
        self.inspect_combo.bind("<<ComboboxSelected>>", lambda e: self.load_snapshots("inspect"))
        ttk.Button(row1, text="↻ Load Snapshots", style="Secondary.TButton", command=lambda: self.load_snapshots("inspect")).pack(side="left", padx=2)
        ttk.Button(row1, text="📊 Size & Deduplication Stats", command=self.run_stats).pack(side="left", padx=4)
        ttk.Button(row1, text="✔ Verify Integrity (check)", style="Secondary.TButton", command=self.run_check).pack(side="left", padx=2)

        row2 = ttk.Frame(card, style="Card.TFrame")
        row2.pack(fill="x", pady=6)
        ttk.Label(row2, text="Search File/Folder:", style="Card.TLabel").pack(side="left", padx=(0, 8))
        self.entry_find = ttk.Entry(row2, width=32)
        self.entry_find.pack(side="left", padx=(0, 6))
        ttk.Button(row2, text="🔍 Search Across Snapshots", style="Secondary.TButton", command=self.run_find).pack(side="left", padx=(0, 16))

        ttk.Label(row2, text="Keep Last N:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.spin_keep = ttk.Entry(row2, width=6)
        self.spin_keep.insert(0, "5")
        self.spin_keep.pack(side="left", padx=(0, 6))
        ttk.Button(row2, text="🗑 Keep Last N & Prune", style="Danger.TButton", command=self.run_prune_keep_last).pack(side="left")

        row3 = ttk.Frame(card, style="Card.TFrame")
        row3.pack(fill="x", pady=(2, 8))
        ttk.Button(row3, text="📂 Browse Files in Selected Snapshot (ls)", style="Secondary.TButton", command=self.run_ls_selected).pack(side="left", padx=(0, 8))
        ttk.Button(row3, text="🗑 Delete Selected Snapshot & Prune", style="Danger.TButton", command=self.run_prune_selected).pack(side="left")

        cols = ("num", "time", "short_id", "paths")
        self.inspect_tree = ttk.Treeview(card, columns=cols, show="headings")
        self.inspect_tree.heading("num", text="#")
        self.inspect_tree.heading("time", text="Timestamp")
        self.inspect_tree.heading("short_id", text="Snapshot ID")
        self.inspect_tree.heading("paths", text="Backed Up Paths")
        self.inspect_tree.column("num", width=50, anchor="center")
        self.inspect_tree.column("time", width=200)
        self.inspect_tree.column("short_id", width=130)
        self.inspect_tree.column("paths", width=520)
        self.inspect_tree.pack(fill="both", expand=True)

    # --- UI State & Logging Helpers ---
    def _update_status_badges(self):
        if RESTIC_BIN.is_file():
            self.restic_badge.configure(text="restic.exe Ready", bg="#065f46", fg="#ffffff")
        else:
            self.restic_badge.configure(text="restic.exe Missing in source/", bg="#991b1b", fg="#ffffff")

    def _browse_into(self, entry_widget: ttk.Entry, title: str):
        selected = filedialog.askdirectory(title=title, mustexist=False)
        if selected:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, str(Path(selected)))

    def _prompt_password(self):
        pw = simpledialog.askstring(
            "Master Encryption Password",
            f"Enter master encryption password for Restic backups\n(Saved to {PW_FILE}):",
            show="*",
            parent=self,
        )
        if pw and pw.strip():
            PW_FILE.write_text(pw.strip() + "\n", encoding="utf-8")
            self._log(f"[+] Master password saved to {PW_FILE}\n")
        elif not PW_FILE.is_file():
            self._log("[WARNING] No password.txt set yet. Set a password before initializing or running backups.\n")

    def _refresh_profiles_ui(self):
        self._update_status_badges()
        self.config_data = load_config()
        jobs = self.config_data.get("jobs", [])

        for item in self.profiles_tree.get_children():
            self.profiles_tree.delete(item)

        combo_labels = []
        for idx, job in enumerate(jobs):
            repo_ready = (Path(job["repo_dir"]) / "config").is_file()
            src_ready = Path(job["source_dir"]).is_dir()
            if repo_ready and src_ready:
                status = "Ready"
            elif not repo_ready:
                status = "Repo Offline / Uninitialized"
            else:
                status = "Source Offline"

            self.profiles_tree.insert("", "end", iid=str(idx), values=(job["name"], job["source_dir"], job["repo_dir"], status))
            combo_labels.append(f"[{idx + 1}] {job['name']} ({job['repo_dir']})")

        self.restore_combo["values"] = combo_labels
        self.inspect_combo["values"] = combo_labels
        if combo_labels:
            if self.restore_combo.current() == -1:
                self.restore_combo.current(0)
            if self.inspect_combo.current() == -1:
                self.inspect_combo.current(0)

    def _log(self, text: str):
        self.log_queue.put(("log", text))

    def _clear_console(self):
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", tk.END)
        self.console_text.configure(state="disabled")

    def _poll_queue(self):
        while not self.log_queue.empty():
            msg_type, payload = self.log_queue.get_nowait()
            if msg_type == "log":
                self.console_text.configure(state="normal")
                self.console_text.insert(tk.END, payload)
                self.console_text.see(tk.END)
                self.console_text.configure(state="disabled")
            elif msg_type == "status":
                self.status_lbl.configure(text=payload)
            elif msg_type == "done":
                self.task_running = False
                self.active_proc = None
                self.cancel_btn.state(["disabled"])
                self.status_lbl.configure(text="Idle — Ready")
                self._refresh_profiles_ui()
        self.after(100, self._poll_queue)

    def _start_background_task(self, title: str, worker_fn):
        if self.task_running:
            messagebox.showwarning("Task in Progress", "Another operation is currently running. Wait for it to finish or click Stop.")
            return
        if not RESTIC_BIN.is_file():
            messagebox.showerror("Missing Binary", f"restic binary not found at:\n{RESTIC_BIN}\n\nPlease place restic.exe in the source/ folder.")
            return
        if not PW_FILE.is_file():
            self._prompt_password()
            if not PW_FILE.is_file():
                return

        self.task_running = True
        self.cancel_requested = False
        self.cancel_btn.state(["!disabled"])
        self.log_queue.put(("status", f"⏳ Running: {title}"))
        write_log_header(title)
        self._log(f"\n=== {title} ===\n")

        def _thread_target():
            try:
                worker_fn()
            finally:
                self.log_queue.put(("done", None))

        threading.Thread(target=_thread_target, daemon=True).start()

#     def _exec_restic_stream(self, args: list) -> int:
#         full_cmd = [str(RESTIC_BIN)] + args
#         self.active_proc = subprocess.Popen(
#             full_cmd,
#             stdout=subprocess.PIPE,
#             stderr=subprocess.STDOUT,
#             text=True,
#             encoding="utf-8",
#             errors="replace",
#             creationflags=CREATE_NO_WINDOW,
#         )
#         for line in self.active_proc.stdout:
#             self._log(line)
#             append_log_file(line)
#         self.active_proc.wait()
#         rc = self.active_proc.returncode
#         self.active_proc = None
#         return rc
#
    def _exec_restic_stream(self, args: list[str]) -> int:
        full_cmd = [str(RESTIC_BIN)] + args
        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        self.active_proc = proc
        if proc.stdout is not None:
            for line in proc.stdout:
                self._log(line)
                append_log_file(line)
        rc = proc.wait()
        self.active_proc = None
        return rc

    def cancel_active_task(self):
        self.cancel_requested = True
        if self.active_proc and self.active_proc.poll() is None:
            try:
                self.active_proc.terminate()
                self._log("\n[!] Operation cancelled by user.\n")
            except Exception as e:
                self._log(f"\n[ERROR] Failed to terminate process: {e}\n")

    # --- Actions: Profiles & Backups ---
    def add_profile(self):
        name = self.entry_name.get().strip()
        src = self.entry_source.get().strip()
        repo = self.entry_repo.get().strip()

        if not name or not src or not repo:
            messagebox.showwarning("Missing Fields", "Please enter a Profile Name, Source Folder, and Target Repository.")
            return

        src_path = str(Path(src))
        repo_path = str(Path(repo))

        def worker():
            repo_cfg = Path(repo_path) / "config"
            if repo_cfg.is_file():
                self._log(f"[INFO] Existing Restic repository found at {repo_path}.\n")
            else:
                self._log(f"Initializing new repository at {repo_path}...\n")
                rc = self._exec_restic_stream(["init", "--repo", repo_path, "--password-file", str(PW_FILE)])
                if rc != 0:
                    self._log("[ERROR] Repository initialization failed. Profile was not saved.\n")
                    return

            self.config_data.setdefault("jobs", []).append({
                "name": name,
                "source_dir": src_path,
                "repo_dir": repo_path,
            })
            save_config(self.config_data)
            self._log(f"[+] Profile '{name}' saved to {CONFIG_FILE}!\n")

        self.entry_name.delete(0, tk.END)
        self.entry_source.delete(0, tk.END)
        self.entry_repo.delete(0, tk.END)
        self._start_background_task(f"Initialize Profile: {name}", worker)

    def delete_selected_profile(self):
        sel = self.profiles_tree.selection()
        if not sel:
            messagebox.showinfo("Select Profile", "Please select a profile from the table first.")
            return
        idx = int(sel[0])
        jobs = self.config_data.get("jobs", [])
        if idx < 0 or idx >= len(jobs):
            return
        job_name = jobs[idx]["name"]
        if not messagebox.askyesno("Confirm Removal", f"Remove profile '{job_name}' from config.json?\n(Backup files on your drive will not be deleted.)"):
            return
        jobs.pop(idx)
        save_config(self.config_data)
        self._refresh_profiles_ui()
        self._log(f"[INFO] Removed profile '{job_name}' from configuration.\n")

    def _backup_single_job_worker(self, job: dict) -> bool:
        name = job["name"]
        src = Path(job["source_dir"])
        repo = Path(job["repo_dir"])

        self._log(f"\n--- Backing Up [{name}] ---\nSource: {src}\nTarget: {repo}\n\n")
        if not (repo / "config").is_file():
            self._log(f"[ERROR] Repository not found at {repo}. Connect drive or initialize first.\n")
            return False
        if not src.is_dir():
            self._log(f"[ERROR] Source folder '{src}' does not exist.\n")
            return False

        args = ["-r", str(repo), "--password-file", str(PW_FILE), "backup", str(src)]
        if is_admin() and os.name == "nt":
            args.append("--use-fs-snapshot")
        else:
            self._log("[WARNING] Running without Administrator rights; VSS (--use-fs-snapshot) skipped.\n")

        rc = self._exec_restic_stream(args)
        if rc == 0:
            self._log(f"\n[+] Backup completed successfully for [{name}]!\n")
            return True
        self._log(f"\n[!] Backup failed for [{name}] (exit code {rc}).\n")
        return False

    def run_selected_backup(self):
        sel = self.profiles_tree.selection()
        if not sel:
            messagebox.showinfo("Select Profile", "Please select a profile to back up.")
            return
        idx = int(sel[0])
        job = self.config_data["jobs"][idx]
        self._start_background_task(f"Backup: {job['name']}", lambda: self._backup_single_job_worker(job))

    def run_all_backups(self):
        jobs = list(self.config_data.get("jobs", []))
        if not jobs:
            messagebox.showinfo("No Profiles", "Add a backup profile first.")
            return

        def worker():
            ok_list, fail_list = [], []
            for job in jobs:
                if self.cancel_requested:
                    break
                if self._backup_single_job_worker(job):
                    ok_list.append(job["name"])
                else:
                    fail_list.append(job["name"])
            self._log(
                f"\n===================================================\n"
                f"Backup Summary — Succeeded ({len(ok_list)}): {', '.join(ok_list) or 'None'} | "
                f"Failed ({len(fail_list)}): {', '.join(fail_list) or 'None'}\n"
                f"===================================================\n"
            )

        self._start_background_task("Run ALL Backups", worker)

    # --- Actions: Snapshots, Restore, Inspect & Prune ---
    def load_snapshots(self, mode: str):
        combo = self.restore_combo if mode == "restore" else self.inspect_combo
        tree = self.restore_tree if mode == "restore" else self.inspect_tree
        idx = combo.current()
        jobs = self.config_data.get("jobs", [])
        if idx < 0 or idx >= len(jobs):
            return

        job = jobs[idx]
        repo = Path(job["repo_dir"])
        for item in tree.get_children():
            tree.delete(item)

        if not (repo / "config").is_file():
            self._log(f"[ERROR] Repository for '{job['name']}' not found at {repo}.\n")
            return

        try:
            res = subprocess.run(
                [str(RESTIC_BIN), "-r", str(repo), "--password-file", str(PW_FILE), "snapshots", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode != 0:
                self._log(f"[ERROR] Failed to load snapshots: {res.stderr}\n")
                return
            snaps = json.loads(res.stdout or "[]")
            self.current_snapshots = snaps
            for i, s in enumerate(snaps):
                raw_time = s.get("time", "")[:19].replace("T", " ")
                short_id = s.get("short_id", "")
                paths = ", ".join(s.get("paths", []))
                tree.insert("", "end", iid=short_id, values=(i + 1, raw_time, short_id, paths))
            self._log(f"[+] Loaded {len(snaps)} snapshot(s) for [{job['name']}].\n")
        except Exception as e:
            self._log(f"[ERROR] Could not parse snapshots: {e}\n")

    def restore_selected_snapshot(self):
        sel = self.restore_tree.selection()
        if not sel:
            messagebox.showinfo("Select Snapshot", "Select a snapshot from the table (or click Restore 'latest').")
            return
        self.execute_restore(sel[0])

    def execute_restore(self, snap_id: str):
        idx = self.restore_combo.current()
        jobs = self.config_data.get("jobs", [])
        if idx < 0 or idx >= len(jobs):
            return
        job = jobs[idx]

        safe_name = job["name"].replace(" ", "_")
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        custom_base = self.entry_restore_target.get().strip()
        base_dir = Path(custom_base) if custom_base else RESTORE_BASE_DIR
        target_dir = base_dir / f"restored_{safe_name}_{ts}"

        inc_filter = normalize_include_path(self.entry_restore_include.get())

        def worker():
            target_dir.mkdir(parents=True, exist_ok=True)
            args = ["-r", job["repo_dir"], "--password-file", str(PW_FILE), "restore", snap_id, "--target", str(target_dir)]
            if inc_filter:
                self._log(f"[INFO] Applying path filter: --include {inc_filter}\n")
                args.extend(["--include", inc_filter])
            self._log(f"Restoring snapshot '{snap_id}' into:\n{target_dir}\n\n")
            rc = self._exec_restic_stream(args)
            if rc == 0:
                self._log(f"\n[+] Restore completed! Files saved in:\n{target_dir}\n")
            else:
                self._log(f"\n[!] Restore failed (exit code {rc}).\n")

        self._start_background_task(f"Restore [{job['name']}] ({snap_id})", worker)

    def _get_inspect_job(self):
        idx = self.inspect_combo.current()
        jobs = self.config_data.get("jobs", [])
        if idx < 0 or idx >= len(jobs):
            messagebox.showinfo("Select Profile", "Please select a profile first.")
            return None
        return jobs[idx]

    def run_stats(self):
        job = self._get_inspect_job()
        if not job:
            return

        def worker():
            self._log("[1/2] Calculating uncompressed restore size (latest snapshot)...\n")
            self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "stats", "latest"])
            self._log("\n[2/2] Calculating actual deduplicated disk space used (raw-data)...\n")
            self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "stats", "--mode", "raw-data"])

        self._start_background_task(f"Stats [{job['name']}]", worker)

    def run_check(self):
        job = self._get_inspect_job()
        if not job:
            return
        self._start_background_task(
            f"Verify Integrity [{job['name']}]",
            lambda: self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "check"]),
        )

    def run_find(self):
        job = self._get_inspect_job()
        if not job:
            return
        pattern = self.entry_find.get().strip()
        if not pattern:
            messagebox.showinfo("Enter Pattern", "Enter a filename or wildcard pattern (e.g. *.pst or report) to search.")
            return
        self._start_background_task(
            f"Search '{pattern}' in [{job['name']}]",
            lambda: self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "find", pattern]),
        )

    def run_ls_selected(self):
        job = self._get_inspect_job()
        if not job:
            return
        sel = self.inspect_tree.selection()
        snap_id = sel[0] if sel else "latest"
        self._start_background_task(
            f"Browse Files [{job['name']}] ({snap_id})",
            lambda: self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "ls", "-l", snap_id]),
        )

    def run_prune_keep_last(self):
        job = self._get_inspect_job()
        if not job:
            return
        count = self.spin_keep.get().strip()
        if not count.isdigit() or int(count) < 1:
            messagebox.showwarning("Invalid Number", "Enter a positive integer for Keep Last N.")
            return
        if not messagebox.askyesno("Confirm Prune", f"Permanently delete all snapshots except the last {count} in [{job['name']}] and prune disk space?"):
            return
        self._start_background_task(
            f"Prune (Keep Last {count}) [{job['name']}]",
            lambda: self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "forget", "--keep-last", count, "--prune"]),
        )

    def run_prune_selected(self):
        job = self._get_inspect_job()
        if not job:
            return
        sel = self.inspect_tree.selection()
        if not sel:
            messagebox.showinfo("Select Snapshot", "Select a snapshot from the table to delete.")
            return
        snap_id = sel[0]
        if not messagebox.askyesno("Confirm Snapshot Deletion", f"Permanently delete snapshot '{snap_id}' from [{job['name']}] and prune disk space?"):
            return
        self._start_background_task(
            f"Delete Snapshot {snap_id} [{job['name']}]",
            lambda: self._exec_restic_stream(["-r", job["repo_dir"], "--password-file", str(PW_FILE), "forget", snap_id, "--prune"]),
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "backup-all":
        sys.exit(run_headless_backup_all())

    app = ResticManagerApp()
    app.mainloop()
