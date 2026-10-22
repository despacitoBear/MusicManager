#!/usr/bin/env python3
"""
Music Manager - Cross-platform GUI tool for:
 - Editing music file metadata (batch support)
 - Converting FLAC to MP3 / ALAC
 - Extracting audio from video files
 - Converting AVI/MOV to MP4

Requirements:
  pip install mutagen Pillow
  ffmpeg must be installed and in PATH (or common install locations)
"""

import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def check_import(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False

MUTAGEN_OK = check_import("mutagen")
PILLOW_OK  = check_import("PIL")
YTDLP_OK   = check_import("yt_dlp")

if not MUTAGEN_OK:
    print("mutagen not found – run:  pip install mutagen")
if not PILLOW_OK:
    print("Pillow not found – run:  pip install Pillow")
if not YTDLP_OK:
    print("yt-dlp not found – run:  pip install yt-dlp")

# ---------------------------------------------------------------------------
# FFmpeg helper
# ---------------------------------------------------------------------------

def find_ffmpeg():
    candidates = []

    # 1. Bundled inside PyInstaller .exe (highest priority)
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
        for name in ("ffmpeg", "ffmpeg.exe"):
            candidates.append(str(base / name))

    # 2. Same folder as the script / exe
    script_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) \
                 else Path(__file__).parent
    for name in ("ffmpeg", "ffmpeg.exe"):
        candidates.append(str(script_dir / name))

    # 3. System PATH and common install locations
    candidates += [
        "ffmpeg",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0-full_build\bin\ffmpeg.exe"),
    ]

    for c in candidates:
        try:
            subprocess.run([c, "-version"], capture_output=True, check=True)
            return c
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None

FFMPEG = find_ffmpeg()

# ---------------------------------------------------------------------------
# Metadata helpers (mutagen)
# ---------------------------------------------------------------------------

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aiff", ".opus"}

def get_audio_tags(path):
    """Return a dict with common tag keys for a file."""
    if not MUTAGEN_OK:
        return {}
    from mutagen import File
    from mutagen.id3 import ID3NoHeaderError
    try:
        f = File(path, easy=True)
        if f is None:
            return {}
        tags = {
            "title":  (f.get("title",  [""])[0] if f.get("title")  else ""),
            "artist": (f.get("artist", [""])[0] if f.get("artist") else ""),
            "album":  (f.get("album",  [""])[0] if f.get("album")  else ""),
            "genre":  (f.get("genre",  [""])[0] if f.get("genre")  else ""),
            "date":   (f.get("date",   [""])[0] if f.get("date")   else ""),
            "tracknumber": (f.get("tracknumber", [""])[0] if f.get("tracknumber") else ""),
        }
        return tags
    except Exception as e:
        logger.warning(f"Could not read tags from {path}: {e}")
        return {}

def set_audio_tags(path, tags, artwork_path=None, artwork_size=None):
    """Write tags (and optionally artwork) to a file."""
    if not MUTAGEN_OK:
        return False, "mutagen not installed"
    from mutagen import File
    try:
        f = File(path, easy=True)
        if f is None:
            return False, "Unsupported format"
        for key, val in tags.items():
            if val.strip():
                f[key] = val.strip()
            else:
                try:
                    del f[key]
                except KeyError:
                    pass
        f.save()

        # Artwork (non-easy interface needed)
        if artwork_path:
            _set_artwork(path, artwork_path, artwork_size)

        return True, "OK"
    except Exception as e:
        return False, str(e)

def _set_artwork(audio_path, img_path, size=None):
    """Embed artwork into an audio file, optionally resizing."""
    if not PILLOW_OK:
        return
    from PIL import Image
    import io
    ext = Path(audio_path).suffix.lower()

    # Load & resize image
    img = Image.open(img_path).convert("RGB")
    if size:
        img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    img_data = buf.getvalue()

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, APIC
            try:
                tags = ID3(audio_path)
            except Exception:
                from mutagen.id3 import ID3NoHeaderError
                from mutagen.mp3 import MP3
                mp3 = MP3(audio_path)
                mp3.add_tags()
                mp3.save()
                tags = ID3(audio_path)
            tags["APIC:"] = APIC(encoding=3, mime="image/jpeg", type=3,
                                  desc="Cover", data=img_data)
            tags.save(audio_path)
        elif ext == ".flac":
            from mutagen.flac import FLAC, Picture
            flac = FLAC(audio_path)
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = img_data
            flac.clear_pictures()
            flac.add_picture(pic)
            flac.save()
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            mp4 = MP4(audio_path)
            mp4["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
            mp4.save()
    except Exception as e:
        logger.warning(f"Could not embed artwork in {audio_path}: {e}")

# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def run_ffmpeg(cmd, progress_cb=None):
    if not FFMPEG:
        return False, "ffmpeg not found"
    try:
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)

        # On Windows: CREATE_NO_WINDOW suppresses the black console popup
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        full_cmd = [FFMPEG, "-nostdin"] + cmd
        result = subprocess.run(
            full_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, **kwargs
        )
        if result.returncode != 0:
            return False, result.stderr[-600:]
        return True, "OK"
    except Exception as e:
        return False, str(e)

def flac_to_mp3(src, quality="320k", dst=None):
    if dst is None:
        dst = Path(src).with_suffix(".mp3")
    dst = Path(dst)
    if dst.exists():
        return True, f"Skipped (exists): {dst.name}"
    ok, msg = run_ffmpeg([
        "-i", str(src),
        "-vn",
        "-codec:a", "libmp3lame",
        "-b:a", quality,
        "-map_metadata", "0",
        "-id3v2_version", "3",
        "-write_id3v1", "1",
        "-loglevel", "error",
        "-y", str(dst)
    ])
    return ok, msg

def flac_to_alac(src, dst=None):
    if dst is None:
        dst = Path(src).with_suffix(".m4a")
    dst = Path(dst)
    if dst.exists():
        return True, f"Skipped (exists): {dst.name}"
    ok, msg = run_ffmpeg([
        "-i", str(src),
        "-vn",
        "-codec:a", "alac",
        "-map_metadata", "0",
        "-loglevel", "error",
        "-y", str(dst)
    ])
    return ok, msg

def extract_audio(src, fmt="mp3", dst=None, quality="320k"):
    if dst is None:
        dst = Path(src).with_suffix(f".{fmt}")
    dst = Path(dst)
    if fmt == "flac":
        cmd = ["-i", str(src), "-vn", "-codec:a", "flac",
               "-loglevel", "error", "-y", str(dst)]
    else:
        cmd = ["-i", str(src), "-vn", "-codec:a", "libmp3lame",
               "-b:a", quality, "-loglevel", "error", "-y", str(dst)]
    ok, msg = run_ffmpeg(cmd)
    return ok, msg

def convert_to_mp4(src, dst=None):
    if dst is None:
        dst = Path(src).with_suffix(".mp4")
    dst = Path(dst)
    if dst.exists():
        return True, f"Skipped (exists): {dst.name}"
    ok, msg = run_ffmpeg([
        "-i", str(src),
        "-codec:v", "libx264",
        "-crf", "23", "-preset", "medium",
        "-codec:a", "aac", "-b:a", "192k",
        "-loglevel", "error",
        "-y", str(dst)
    ])
    return ok, msg

# ---------------------------------------------------------------------------
# Styled helpers
# ---------------------------------------------------------------------------

BG      = "#1e1e2e"
BG2     = "#2a2a3e"
ACCENT  = "#7c5cbf"
FG      = "#e0e0f0"
FG2     = "#a0a0c0"
BTN_BG  = "#3a3a5a"
# macOS draws native light buttons and ignores -bg no matter what bd is, so
# button text has to be dark there. Other platforms honour BTN_BG (dark) and
# need light text instead.
IS_MAC   = sys.platform == "darwin"
BTN_FG   = "#000000" if IS_MAC else FG
BTN_FG2  = "#4a4a4a" if IS_MAC else FG2
BTN_FGD  = "#8a8a8a" if IS_MAC else FG2   # disabled
SEL_BG  = "#4a4a7a"
FONT    = ("Segoe UI", 10) if sys.platform == "win32" else ("Helvetica Neue", 12)
FONT_B  = (FONT[0], FONT[1], "bold")

def styled_btn(parent, text, command, **kw):
    kw.setdefault("bg", BTN_BG)
    kw.setdefault("fg", BTN_FG)
    kw.setdefault("activebackground", ACCENT)
    kw.setdefault("activeforeground", BTN_FG if IS_MAC else "#fff")
    kw.setdefault("disabledforeground", BTN_FGD)
    kw.setdefault("relief", "flat")
    # bd/highlightthickness must be 0: with a border, macOS draws a native
    # light button and ignores -bg, leaving pale text on white.
    kw.setdefault("bd", 0)
    kw.setdefault("highlightthickness", 0)
    kw.setdefault("padx", 10)
    kw.setdefault("pady", 4)
    kw.setdefault("font", FONT)
    kw.setdefault("cursor", "hand2")
    return tk.Button(parent, text=text, command=command, **kw)

def styled_label(parent, text, **kw):
    kw.setdefault("font", FONT)
    kw.setdefault("bg", BG)
    kw.setdefault("fg", FG)
    return tk.Label(parent, text=text, **kw)

def styled_entry(parent, textvariable=None, **kw):
    e = tk.Entry(parent, textvariable=textvariable,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", font=FONT, **kw)
    return e

def styled_option(parent, variable, *values):
    om = tk.OptionMenu(parent, variable, *values)
    om.config(bg=BTN_BG, fg=BTN_FG, activebackground=ACCENT,
              activeforeground=BTN_FG if IS_MAC else "#fff", relief="flat",
              font=FONT, bd=0, highlightthickness=0, indicatoron=False,
              padx=10, pady=4)
    om["menu"].config(bg=BTN_BG, fg=BTN_FG, font=FONT,
                      activebackground=ACCENT,
                      activeforeground=BTN_FG if IS_MAC else "#fff")
    return om


# ---------------------------------------------------------------------------
# Desktop notification helper (cross-platform)
# ---------------------------------------------------------------------------

def notify(title, message):
    """Send a native desktop notification."""
    try:
        if sys.platform == "win32":
            # Use PowerShell toast notification (no extra libs needed)
            ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = "{title}"
$notify.BalloonTipText = "{message}"
$notify.Visible = $true
$notify.ShowBalloonTip(4000)
Start-Sleep -Milliseconds 4500
$notify.Dispose()
"""
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"']
            )
        else:
            # Linux: try notify-send
            subprocess.Popen(["notify-send", title, message])
    except Exception:
        pass  # Notifications are best-effort — never crash the app

# ---------------------------------------------------------------------------
# Error log dialog
# ---------------------------------------------------------------------------

def show_errors(parent, errors):
    """Show a scrollable window listing all errors."""
    win = tk.Toplevel(parent)
    win.title("Errors")
    win.configure(bg=BG)
    win.geometry("700x400")
    win.resizable(True, True)

    tk.Label(win, text=f"\u26a0  {len(errors)} error(s) occurred:",
             bg=BG, fg="#ffaaaa", font=FONT_B).pack(anchor="w", padx=12, pady=(10, 4))

    frame = tk.Frame(win, bg=BG)
    frame.pack(fill="both", expand=True, padx=12, pady=4)

    sb = tk.Scrollbar(frame)
    sb.pack(side="right", fill="y")
    txt = tk.Text(frame, bg="#2a1a1a", fg="#ffcccc", font=(FONT[0], 9),
                  wrap="word", relief="flat", yscrollcommand=sb.set)
    txt.pack(fill="both", expand=True)
    sb.config(command=txt.yview)

    for path, msg in errors:
        txt.insert("end", f"FILE: {path}\n", "file")
        txt.insert("end", f"{msg}\n\n", "msg")
    txt.tag_config("file", foreground="#ff8888", font=(FONT[0], 9, "bold"))
    txt.tag_config("msg",  foreground="#ffcccc")
    txt.config(state="disabled")

    styled_btn(win, "Close", win.destroy).pack(pady=8)

# ---------------------------------------------------------------------------
# Tab: Metadata Editor
# ---------------------------------------------------------------------------

class MetadataTab(ttk.Frame):
    FIELDS = [
        ("Title",   "title"),
        ("Artist",  "artist"),
        ("Album",   "album"),
        ("Genre",   "genre"),
        ("Year",    "date"),
        ("Track #", "tracknumber"),
    ]

    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self.files        = []
        self.artwork_path = None
        self._mode        = "none"
        self._build()

    # ── UI ───────────────────────────────────────────────────────────

    def _build(self):
        # Toolbar
        bar = tk.Frame(self, bg=BG2, pady=6)
        bar.pack(fill="x")
        styled_btn(bar, "\U0001f4c2  Add Files",  self._add_files).pack(side="left", padx=(12,4))
        styled_btn(bar, "\U0001f4c1  Add Folder", self._add_folder).pack(side="left", padx=4)
        styled_btn(bar, "\u2796  Remove",          self._remove_selected).pack(side="left", padx=4)
        styled_btn(bar, "\U0001f5d1  Clear All",   self._clear).pack(side="left", padx=4)
        tk.Label(bar, text="Ctrl+A = select all     Shift/Ctrl+click = multi-select",
                 bg=BG2, fg=FG2, font=(FONT[0], 8)).pack(side="right", padx=12)

        # PanedWindow body
        pane = tk.PanedWindow(self, orient="horizontal", bg="#111",
                              sashwidth=5, sashrelief="flat", bd=0)
        pane.pack(fill="both", expand=True, padx=8, pady=8)

        # ── Left: file tree ──────────────────────────────────────────
        left = tk.Frame(pane, bg=BG)
        pane.add(left, minsize=260, width=430, stretch="always")

        style = ttk.Style()
        style.configure("Meta.Treeview", background=BG2, foreground=FG,
                        fieldbackground=BG2, rowheight=22, font=FONT)
        style.configure("Meta.Treeview.Heading", background=BTN_BG,
                        foreground=FG2, font=(FONT[0], FONT[1], "bold"), relief="flat")
        style.map("Meta.Treeview",
                  background=[("selected", SEL_BG)],
                  foreground=[("selected", "#fff")])

        self.tree = ttk.Treeview(left, columns=("artist","title"),
                                 show="tree headings",
                                 selectmode="extended",
                                 style="Meta.Treeview")
        self.tree.heading("#0",     text="File",   anchor="w")
        self.tree.heading("artist", text="Artist", anchor="w")
        self.tree.heading("title",  text="Title",  anchor="w")
        self.tree.column("#0",      width=200, stretch=True)
        self.tree.column("artist",  width=130, stretch=True)
        self.tree.column("title",   width=130, stretch=True)

        vsb = ttk.Scrollbar(left, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # ── Right: editor ────────────────────────────────────────────
        right = tk.Frame(pane, bg=BG)
        pane.add(right, minsize=300, width=340, stretch="never")

        # Mode banner
        self.banner = tk.Label(right, text="\u2190 Select a file to edit",
                               bg="#22223a", fg=FG2, font=FONT,
                               anchor="w", padx=10, pady=7)
        self.banner.pack(fill="x", pady=(0,6))

        # Tag fields
        fields_frame = tk.Frame(right, bg=BG)
        fields_frame.pack(fill="x", padx=6)

        self.tag_vars    = {}
        self.tag_entries = {}
        self.tag_checks  = {}
        self.tag_enabled = {}

        for label, key in self.FIELDS:
            row = tk.Frame(fields_frame, bg=BG)
            row.pack(fill="x", pady=3)

            cb_var = tk.BooleanVar(value=False)
            self.tag_enabled[key] = cb_var
            cb = tk.Checkbutton(row, variable=cb_var, bg=BG, fg=FG,
                                selectcolor="#3a3a5a", activebackground=BG,
                                relief="flat", cursor="hand2",
                                command=lambda k=key: self._toggle_field(k))
            self.tag_checks[key] = cb

            styled_label(row, f"{label}:", width=9, anchor="e").pack(side="left")

            var = tk.StringVar()
            self.tag_vars[key] = var
            e = styled_entry(row, textvariable=var)
            e.pack(side="left", fill="x", expand=True, padx=(4,0))
            self.tag_entries[key] = e

        # Divider
        tk.Frame(right, bg=BG2, height=1).pack(fill="x", padx=6, pady=8)

        # Artwork
        art_outer = tk.Frame(right, bg=BG)
        art_outer.pack(fill="x", padx=6)
        styled_label(art_outer, "Artwork", font=FONT_B).pack(anchor="w")

        art_row = tk.Frame(art_outer, bg=BG)
        art_row.pack(fill="x", pady=4)

        self.art_canvas = tk.Canvas(art_row, width=64, height=64,
                                    bg="#1a1a2a", highlightthickness=1,
                                    highlightbackground="#444")
        self.art_canvas.pack(side="left", padx=(0,10))
        self._art_photo = None

        art_info = tk.Frame(art_row, bg=BG)
        art_info.pack(side="left", fill="both", expand=True)
        self.art_label = styled_label(art_info, "No image selected", fg=FG2)
        self.art_label.pack(anchor="w")
        styled_btn(art_info, "Choose Image\u2026", self._choose_artwork).pack(anchor="w", pady=3)

        size_row = tk.Frame(art_info, bg=BG)
        size_row.pack(anchor="w")
        styled_label(size_row, "Resize:", fg=FG2).pack(side="left")
        self.art_size = tk.StringVar(value="500")
        om = styled_option(size_row, self.art_size,
                           "Original","300","500","600","800","1000","1200")
        om.pack(side="left", padx=4)

        # Divider
        tk.Frame(right, bg=BG2, height=1).pack(fill="x", padx=6, pady=8)

        # Save + status
        self.save_btn = styled_btn(right, "\U0001f4be  Save", self._save_tags,
                                   bg=ACCENT, activebackground="#9a7ad8",
                                   state="disabled")
        self.save_btn.pack(fill="x", padx=6, pady=(0,4))

        self.status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.status_var, bg=BG, fg=FG2,
                 font=(FONT[0], 8), wraplength=300, justify="left").pack(
                 anchor="w", padx=6)

        self._set_mode("none")

    # ── Mode ─────────────────────────────────────────────────────────

    def _set_mode(self, mode):
        self._mode = mode

        # Hide all checkboxes first
        for key in self.tag_checks:
            self.tag_checks[key].pack_forget()

        if mode == "none":
            self.banner.config(text="\u2190 Add files and select one to edit",
                               bg="#22223a", fg=FG2)
            self.save_btn.config(state="disabled", text="\U0001f4be  Save")
            for key in self.tag_vars:
                self.tag_vars[key].set("")
                self.tag_entries[key].config(state="disabled", bg="#1e1e30")

        elif mode == "single":
            self.banner.config(text="\u270f  Single file \u2014 edit any field and save",
                               bg="#1a2e1a", fg="#88dd88")
            self.save_btn.config(state="normal", text="\U0001f4be  Save to This File")
            for key in self.tag_vars:
                self.tag_entries[key].config(state="normal", bg=BG2)

        elif mode == "multi":
            self.banner.config(
                text="\u2611  Multi-select \u2014 check fields to apply to all selected",
                bg="#2e2510", fg="#ddbb55")
            for key in self.tag_vars:
                self.tag_vars[key].set("")
                self.tag_enabled[key].set(False)
                # Pack checkbox before the label in its row
                check     = self.tag_checks[key]
                row_frame = self.tag_entries[key].master
                label     = next(w for w in row_frame.winfo_children() if w is not check)
                check.pack(in_=row_frame, side="left", before=label)
                self._toggle_field(key)
            self._refresh_save_label()

    def _toggle_field(self, key):
        if self.tag_enabled[key].get():
            self.tag_entries[key].config(state="normal", bg=BG2)
        else:
            self.tag_entries[key].config(state="disabled", bg="#1e1e30")
            self.tag_vars[key].set("")
        self._refresh_save_label()

    def _refresh_save_label(self):
        if self._mode != "multi":
            return
        sel     = self.tree.selection()
        checked = sum(1 for v in self.tag_enabled.values() if v.get())
        n       = len(sel)
        parts   = []
        if checked:
            parts.append(f"{checked} field(s)")
        if self.artwork_path:
            parts.append("artwork")
        if parts:
            self.save_btn.config(
                state="normal",
                text=f"\U0001f4be  Apply {' + '.join(parts)} to {n} file(s)")
        else:
            self.save_btn.config(
                state="disabled",
                text=f"\U0001f4be  Check fields above to apply to {n} file(s)")

    # ── File management ───────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio files", " ".join(f"*{e}" for e in AUDIO_EXTS)),
                       ("All files", "*.*")])
        for p in paths:
            if p not in self.files:
                self._insert_file(p)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder")
        if not folder:
            return
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    full = os.path.join(root, f)
                    if full not in self.files:
                        self._insert_file(full)

    def _insert_file(self, path):
        self.files.append(path)
        tags = get_audio_tags(path)
        iid  = str(len(self.files) - 1)
        self.tree.insert("", "end", iid=iid,
                         text=Path(path).name,
                         values=(tags.get("artist",""), tags.get("title","")))

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        # Collect paths to keep
        all_iids  = self.tree.get_children()
        sel_set   = set(sel)
        keep      = [self.files[int(iid)] for iid in all_iids if iid not in sel_set]
        self.tree.delete(*self.tree.get_children())
        self.files = []
        for p in keep:
            self._insert_file(p)
        self._set_mode("none")

    def _clear(self):
        self.files.clear()
        self.tree.delete(*self.tree.get_children())
        self.artwork_path = None
        self._art_clear_preview()
        self._set_mode("none")

    # ── Selection ─────────────────────────────────────────────────────

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            self._set_mode("none")
            return
        if len(sel) == 1:
            self._set_mode("single")
            path = self.files[int(sel[0])]
            tags = get_audio_tags(path)
            for k, v in self.tag_vars.items():
                v.set(tags.get(k, ""))
            self.status_var.set(Path(path).name)
            self._load_embedded_art(path)
        else:
            self._set_mode("multi")
            self.status_var.set(f"{len(sel)} files selected")
            self._art_clear_preview()

    # ── Artwork ───────────────────────────────────────────────────────

    def _choose_artwork(self):
        p = filedialog.askopenfilename(
            title="Select artwork",
            filetypes=[("Images","*.jpg *.jpeg *.png *.bmp *.webp"),("All","*.*")])
        if not p:
            return
        self.artwork_path = p
        self.art_label.config(text=Path(p).name, fg=FG)
        self._show_art_preview(p)
        self._refresh_save_label()

    def _load_embedded_art(self, path):
        if not PILLOW_OK or not MUTAGEN_OK:
            return
        try:
            import io
            from PIL import Image
            from mutagen import File as MFile
            f    = MFile(path)
            data = None
            if f and hasattr(f, "tags") and f.tags:
                for tag in f.tags.values():
                    if hasattr(tag, "data"):
                        data = tag.data; break
            if data is None and hasattr(f, "pictures") and f.pictures:
                data = f.pictures[0].data
            if data:
                self._show_pil_preview(Image.open(io.BytesIO(data)))
                self.art_label.config(text="Embedded artwork", fg=FG2)
                return
        except Exception:
            pass
        self._art_clear_preview()
        self.art_label.config(text="No artwork", fg=FG2)

    def _show_art_preview(self, path):
        if not PILLOW_OK:
            return
        try:
            from PIL import Image
            self._show_pil_preview(Image.open(path))
        except Exception:
            pass

    def _show_pil_preview(self, img):
        if not PILLOW_OK:
            return
        try:
            from PIL import Image, ImageTk
            img = img.copy().convert("RGB")
            img.thumbnail((64, 64), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._art_photo = photo
            self.art_canvas.delete("all")
            self.art_canvas.create_image(32, 32, image=photo)
        except Exception:
            pass

    def _art_clear_preview(self):
        self.art_canvas.delete("all")
        self._art_photo = None
        self.art_label.config(text="No image selected", fg=FG2)

    # ── Save ──────────────────────────────────────────────────────────

    def _save_tags(self):
        if not MUTAGEN_OK:
            messagebox.showerror("Missing dependency","Install mutagen:\n  pip install mutagen")
            return

        sel = self.tree.selection()
        if not sel:
            return

        size_str = self.art_size.get()
        size     = None if size_str == "Original" else int(size_str)
        multi    = len(sel) > 1

        if multi:
            tags_to_write = {
                k: self.tag_vars[k].get()
                for k in self.tag_vars
                if self.tag_enabled[k].get() and self.tag_vars[k].get().strip()
            }
            if not tags_to_write and not self.artwork_path:
                messagebox.showinfo("Nothing to apply",
                    "Check at least one field to apply to the selected files.")
                return
        else:
            tags_to_write = {k: v.get() for k, v in self.tag_vars.items()}

        self.save_btn.config(state="disabled", text="\U0001f4be  Saving\u2026")
        errors = []

        def run():
            ok_count = err_count = 0
            for iid in sel:
                idx = int(iid)
                ok, msg = set_audio_tags(
                    self.files[idx], tags_to_write,
                    artwork_path=self.artwork_path, artwork_size=size)
                if ok:
                    ok_count += 1
                    new_tags = get_audio_tags(self.files[idx])
                    self.tree.item(iid, values=(
                        new_tags.get("artist",""), new_tags.get("title","")))
                else:
                    err_count += 1
                    errors.append((self.files[idx], msg))

            lbl = "Save to This File" if not multi else f"Apply to {len(sel)} file(s)"
            self.save_btn.config(state="normal", text=f"\U0001f4be  {lbl}")
            self.status_var.set(f"Saved: {ok_count}   Errors: {err_count}")
            if errors:
                notify("Music Manager \u2014 Errors",
                       f"{len(errors)} file(s) failed while saving tags")
                show_errors(self, errors)
            else:
                notify("Music Manager \u2014 Done",
                       f"Tags saved to {ok_count} file(s)")

        threading.Thread(target=run, daemon=True).start()
# ---------------------------------------------------------------------------
# Tab: FLAC → MP3 / ALAC
# ---------------------------------------------------------------------------

class FlacTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self.files = []
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=8)
        styled_btn(top, "📂  Add FLAC Files",          self._add_files).pack(side="left", padx=4)
        styled_btn(top, "📁  Add Folder (recursive)",  self._add_folder).pack(side="left", padx=4)
        styled_btn(top, "🗑  Clear",                   self._clear).pack(side="left", padx=4)

        format_frame = tk.Frame(top, bg=BG)
        format_frame.pack(side="left", padx=20)
        styled_label(format_frame, "Output format:").pack(side="left")
        self.output_format = tk.StringVar(value="mp3")
        format_menu = styled_option(format_frame, self.output_format, "mp3", "alac")
        format_menu.pack(side="left", padx=4)

        q_frame = tk.Frame(top, bg=BG)
        q_frame.pack(side="left", padx=20)
        styled_label(q_frame, "Quality:").pack(side="left")
        self.quality = tk.StringVar(value="320k")
        om = styled_option(q_frame, self.quality, "128k", "192k", "256k", "320k")
        om.pack(side="left", padx=4)

        dest = tk.Frame(self, bg=BG2, pady=5)
        dest.pack(fill="x", padx=12)
        styled_label(dest, "Save to:", fg=FG2).pack(side="left", padx=(0, 6))
        self.dest_var = tk.StringVar(value="Same folder as source")
        styled_entry(dest, textvariable=self.dest_var, width=48).pack(side="left", padx=4)
        styled_btn(dest, "Browse", self._browse_dest).pack(side="left", padx=4)
        styled_btn(dest, "Same as source", lambda: self.dest_var.set("Same folder as source")).pack(side="left")

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=12, pady=4)
        sb = tk.Scrollbar(mid)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(mid, yscrollcommand=sb.set,
                                  bg=BG2, fg=FG, selectmode="extended",
                                  selectbackground=SEL_BG, font=FONT, relief="flat")
        self.listbox.pack(fill="both", expand=True)
        sb.config(command=self.listbox.yview)

        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=12, pady=8)
        self.convert_btn = styled_btn(bot, "▶  Convert All", self._convert,
                                      bg=ACCENT, activebackground="#9a7ad8")
        self.convert_btn.pack(side="left")
        self.progress = ttk.Progressbar(bot, length=300, mode="determinate")
        self.progress.pack(side="left", padx=10)
        self.status_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self.status_var, bg=BG, fg=FG2, font=FONT).pack(side="left")

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Select destination folder")
        if d:
            self.dest_var.set(d)

    def _get_dest(self, src_path):
        d = self.dest_var.get()
        return Path(src_path).parent if d == "Same folder as source" else Path(d)

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select FLAC files",
            filetypes=[("FLAC files", "*.flac"), ("All files", "*.*")])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", Path(p).name)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder with FLAC files")
        if not folder:
            return
        found = 0
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                if f.lower().endswith(".flac"):
                    full = os.path.join(root, f)
                    if full not in self.files:
                        self.files.append(full)
                        self.listbox.insert("end", Path(full).name)
                        found += 1
        self.status_var.set(f"Added {found} FLAC file(s)." if found else "No FLAC files found.")

    def _clear(self):
        self.files.clear()
        self.listbox.delete(0, "end")
        self.status_var.set("")

    def _convert(self):
        if not FFMPEG:
            messagebox.showerror("ffmpeg not found", "Please install ffmpeg.")
            return
        if not self.files:
            self.status_var.set("No files in queue.")
            return
        quality = self.quality.get()
        files   = list(self.files)
        self.progress["maximum"] = len(files)
        self.progress["value"]   = 0
        self.convert_btn.config(state="disabled", text="⏳  Converting...")
        errors = []

        def run():
            ok = err = skip = 0
            output_format = self.output_format.get()
            for i, f in enumerate(files):
                dest_dir = self._get_dest(f)
                dest_dir.mkdir(parents=True, exist_ok=True)
                if output_format == "alac":
                    dst = dest_dir / Path(f).with_suffix(".m4a").name
                    success, msg = flac_to_alac(f, dst)
                else:
                    dst = dest_dir / Path(f).with_suffix(".mp3").name
                    success, msg = flac_to_mp3(f, quality, dst)
                if "Skipped" in msg:
                    skip += 1
                elif success:
                    ok += 1
                else:
                    err += 1
                    errors.append((f, msg))
                self.progress["value"] = i + 1
                self.status_var.set(f"{i+1}/{len(files)}  —  {Path(f).name}")
                self.update_idletasks()
            self.convert_btn.config(state="normal", text="▶  Convert All")
            summary = f"Converted: {ok}  Skipped: {skip}  Errors: {err}"
            self.status_var.set(f"Done. {summary}")
            if errors:
                notify("Music Manager — Errors", f"{len(errors)} file(s) failed during FLAC to {output_format} conversion")
                show_errors(self, errors)
            else:
                notify("Music Manager — Done", f"FLAC to {output_format} conversion complete. {summary}")

        threading.Thread(target=run, daemon=True).start()
class ExtractTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self.files = []
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=8)
        styled_btn(top, "📂  Add Video Files",         self._add_files).pack(side="left", padx=4)
        styled_btn(top, "📁  Add Folder (recursive)",  self._add_folder).pack(side="left", padx=4)
        styled_btn(top, "🗑  Clear",                   self._clear).pack(side="left", padx=4)

        fmt_frame = tk.Frame(top, bg=BG)
        fmt_frame.pack(side="left", padx=20)
        styled_label(fmt_frame, "Output format:").pack(side="left")
        self.fmt = tk.StringVar(value="mp3")
        om = styled_option(fmt_frame, self.fmt, "mp3", "aac", "flac", "wav", "ogg")
        om.pack(side="left", padx=4)

        dest = tk.Frame(self, bg=BG2, pady=5)
        dest.pack(fill="x", padx=12)
        styled_label(dest, "Save to:", fg=FG2).pack(side="left", padx=(0, 6))
        self.dest_var = tk.StringVar(value="Same folder as source")
        styled_entry(dest, textvariable=self.dest_var, width=48).pack(side="left", padx=4)
        styled_btn(dest, "Browse", self._browse_dest).pack(side="left", padx=4)
        styled_btn(dest, "Same as source", lambda: self.dest_var.set("Same folder as source")).pack(side="left")

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=12, pady=4)
        sb = tk.Scrollbar(mid)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(mid, yscrollcommand=sb.set,
                                  bg=BG2, fg=FG, selectmode="extended",
                                  selectbackground=SEL_BG, font=FONT, relief="flat")
        self.listbox.pack(fill="both", expand=True)
        sb.config(command=self.listbox.yview)

        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=12, pady=8)
        self.extract_btn = styled_btn(bot, "▶  Extract Audio", self._extract,
                                      bg=ACCENT, activebackground="#9a7ad8")
        self.extract_btn.pack(side="left")
        self.progress = ttk.Progressbar(bot, length=300, mode="determinate")
        self.progress.pack(side="left", padx=10)
        self.status_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self.status_var, bg=BG, fg=FG2, font=FONT).pack(side="left")

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Select destination folder")
        if d:
            self.dest_var.set(d)

    def _get_dest(self, src_path):
        d = self.dest_var.get()
        return Path(src_path).parent if d == "Same folder as source" else Path(d)

    def _add_files(self):
        video_exts = [("Video files", "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v"),
                      ("All files", "*.*")]
        paths = filedialog.askopenfilenames(title="Select video files", filetypes=video_exts)
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", Path(p).name)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder with video files")
        if not folder:
            return
        VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
        found = 0
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in VIDEO_EXTS:
                    full = os.path.join(root, f)
                    if full not in self.files:
                        self.files.append(full)
                        self.listbox.insert("end", Path(full).name)
                        found += 1
        self.status_var.set(f"Added {found} video file(s)." if found else "No video files found.")

    def _clear(self):
        self.files.clear()
        self.listbox.delete(0, "end")
        self.status_var.set("")

    def _extract(self):
        if not FFMPEG:
            messagebox.showerror("ffmpeg not found", "Please install ffmpeg.")
            return
        if not self.files:
            self.status_var.set("No files in queue.")
            return
        fmt   = self.fmt.get()
        files = list(self.files)
        self.progress["maximum"] = len(files)
        self.progress["value"]   = 0
        self.extract_btn.config(state="disabled", text="⏳  Extracting...")
        errors = []

        def run():
            ok = err = 0
            for i, f in enumerate(files):
                dest_dir = self._get_dest(f)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dst = dest_dir / Path(f).with_suffix(f".{fmt}").name
                success, msg = extract_audio(f, fmt, dst)
                if success:
                    ok += 1
                else:
                    err += 1
                    errors.append((f, msg))
                self.progress["value"] = i + 1
                self.status_var.set(f"{i+1}/{len(files)}  —  {Path(f).name}")
                self.update_idletasks()
            self.extract_btn.config(state="normal", text="▶  Extract Audio")
            summary = f"Extracted: {ok}  Errors: {err}"
            self.status_var.set(f"Done. {summary}")
            if errors:
                notify("Music Manager — Errors", f"{len(errors)} file(s) failed during audio extraction")
                show_errors(self, errors)
            else:
                notify("Music Manager — Done", f"Audio extraction complete. {summary}")

        threading.Thread(target=run, daemon=True).start()
class VideoConvTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self.files = []
        self._build()

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=8)
        styled_btn(top, "📂  Add AVI / MOV Files",     self._add_files).pack(side="left", padx=4)
        styled_btn(top, "📁  Add Folder (recursive)",  self._add_folder).pack(side="left", padx=4)
        styled_btn(top, "🗑  Clear",                   self._clear).pack(side="left", padx=4)
        styled_label(top, "  →  MP4 (H.264 + AAC)", fg=FG2).pack(side="left", padx=16)

        dest = tk.Frame(self, bg=BG2, pady=5)
        dest.pack(fill="x", padx=12)
        styled_label(dest, "Save to:", fg=FG2).pack(side="left", padx=(0, 6))
        self.dest_var = tk.StringVar(value="Same folder as source")
        styled_entry(dest, textvariable=self.dest_var, width=48).pack(side="left", padx=4)
        styled_btn(dest, "Browse", self._browse_dest).pack(side="left", padx=4)
        styled_btn(dest, "Same as source", lambda: self.dest_var.set("Same folder as source")).pack(side="left")

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill="both", expand=True, padx=12, pady=4)
        sb = tk.Scrollbar(mid)
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(mid, yscrollcommand=sb.set,
                                  bg=BG2, fg=FG, selectmode="extended",
                                  selectbackground=SEL_BG, font=FONT, relief="flat")
        self.listbox.pack(fill="both", expand=True)
        sb.config(command=self.listbox.yview)

        bot = tk.Frame(self, bg=BG)
        bot.pack(fill="x", padx=12, pady=8)
        self.convert_btn = styled_btn(bot, "▶  Convert to MP4", self._convert,
                                      bg=ACCENT, activebackground="#9a7ad8")
        self.convert_btn.pack(side="left")
        self.progress = ttk.Progressbar(bot, length=300, mode="determinate")
        self.progress.pack(side="left", padx=10)
        self.status_var = tk.StringVar(value="")
        tk.Label(bot, textvariable=self.status_var, bg=BG, fg=FG2, font=FONT).pack(side="left")

    def _browse_dest(self):
        d = filedialog.askdirectory(title="Select destination folder")
        if d:
            self.dest_var.set(d)

    def _get_dest(self, src_path):
        d = self.dest_var.get()
        return Path(src_path).parent if d == "Same folder as source" else Path(d)

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select AVI or MOV files",
            filetypes=[("AVI/MOV files", "*.avi *.mov"), ("All files", "*.*")])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", Path(p).name)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder with AVI/MOV files")
        if not folder:
            return
        found = 0
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in (".avi", ".mov"):
                    full = os.path.join(root, f)
                    if full not in self.files:
                        self.files.append(full)
                        self.listbox.insert("end", Path(full).name)
                        found += 1
        self.status_var.set(f"Added {found} file(s)." if found else "No AVI/MOV files found.")

    def _clear(self):
        self.files.clear()
        self.listbox.delete(0, "end")
        self.status_var.set("")

    def _convert(self):
        if not FFMPEG:
            messagebox.showerror("ffmpeg not found", "Please install ffmpeg.")
            return
        if not self.files:
            self.status_var.set("No files in queue.")
            return
        files = list(self.files)
        self.progress["maximum"] = len(files)
        self.progress["value"]   = 0
        self.convert_btn.config(state="disabled", text="⏳  Converting...")
        errors = []

        def run():
            ok = err = skip = 0
            for i, f in enumerate(files):
                dest_dir = self._get_dest(f)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dst = dest_dir / Path(f).with_suffix(".mp4").name
                success, msg = convert_to_mp4(f, dst)
                if "Skipped" in msg:
                    skip += 1
                elif success:
                    ok += 1
                else:
                    err += 1
                    errors.append((f, msg))
                self.progress["value"] = i + 1
                self.status_var.set(f"{i+1}/{len(files)}  —  {Path(f).name}")
                self.update_idletasks()
            self.convert_btn.config(state="normal", text="▶  Convert to MP4")
            summary = f"Converted: {ok}  Skipped: {skip}  Errors: {err}"
            self.status_var.set(f"Done. {summary}")
            if errors:
                notify("Music Manager — Errors", f"{len(errors)} file(s) failed during video conversion")
                show_errors(self, errors)
            else:
                notify("Music Manager — Done", f"Video conversion complete. {summary}")

        threading.Thread(target=run, daemon=True).start()
class YouTubeTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self._jobs    = []
        self._running = False
        self._build()

    def _build(self):
        # URL input bar
        top = tk.Frame(self, bg=BG2, pady=8)
        top.pack(fill="x")
        styled_label(top, "  URL:").pack(side="left", padx=(12, 4))
        self.url_var = tk.StringVar()
        ue = styled_entry(top, textvariable=self.url_var)
        ue.pack(side="left", fill="x", expand=True, padx=4)
        ue.bind("<Return>", lambda e: self._add_url())
        styled_btn(top, "+ Add",    self._add_url).pack(side="left", padx=4)
        styled_btn(top, "Paste",    self._paste_url).pack(side="left", padx=(0, 12))

        # Options
        opts = tk.Frame(self, bg=BG, pady=6)
        opts.pack(fill="x", padx=12)

        styled_label(opts, "Format:").pack(side="left")
        self.fmt_var = tk.StringVar(value="mp3")
        for fmt in ("mp3", "mp4", "m4a"):
            tk.Radiobutton(opts, text=fmt.upper(), variable=self.fmt_var,
                           value=fmt, bg=BG, fg=FG, selectcolor=BG2,
                           activebackground=BG, activeforeground=FG,
                           font=FONT, cursor="hand2").pack(side="left", padx=6)

        tk.Frame(opts, bg=BG2, width=1, height=20).pack(side="left", padx=12)

        styled_label(opts, "Quality:").pack(side="left")
        self.quality_var = tk.StringVar(value="Best")
        self._quality_map = {
            "Best": "best", "1080p": "1080", "720p": "720",
            "480p": "480",  "320k audio": "320k", "192k audio": "192k"
        }
        om = styled_option(opts, self.quality_var, *self._quality_map.keys())
        om.pack(side="left", padx=4)

        tk.Frame(opts, bg=BG2, width=1, height=20).pack(side="left", padx=12)

        styled_label(opts, "Save to:").pack(side="left")
        self.outdir_var = tk.StringVar(value=str(Path.home() / "Downloads"))
        styled_entry(opts, textvariable=self.outdir_var, width=28).pack(side="left", padx=4)
        styled_btn(opts, "Browse", self._browse_outdir).pack(side="left")

        # Job list
        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)

        hdr = tk.Frame(list_frame, bg=BTN_BG)
        hdr.pack(fill="x")
        for text, w in [("URL / Title", 52), ("Format", 8), ("Status", 26)]:
            tk.Label(hdr, text=text, bg=BTN_BG, fg=FG2,
                     font=(FONT[0], FONT[1], "bold"),
                     width=w, anchor="w").pack(side="left", padx=4, pady=3)

        cf = tk.Frame(list_frame, bg=BG)
        cf.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(cf, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.job_frame = tk.Frame(self.canvas, bg=BG)
        cw = self.canvas.create_window((0, 0), window=self.job_frame, anchor="nw")
        self.job_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(cw, width=e.width))

        # Bottom bar
        bot = tk.Frame(self, bg=BG, pady=6)
        bot.pack(fill="x", padx=12)
        self.dl_btn = styled_btn(bot, "⬇  Download All",
                                 self._start_downloads,
                                 bg=ACCENT, activebackground="#9a7ad8")
        self.dl_btn.pack(side="left")
        styled_btn(bot, "🗑  Clear Done", self._clear_done).pack(side="left", padx=8)
        styled_btn(bot, "✖  Clear All",  self._clear_all).pack(side="left")
        self.total_status = tk.StringVar(value="")
        tk.Label(bot, textvariable=self.total_status,
                 bg=BG, fg=FG2, font=FONT).pack(side="right")

        if not YTDLP_OK:
            warn = tk.Frame(self, bg="#4a2020")
            warn.pack(fill="x")
            tk.Label(warn, text="⚠  yt-dlp not installed.  Run:  pip install yt-dlp",
                     bg="#4a2020", fg="#ffaaaa", font=FONT).pack(padx=10, pady=4)

    def _paste_url(self):
        try:
            txt = self.clipboard_get().strip()
            if txt:
                self.url_var.set(txt)
        except Exception:
            pass

    def _add_url(self):
        url = self.url_var.get().strip()
        if not url:
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showwarning("Invalid URL", "Please enter a valid http/https URL.")
            return
        fmt  = self.fmt_var.get()
        qual = self.quality_var.get()

        row = tk.Frame(self.job_frame, bg=BG2, pady=4)
        row.pack(fill="x", pady=1)

        short = url if len(url) <= 60 else url[:57] + "..."
        url_lbl = tk.Label(row, text=short, bg=BG2, fg=FG,
                           font=FONT, anchor="w", width=50)
        url_lbl.pack(side="left", padx=6)

        tk.Label(row, text=fmt.upper(), bg=BG2, fg=FG2,
                 font=FONT, width=6).pack(side="left")

        status_var = tk.StringVar(value="Queued")
        status_lbl = tk.Label(row, textvariable=status_var, bg=BG2,
                              fg="#aaaacc", font=FONT, width=24, anchor="w")
        status_lbl.pack(side="left", padx=4)

        prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(row, variable=prog_var,
                        maximum=100, length=110).pack(side="left", padx=4)

        job = {"url": url, "fmt": fmt,
               "quality": self._quality_map.get(qual, "best"),
               "status_var": status_var, "prog_var": prog_var,
               "url_lbl": url_lbl, "status_lbl": status_lbl,
               "row": row, "done": False, "error": False}

        def remove(r=row, j=job):
            if j in self._jobs:
                self._jobs.remove(j)
            r.destroy()
        styled_btn(row, "✖", remove, padx=4, pady=1).pack(side="right", padx=4)
        self._jobs.append(job)
        self.url_var.set("")

    def _browse_outdir(self):
        d = filedialog.askdirectory(title="Select download folder")
        if d:
            self.outdir_var.set(d)

    def _start_downloads(self):
        if not YTDLP_OK:
            messagebox.showerror("yt-dlp missing",
                "Install yt-dlp:\n  pip install yt-dlp\n\nThen restart the app.")
            return
        if not FFMPEG:
            messagebox.showerror("ffmpeg missing",
                "ffmpeg is required for conversion.")
            return
        pending = [j for j in self._jobs if not j["done"] and not j["error"]]
        if not pending:
            self.total_status.set("Nothing to download.")
            return

        self.dl_btn.config(state="disabled", text="⏳  Downloading...")
        self._running = True

        def run():
            done = err = 0
            for i, job in enumerate(pending):
                self.total_status.set(f"Downloading {i+1}/{len(pending)}...")
                if self._download_job(job):
                    done += 1
                else:
                    err += 1
            self.dl_btn.config(state="normal", text="⬇  Download All")
            self._running = False
            summary = f"Done: {done}  Errors: {err}"
            self.total_status.set(summary)
            if err:
                notify("Music Manager — Download errors",
                       f"{err} download(s) failed")
            else:
                notify("Music Manager — Downloads complete", summary)

        threading.Thread(target=run, daemon=True).start()

    def _download_job(self, job):
        import yt_dlp

        fmt     = job["fmt"]
        quality = job["quality"]
        outdir  = self.outdir_var.get()
        os.makedirs(outdir, exist_ok=True)

        job["status_lbl"].config(fg="#ddbb55")
        job["status_var"].set("Fetching info...")

        def progress_hook(d):
            if d["status"] == "downloading":
                raw = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    job["prog_var"].set(float(raw))
                except ValueError:
                    pass
                speed = d.get("_speed_str", "").strip()
                eta   = d.get("_eta_str", "").strip()
                job["status_var"].set(f"{raw}%  {speed}  ETA {eta}")
            elif d["status"] == "finished":
                job["prog_var"].set(100)
                job["status_var"].set("Converting...")

        ffmpeg_dir = str(Path(FFMPEG).parent)

        if fmt == "mp4":
            h = quality if quality not in ("best", "320k", "192k") else None
            fmt_str = (f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                       if h else "bestvideo+bestaudio/best")
            ydl_opts = {
                "format": fmt_str,
                "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
                "merge_output_format": "mp4",
                "ffmpeg_location": ffmpeg_dir,
                "progress_hooks": [progress_hook],
                "quiet": True, "no_warnings": True,
            }
        elif fmt == "m4a":
            ydl_opts = {
                "format": "bestaudio[ext=m4a]/bestaudio",
                "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
                "ffmpeg_location": ffmpeg_dir,
                "progress_hooks": [progress_hook],
                "quiet": True, "no_warnings": True,
            }
        else:  # mp3
            aq = quality if quality in ("320k", "192k") else "320k"
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": aq.replace("k", ""),
                }],
                "ffmpeg_location": ffmpeg_dir,
                "progress_hooks": [progress_hook],
                "quiet": True, "no_warnings": True,
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info  = ydl.extract_info(job["url"], download=False)
                title = info.get("title", job["url"])
                short = title if len(title) <= 60 else title[:57] + "..."
                job["url_lbl"].config(text=short)
                job["status_var"].set("Downloading...")
                ydl.download([job["url"]])
            job["done"] = True
            job["status_var"].set("✓  Done")
            job["status_lbl"].config(fg="#88dd88")
            job["prog_var"].set(100)
            return True
        except Exception as e:
            job["error"] = True
            job["status_var"].set(f"✗  {str(e)[:70]}")
            job["status_lbl"].config(fg="#ff7777")
            return False

    def _clear_done(self):
        for job in list(self._jobs):
            if job["done"] or job["error"]:
                job["row"].destroy()
                self._jobs.remove(job)

    def _clear_all(self):
        if self._running:
            return
        for job in list(self._jobs):
            job["row"].destroy()
        self._jobs.clear()
        self.total_status.set("")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🎵  Music Manager")
        # Widest tab toolbar (FLAC) needs ~960px; anything narrower clips it.
        self.geometry("1000x660")
        self.minsize(960, 500)
        self.configure(bg=BG)

        ffmpeg_status = "ffmpeg: ✓" if FFMPEG else "ffmpeg: ✗"
        mut_status  = "mutagen: ✓" if MUTAGEN_OK else "mutagen: ✗"
        pil_status  = "Pillow: ✓"  if PILLOW_OK  else "Pillow: ✗"
        ytdlp_status= "yt-dlp: ✓"  if YTDLP_OK   else "yt-dlp: ✗"
        header = tk.Frame(self, bg=ACCENT, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="🎵  Music Manager", bg=ACCENT, fg="#fff",
             font=(FONT[0], 14, "bold")).pack(side="left", padx=18)
        tk.Label(header,
             text=f"{ffmpeg_status}   {mut_status}   {pil_status}   {ytdlp_status}",
             bg=ACCENT, fg="#ddd", font=(FONT[0], 8)).pack(side="right", padx=18)

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Dark.TFrame", background=BG)

        self.tab_names = [
            "🏷  Metadata Editor", "🔄  FLAC → MP3 / ALAC",
            "🎬  Audio Extractor", "📹  Video → MP4", "▶  YouTube",
        ]
        self.tab_bar = tk.Frame(self, bg=BG2, height=38)
        self.tab_bar.pack(fill="x")
        self.tab_bar.pack_propagate(False)
        self.tab_buttons = []
        for index, name in enumerate(self.tab_names):
            button = tk.Button(
                self.tab_bar, text=name, command=lambda i=index: self._select_tab(i),
                bg=BG2, fg=BTN_FG2, activebackground=ACCENT,
                activeforeground=BTN_FG if IS_MAC else "#fff", relief="flat", bd=0,
                font=FONT, cursor="hand2", padx=10, pady=4
            )
            button.pack(side="left", fill="both", expand=True, padx=1, pady=2)
            self.tab_buttons.append(button)

        page_container = tk.Frame(self, bg=BG)
        page_container.pack(fill="both", expand=True)
        page_container.rowconfigure(0, weight=1)
        page_container.columnconfigure(0, weight=1)

        tabs = [
            (self.tab_names[0], MetadataTab), (self.tab_names[1], FlacTab),
            (self.tab_names[2], ExtractTab), (self.tab_names[3], VideoConvTab),
            (self.tab_names[4], YouTubeTab),
        ]
        self.page_container = page_container
        self.pages = [cls(page_container) for _, cls in tabs]
        for page in self.pages:
            page.grid(row=0, column=0, sticky="nsew")
        self.selected_tab = 0
        self._refresh_tabs()
        self._show_tab(0)
        self.bind_all("<<SelectAll>>", self._on_select_all, add="+")
        self.bind_all("<Control-a>", self._on_select_all, add="+")
        mod = "Command" if IS_MAC else "Control"
        self.bind_all(f"<{mod}-Cyrillic_ef>", self._on_select_all, add="+")

        # Deps warning
        missing = []
        if not MUTAGEN_OK: missing.append("mutagen")
        if not PILLOW_OK:  missing.append("Pillow")
        if not YTDLP_OK:   missing.append("yt-dlp")
        if not FFMPEG:     missing.append("ffmpeg")
        if missing:
            warn = tk.Frame(self, bg="#4a2020")
            warn.pack(fill="x")
            tk.Label(warn, text=f"⚠  Missing dependencies: {', '.join(missing)}.  "
                                 f"Install:  pip install {' '.join(p for p in missing if p != 'ffmpeg')}  "
                                 f"{'  +  install ffmpeg from https://ffmpeg.org' if 'ffmpeg' in missing else ''}",
                     bg="#4a2020", fg="#ffaaaa", font=(FONT[0], 9)).pack(padx=10, pady=4)

    def _refresh_tabs(self):
        for index, button in enumerate(self.tab_buttons):
            selected = index == self.selected_tab
            button.configure(
                bg=ACCENT if selected else BG2,
                # macOS ignores -bg here, so the active tab is marked by a
                # bold, fully black label rather than by its background.
                fg=(BTN_FG if selected else BTN_FG2) if IS_MAC
                   else ("#fff" if selected else FG2),
                font=FONT_B if selected else FONT,
            )

    def _select_tab(self, index):
        if index == self.selected_tab:
            return
        self.selected_tab = index
        self._show_tab(index)
        self._refresh_tabs()

    def _show_tab(self, index):
        self.pages[index].tkraise()

    def _on_select_all(self, _event=None):
        focus = self.focus_get()
        if focus is not None and focus.winfo_class() in ("Entry", "TEntry", "Text"):
            return None
        tree = getattr(self.pages[self.selected_tab], "tree", None)
        if tree is None:
            return None
        tree.selection_set(tree.get_children())
        return "break"

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()