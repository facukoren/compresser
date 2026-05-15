import os
import sys
import json
import time
import shutil
import zipfile
import platform
import datetime
import traceback
import threading
import subprocess
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD, DND_FILES


if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent.resolve()
else:
    APP_DIR = Path(__file__).parent.resolve()
FFMPEG_DIR = APP_DIR / "ffmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_EXE = FFMPEG_DIR / "ffprobe.exe"
LOG_DIR = APP_DIR / "logs"
CONFIG_PATH = APP_DIR / "config.json"
FFMPEG_URL = "https://github.com/GyanD/codexffmpeg/releases/download/6.1.1/ffmpeg-6.1.1-essentials_build.zip"


class Logger:
    LEVEL_COLORS = {
        "INFO":   "#cbd5e1",
        "OK":     "#10b981",
        "WARN":   "#f59e0b",
        "ERR":    "#ef4444",
        "DEBUG":  "#94a3b8",
        "FFMPEG": "#60a5fa",
        "CMD":    "#a78bfa",
        "TELEM":  "#06b6d4",
        "HW":     "#22d3ee",
    }

    def __init__(self):
        LOG_DIR.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = LOG_DIR / f"compresser_{ts}.log"
        self.fp = open(self.path, "w", encoding="utf-8", buffering=1)
        self.ui_cb = None
        self.lock = threading.Lock()
        self._write_header()

    def _write_header(self):
        h = [
            "=" * 70,
            f"Compresser session log",
            f"Started: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Platform: {platform.platform()}",
            f"Python: {platform.python_version()}",
            f"App dir: {APP_DIR}",
            "=" * 70,
            "",
        ]
        for line in h:
            self.fp.write(line + "\n")

    def attach_ui(self, cb):
        self.ui_cb = cb

    def log(self, level, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{ts}] [{level:>6}] {msg}"
        with self.lock:
            try:
                self.fp.write(line + "\n")
            except Exception:
                pass
        if self.ui_cb:
            try:
                self.ui_cb(level, line)
            except Exception:
                pass

    def info(self, m):   self.log("INFO", m)
    def ok(self, m):     self.log("OK", m)
    def warn(self, m):   self.log("WARN", m)
    def err(self, m):    self.log("ERR", m)
    def debug(self, m):  self.log("DEBUG", m)
    def ffmpeg(self, m): self.log("FFMPEG", m)
    def cmd(self, m):    self.log("CMD", m)
    def telem(self, m):  self.log("TELEM", m)
    def hw(self, m):     self.log("HW", m)

    def exception(self, where, exc_info=None):
        if exc_info is None:
            exc_info = sys.exc_info()
        if not exc_info or exc_info[0] is None:
            return
        self.log("ERR", f"Excepción en {where}: {exc_info[1]!r}")
        tb = "".join(traceback.format_exception(*exc_info)).rstrip().splitlines()
        for line in tb:
            self.log("ERR", "  " + line)

    def close(self):
        try:
            self.fp.write(f"\n[{datetime.datetime.now():%H:%M:%S}] Session closed.\n")
            self.fp.close()
        except Exception:
            pass


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class QueueItem:
    PENDING = "pending"
    PROBING = "probing"
    READY = "ready"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ICONS = {
        PENDING: "⏳", PROBING: "🔍", READY: "•",
        PROCESSING: "⚙", DONE: "✓",
        FAILED: "✗", CANCELLED: "⊘",
    }
    COLORS = {
        PENDING: "gray60", PROBING: "gray70", READY: "gray85",
        PROCESSING: "#3b82f6", DONE: "#10b981",
        FAILED: "#ef4444", CANCELLED: "#f59e0b",
    }

    def __init__(self, path):
        self.path = Path(path)
        self.info = None
        self.state = self.PENDING
        self.output_path = None
        self.error_msg = ""
        self.elapsed = 0.0
        self.size_after = 0
        # UI handles (filled by _render_queue)
        self.row = None
        self.icon_lbl = None
        self.title_lbl = None
        self.sub_lbl = None
        self.right_lbl = None
        self.del_btn = None


PRESETS = {
    "alta": {
        "label": "Alta calidad",
        "subtitle": "Referencia · indistinguible del original",
        "cq": 21,
        "crf": 19,
        "estimate": "~35-45% del original",
    },
    "balanceado": {
        "label": "Balanceado",
        "subtitle": "Recomendado para entrevistas/podcasts",
        "cq": 24,
        "crf": 22,
        "estimate": "~18-28% del original",
    },
    "ahorro": {
        "label": "Máximo ahorro",
        "subtitle": "Voz e imagen claras, archivo mínimo",
        "cq": 27,
        "crf": 25,
        "estimate": "~10-18% del original",
    },
}


def run_quiet(cmd, **kwargs):
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(
        cmd,
        creationflags=CREATE_NO_WINDOW,
        **kwargs,
    )


def popen_quiet(cmd, **kwargs):
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.Popen(
        cmd,
        creationflags=CREATE_NO_WINDOW,
        **kwargs,
    )


def find_ffmpeg(prefer="system"):
    """Returns (ffmpeg, ffprobe, source). source: 'system' | 'bundled' | 'embedded' | None.
    'embedded' = empaquetado dentro del .exe (PyInstaller _MEIPASS)."""
    sys_ff = shutil.which("ffmpeg")
    sys_fp = shutil.which("ffprobe")
    bundled_ok = FFMPEG_EXE.exists() and FFPROBE_EXE.exists()

    emb_ff = emb_fp = None
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        m = Path(sys._MEIPASS)
        e_ff = m / "ffmpeg" / "ffmpeg.exe"
        e_fp = m / "ffmpeg" / "ffprobe.exe"
        if e_ff.exists() and e_fp.exists():
            emb_ff, emb_fp = str(e_ff), str(e_fp)

    if prefer == "system" and sys_ff and sys_fp:
        return sys_ff, sys_fp, "system"
    if bundled_ok:
        return str(FFMPEG_EXE), str(FFPROBE_EXE), "bundled"
    if emb_ff:
        return emb_ff, emb_fp, "embedded"
    if sys_ff and sys_fp:
        return sys_ff, sys_fp, "system"
    return None, None, None


def download_ffmpeg(progress_cb):
    FFMPEG_DIR.mkdir(exist_ok=True)
    zip_path = FFMPEG_DIR / "ffmpeg.zip"

    def hook(blocks, block_size, total_size):
        if total_size > 0:
            pct = min(blocks * block_size / total_size, 1.0)
            progress_cb(pct, f"Descargando ffmpeg... {int(pct*100)}%")

    progress_cb(0.0, "Descargando ffmpeg...")
    urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook=hook)

    progress_cb(1.0, "Extrayendo ffmpeg...")
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            name = Path(member).name
            if name in ("ffmpeg.exe", "ffprobe.exe"):
                with z.open(member) as src, open(FFMPEG_DIR / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    zip_path.unlink(missing_ok=True)
    progress_cb(1.0, "ffmpeg listo")


def detect_nvenc(ffmpeg_path):
    try:
        out = run_quiet(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        return "h264_nvenc" in out
    except Exception:
        return False


def test_nvenc(ffmpeg_path):
    """Real init test: encodes a tiny synthetic frame to validate driver compat."""
    try:
        r = run_quiet(
            [ffmpeg_path, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1:r=10",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return True, ""
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        relevant = [l for l in msg if any(k in l.lower() for k in ("nvenc", "driver", "error"))]
        return False, " | ".join(relevant[:3]) if relevant else (msg[-1] if msg else "exit code != 0")
    except Exception as e:
        return False, str(e)


def probe_video(ffprobe_path, video_path):
    cmd = [
        ffprobe_path, "-v", "error",
        "-show_entries", "format=duration,size:stream=width,height,codec_name,codec_type,r_frame_rate",
        "-of", "json", video_path,
    ]
    out = run_quiet(cmd, capture_output=True, text=True).stdout
    data = json.loads(out)
    duration = float(data["format"].get("duration", 0))
    size = int(data["format"].get("size", 0))
    width = height = 0
    codec = ""
    fps = 0.0
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            width = s.get("width", 0)
            height = s.get("height", 0)
            codec = s.get("codec_name", "")
            r = s.get("r_frame_rate", "0/1")
            try:
                a, b = r.split("/")
                fps = float(a) / float(b) if float(b) else 0
            except Exception:
                fps = 0
            break
    return {
        "duration": duration, "size": size,
        "width": width, "height": height,
        "codec": codec, "fps": fps,
    }


def fmt_size(b):
    if b <= 0:
        return "—"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"


def fmt_time(s):
    if s is None or s < 0:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Compresser · Video sin pérdida visible")
        self.geometry("900x980")
        self.minsize(820, 880)

        self.logger = Logger()
        self.logger.info(f"App start · log file: {self.logger.path.name}")

        self.video_path = None
        self.video_info = None
        self.ffmpeg = None
        self.ffprobe = None
        self.has_nvenc = False
        self.selected_preset = "balanceado"
        self.process = None
        self.encoding = False

        # Cola
        self.queue = []
        self.current_item = None
        self.queue_start_time = None

        self.config = load_config()
        saved_dir = self.config.get("output_dir")
        self.output_dir = Path(saved_dir) if saved_dir and Path(saved_dir).exists() else None

        self._build_ui()
        self.logger.attach_ui(self._ui_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._install_crash_handlers()
        self.after(100, self._initialize)

    def _install_crash_handlers(self):
        prev_excepthook = sys.excepthook
        def _sys_hook(exc_type, exc_value, tb):
            self.logger.exception("hilo principal", (exc_type, exc_value, tb))
            try:
                prev_excepthook(exc_type, exc_value, tb)
            except Exception:
                pass
        sys.excepthook = _sys_hook

        def _thread_hook(args):
            self.logger.exception(
                f"hilo {args.thread.name}",
                (args.exc_type, args.exc_value, args.exc_traceback),
            )
        try:
            threading.excepthook = _thread_hook
        except Exception:
            pass

        def _tk_hook(exc, val, tb):
            self.logger.exception("callback Tk", (exc, val, tb))
        self.report_callback_exception = _tk_hook

    def _on_close(self):
        self.logger.info("App closing")
        self.logger.close()
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.destroy()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.log_visible = True

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(20, 4), sticky="ew")
        ctk.CTkLabel(
            header, text="Compresser",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Entrevistas y podcasts · H.264 universal · NVENC",
            font=ctk.CTkFont(size=12), text_color="gray60",
        ).pack(anchor="w")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=24, pady=12, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(7, weight=1)
        self.body = body

        # Drop zone
        self.drop_frame = ctk.CTkFrame(
            body, fg_color="#1f2937", corner_radius=14,
            border_width=2, border_color="#374151", height=180,
        )
        self.drop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.drop_frame.grid_propagate(False)
        self.drop_frame.grid_columnconfigure(0, weight=1)
        self.drop_frame.grid_rowconfigure(0, weight=1)

        self.drop_inner = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        self.drop_inner.grid(row=0, column=0)

        self.drop_icon = ctk.CTkLabel(
            self.drop_inner, text="📁", font=ctk.CTkFont(size=42),
        )
        self.drop_icon.pack()
        self.drop_label = ctk.CTkLabel(
            self.drop_inner,
            text="Arrastrá un video acá o hacé click para elegir",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.drop_label.pack(pady=(4, 2))
        self.drop_sub = ctk.CTkLabel(
            self.drop_inner, text="MP4 · MKV · MOV · AVI · WEBM",
            font=ctk.CTkFont(size=11), text_color="gray60",
        )
        self.drop_sub.pack()

        for w in (self.drop_frame, self.drop_inner, self.drop_icon, self.drop_label, self.drop_sub):
            w.bind("<Button-1>", lambda e: self._browse())

        self.drop_frame.drop_target_register(DND_FILES)
        self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)

        # Queue panel (hidden until a video is queued)
        self.queue_frame = ctk.CTkFrame(body, fg_color="#1f2937", corner_radius=14)
        self.queue_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.queue_frame.grid_remove()

        queue_header = ctk.CTkFrame(self.queue_frame, fg_color="transparent")
        queue_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        queue_header.grid_columnconfigure(0, weight=1)
        self.queue_title = ctk.CTkLabel(
            queue_header, text="Cola",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        )
        self.queue_title.grid(row=0, column=0, sticky="w")
        self.queue_clear_done_btn = ctk.CTkButton(
            queue_header, text="Limpiar terminados", width=140, height=26,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._clear_done_items,
        )
        self.queue_clear_done_btn.grid(row=0, column=1, padx=(6, 4))
        self.queue_clear_all_btn = ctk.CTkButton(
            queue_header, text="✕ Vaciar cola", width=110, height=26,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._clear_queue,
        )
        self.queue_clear_all_btn.grid(row=0, column=2)

        self.queue_list = ctk.CTkScrollableFrame(
            self.queue_frame, fg_color="transparent", height=200,
        )
        self.queue_list.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))
        self.queue_list.grid_columnconfigure(0, weight=1)

        self.queue_add_more = ctk.CTkButton(
            self.queue_frame, text="+ Agregar más videos", height=32,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=12),
            command=self._browse,
        )
        self.queue_add_more.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))

        # Preset cards
        presets_label = ctk.CTkLabel(
            body, text="Calidad", font=ctk.CTkFont(size=14, weight="bold"),
        )
        presets_label.grid(row=2, column=0, sticky="w", pady=(4, 6))

        self.presets_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.presets_frame.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        self.presets_frame.grid_columnconfigure((0, 1, 2), weight=1)
        self.preset_cards = {}
        for i, key in enumerate(("alta", "balanceado", "ahorro")):
            card = self._make_preset_card(self.presets_frame, key, PRESETS[key])
            card.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="ew")
            self.preset_cards[key] = card
        self._refresh_preset_cards()

        # Destination row
        dest_frame = ctk.CTkFrame(body, fg_color="#1f2937", corner_radius=10)
        dest_frame.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        dest_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            dest_frame, text="📁", font=ctk.CTkFont(size=20),
        ).grid(row=0, column=0, padx=(16, 10), pady=14)

        dest_text = ctk.CTkFrame(dest_frame, fg_color="transparent")
        dest_text.grid(row=0, column=1, sticky="ew", pady=12)
        ctk.CTkLabel(
            dest_text, text="GUARDAR EN",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="gray60", anchor="w",
        ).pack(anchor="w", pady=(0, 2))
        self.dest_label = ctk.CTkLabel(
            dest_text, text="", font=ctk.CTkFont(size=12),
            anchor="w", text_color="gray85", justify="left",
        )
        self.dest_label.pack(anchor="w", fill="x")
        self._refresh_dest_label()

        ctk.CTkButton(
            dest_frame, text="Cambiar", width=90, height=32,
            fg_color="#374151", hover_color="#4b5563",
            command=self._choose_output_dir,
        ).grid(row=0, column=2, padx=14, pady=14)

        # Action area
        action = ctk.CTkFrame(body, fg_color="transparent")
        action.grid(row=5, column=0, sticky="ew")
        action.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            action, text="Cargando...", font=ctk.CTkFont(size=12),
            text_color="gray70", anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.progress = ctk.CTkProgressBar(action, height=10)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self.progress_meta = ctk.CTkLabel(
            action, text="", font=ctk.CTkFont(size=11),
            text_color="gray60", anchor="w",
        )
        self.progress_meta.grid(row=2, column=0, sticky="ew", pady=(0, 4))

        self.telem_label = ctk.CTkLabel(
            action, text="", font=ctk.CTkFont(family="Consolas", size=11),
            text_color="#06b6d4", anchor="w",
        )
        self.telem_label.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        btn_row = ctk.CTkFrame(action, fg_color="transparent")
        btn_row.grid(row=4, column=0, sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)

        self.action_btn = ctk.CTkButton(
            btn_row, text="Comprimir", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_action, state="disabled",
        )
        self.action_btn.grid(row=0, column=0, sticky="ew")

        # Logs panel
        log_header = ctk.CTkFrame(body, fg_color="transparent")
        log_header.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        log_header.grid_columnconfigure(0, weight=1)

        log_title = ctk.CTkFrame(log_header, fg_color="transparent")
        log_title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            log_title, text="Logs",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        self.log_count = ctk.CTkLabel(
            log_title, text="· 0 líneas",
            font=ctk.CTkFont(size=11), text_color="gray60",
        )
        self.log_count.pack(side="left", padx=(8, 0))

        log_btns = ctk.CTkFrame(log_header, fg_color="transparent")
        log_btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(
            log_btns, text="Abrir carpeta", width=110, height=28,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._open_logs_folder,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            log_btns, text="Copiar", width=80, height=28,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._copy_logs,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            log_btns, text="Limpiar vista", width=110, height=28,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._clear_logs_view,
        ).pack(side="left", padx=(0, 6))
        self.toggle_btn = ctk.CTkButton(
            log_btns, text="Ocultar", width=80, height=28,
            fg_color="#374151", hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._toggle_logs,
        )
        self.toggle_btn.pack(side="left")

        self.log_box = ctk.CTkTextbox(
            body, height=200, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#0f172a", text_color="#cbd5e1",
            corner_radius=10, border_width=1, border_color="#1f2937",
            wrap="none",
        )
        self.log_box.grid(row=7, column=0, sticky="nsew", pady=(8, 0))
        self.log_box.configure(state="disabled")
        self._log_lines = 0
        self._init_log_tags()

        # Footer engine info
        self.engine_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=10),
            text_color="gray50",
        )
        self.engine_label.grid(row=2, column=0, pady=(8, 14))

    def _render_queue(self):
        # Show/hide containers
        if self.queue:
            self.queue_frame.grid()
            self.drop_frame.grid_remove()
        else:
            self.queue_frame.grid_remove()
            self.drop_frame.grid()

        # Header counts
        total = len(self.queue)
        done = sum(1 for it in self.queue if it.state == QueueItem.DONE)
        failed = sum(1 for it in self.queue if it.state == QueueItem.FAILED)
        title = f"Cola · {total} video" + ("s" if total != 1 else "")
        if done or failed:
            extras = []
            if done: extras.append(f"✓ {done}")
            if failed: extras.append(f"✗ {failed}")
            title += "  ·  " + "  ".join(extras)
        self.queue_title.configure(text=title)

        # Rebuild rows
        for w in self.queue_list.winfo_children():
            w.destroy()
        for idx, item in enumerate(self.queue):
            self._render_queue_row(idx, item)

    def _render_queue_row(self, idx, item):
        row = ctk.CTkFrame(self.queue_list, fg_color="#0f172a", corner_radius=8)
        row.grid(row=idx, column=0, sticky="ew", padx=4, pady=3)
        row.grid_columnconfigure(2, weight=1)

        icon = ctk.CTkLabel(
            row, text=QueueItem.ICONS.get(item.state, "•"),
            font=ctk.CTkFont(size=16),
            text_color=QueueItem.COLORS.get(item.state, "gray60"),
            width=28,
        )
        icon.grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8)
        item.icon_lbl = icon

        # Filename
        name = item.path.name
        if len(name) > 60:
            name = name[:30] + "…" + name[-27:]
        title = ctk.CTkLabel(
            row, text=name, font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        title.grid(row=0, column=1, columnspan=2, sticky="w", padx=(0, 10), pady=(8, 0))
        item.title_lbl = title

        # Subtitle (info + status text)
        subtitle = self._format_item_subtitle(item)
        sub = ctk.CTkLabel(
            row, text=subtitle, font=ctk.CTkFont(size=11),
            text_color="gray60", anchor="w",
        )
        sub.grid(row=1, column=1, columnspan=2, sticky="w", padx=(0, 10), pady=(0, 8))
        item.sub_lbl = sub

        # Remove button (disabled if processing)
        can_remove = item.state in (QueueItem.PENDING, QueueItem.READY,
                                    QueueItem.PROBING, QueueItem.DONE,
                                    QueueItem.FAILED, QueueItem.CANCELLED)
        del_btn = ctk.CTkButton(
            row, text="✕", width=28, height=28,
            fg_color="transparent", hover_color="#374151",
            text_color="gray60", font=ctk.CTkFont(size=14),
            command=lambda i=item: self._remove_item(i),
            state="normal" if can_remove else "disabled",
        )
        del_btn.grid(row=0, column=3, rowspan=2, padx=(0, 8), pady=8)
        item.del_btn = del_btn
        item.row = row

    def _format_item_subtitle(self, item):
        info = item.info
        parts = []
        if item.state == QueueItem.PROBING:
            parts.append("Analizando...")
        elif item.state == QueueItem.PENDING:
            parts.append("En espera")
        elif item.state == QueueItem.READY and info:
            parts.append(fmt_size(info["size"]))
            parts.append(fmt_time(info["duration"]))
            if info["width"]:
                parts.append(f"{info['width']}×{info['height']}")
        elif item.state == QueueItem.PROCESSING:
            parts.append("Comprimiendo...")
            if info:
                parts.append(fmt_size(info["size"]))
        elif item.state == QueueItem.DONE:
            if info and item.size_after:
                saved = (1 - item.size_after / info["size"]) * 100 if info["size"] else 0
                parts.append(f"{fmt_size(info['size'])} → {fmt_size(item.size_after)} ({saved:.0f}% ahorro)")
            parts.append(f"en {fmt_time(item.elapsed)}")
        elif item.state == QueueItem.FAILED:
            parts.append(item.error_msg or "Falló")
        elif item.state == QueueItem.CANCELLED:
            parts.append("Cancelado")
        return "  ·  ".join(parts)

    def _remove_item(self, item):
        if item.state == QueueItem.PROCESSING:
            return
        try:
            self.queue.remove(item)
            self.logger.info(f"Removido de cola: {item.path.name}")
        except ValueError:
            pass
        self._render_queue()
        self._update_action_button()
        if not self.queue:
            self._refresh_dest_label()

    def _clear_done_items(self):
        if self.encoding:
            return
        before = len(self.queue)
        self.queue = [it for it in self.queue if it.state not in (
            QueueItem.DONE, QueueItem.FAILED, QueueItem.CANCELLED
        )]
        removed = before - len(self.queue)
        if removed:
            self.logger.info(f"Limpiados {removed} items terminados de la cola")
        self._render_queue()
        self._update_action_button()

    def _clear_queue(self):
        if self.encoding:
            return
        n = len(self.queue)
        self.queue = []
        self.video_path = None
        self.video_info = None
        if n:
            self.logger.info(f"Cola vaciada ({n} items)")
        self._render_queue()
        self._update_action_button()
        self._refresh_dest_label()
        self._set_status("Arrastrá videos o hacé click para agregar.")

    def _update_action_button(self):
        if self.encoding:
            self.action_btn.configure(
                state="normal", text="Cancelar",
                fg_color="#dc2626", hover_color="#b91c1c",
            )
            return
        ready = [it for it in self.queue if it.state == QueueItem.READY]
        if not ready:
            self.action_btn.configure(state="disabled", text="Comprimir")
            self._reset_action_btn_colors()
            return
        if len(ready) == 1:
            txt = "Comprimir"
        else:
            txt = f"Comprimir {len(ready)} videos"
        self.action_btn.configure(state="normal", text=txt)
        self._reset_action_btn_colors()

    def _reset_action_btn_colors(self):
        self.action_btn.configure(
            fg_color=("#3b82f6", "#1d4ed8"),
            hover_color=("#2563eb", "#1e40af"),
        )

    def _make_preset_card(self, parent, key, preset):
        card = ctk.CTkFrame(parent, fg_color="#1f2937", corner_radius=12,
                            border_width=2, border_color="#1f2937", height=110)
        card.grid_propagate(False)
        card.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(card, text=preset["label"],
                             font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
        title.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))

        sub = ctk.CTkLabel(card, text=preset["subtitle"],
                           font=ctk.CTkFont(size=11), text_color="gray60", anchor="w",
                           wraplength=200, justify="left")
        sub.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))

        est = ctk.CTkLabel(card, text=preset["estimate"],
                           font=ctk.CTkFont(size=10), text_color="#60a5fa", anchor="w")
        est.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 12))

        for w in (card, title, sub, est):
            w.bind("<Button-1>", lambda e, k=key: self._select_preset(k))
        return card

    def _init_log_tags(self):
        try:
            tk_text = self.log_box._textbox
            for level, color in Logger.LEVEL_COLORS.items():
                tk_text.tag_configure(f"lvl_{level}", foreground=color)
        except Exception:
            pass

    def _ui_log(self, level, line):
        self.after(0, self._append_log_line, level, line)

    def _append_log_line(self, level, line):
        try:
            self.log_box.configure(state="normal")
            tag = f"lvl_{level}"
            try:
                self.log_box._textbox.insert("end", line + "\n", tag)
            except Exception:
                self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            self._log_lines += 1
            if self._log_lines % 10 == 0 or self._log_lines < 20:
                self.log_count.configure(text=f"· {self._log_lines} líneas")
        except Exception:
            pass

    def _open_logs_folder(self):
        try:
            os.startfile(LOG_DIR)
        except Exception as e:
            self.logger.err(f"No pude abrir carpeta de logs: {e}")

    def _copy_logs(self):
        try:
            content = self.log_box.get("1.0", "end")
            self.clipboard_clear()
            self.clipboard_append(content)
            self.logger.info("Logs copiados al portapapeles")
        except Exception as e:
            self.logger.err(f"No pude copiar logs: {e}")

    def _clear_logs_view(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self._log_lines = 0
        self.log_count.configure(text="· 0 líneas (vista limpia, archivo intacto)")

    def _toggle_logs(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.grid()
            self.toggle_btn.configure(text="Ocultar")
        else:
            self.log_box.grid_remove()
            self.toggle_btn.configure(text="Mostrar")

    def _refresh_dest_label(self):
        if self.output_dir:
            txt = str(self.output_dir)
        elif self.queue:
            # Use first item's parent as hint
            txt = f"Junto al original  ·  {self.queue[0].path.parent}"
        else:
            txt = "Junto al video original (carpeta del archivo)"
        if len(txt) > 80:
            txt = txt[:38] + "…" + txt[-40:]
        self.dest_label.configure(text=txt)

    def _choose_output_dir(self):
        if self.encoding:
            return
        initial = str(self.output_dir) if self.output_dir else (
            str(self.queue[0].path.parent) if self.queue else str(Path.home())
        )
        d = filedialog.askdirectory(title="Carpeta de destino", initialdir=initial)
        if d:
            self.output_dir = Path(d)
            self.config["output_dir"] = str(self.output_dir)
            save_config(self.config)
            self._refresh_dest_label()
            self.logger.info(f"Carpeta de salida: {self.output_dir}")

    def _select_preset(self, key):
        if self.selected_preset != key:
            self.logger.info(f"Preset: {key} (cq={PRESETS[key]['cq']}, crf={PRESETS[key]['crf']})")
        self.selected_preset = key
        self._refresh_preset_cards()

    def _refresh_preset_cards(self):
        for key, card in self.preset_cards.items():
            if key == self.selected_preset:
                card.configure(border_color="#3b82f6", fg_color="#1e3a5f")
            else:
                card.configure(border_color="#1f2937", fg_color="#1f2937")

    def _set_status(self, text, color="gray70"):
        self.status_label.configure(text=text, text_color=color)

    def _initialize(self):
        threading.Thread(target=self._setup_ffmpeg, daemon=True).start()

    def _log_hardware(self):
        try:
            import psutil
            cpu_phys = psutil.cpu_count(logical=False) or 0
            cpu_log = psutil.cpu_count(logical=True) or 0
            ram = psutil.virtual_memory().total / (1024**3)
            cpu_name = platform.processor() or "desconocido"
            self.logger.hw(f"CPU: {cpu_name} · {cpu_phys}c / {cpu_log}t")
            self.logger.hw(f"RAM total: {ram:.1f} GB")
        except Exception as e:
            self.logger.debug(f"psutil no disponible: {e}")
        try:
            import pynvml
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            for i in range(count):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(h)
                if isinstance(name, bytes):
                    name = name.decode()
                meminfo = pynvml.nvmlDeviceGetMemoryInfo(h)
                self.logger.hw(f"GPU{i}: {name} · {meminfo.total/(1024**3):.1f} GB VRAM")
            try:
                drv = pynvml.nvmlSystemGetDriverVersion()
                if isinstance(drv, bytes):
                    drv = drv.decode()
                self.logger.hw(f"Driver NVIDIA: {drv}")
            except Exception:
                pass
            pynvml.nvmlShutdown()
        except Exception as e:
            self.logger.debug(f"NVML no disponible: {e}")

    def _start_telemetry(self):
        self._telem_running = True
        self._telem_thread = threading.Thread(target=self._telemetry_loop, daemon=True)
        self._telem_thread.start()

    def _stop_telemetry(self):
        self._telem_running = False
        self.after(0, lambda: self.telem_label.configure(text=""))

    def _telemetry_loop(self):
        try:
            import psutil
        except ImportError:
            self.logger.warn("psutil no instalado · sin telemetría CPU/RAM (corré setup.bat de nuevo)")
            return
        nvml = None
        nvml_handle = None
        try:
            import pynvml
            pynvml.nvmlInit()
            nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            nvml = pynvml
        except ImportError:
            self.logger.warn("nvidia-ml-py no instalado · sin telemetría GPU (corré setup.bat de nuevo)")
        except Exception as e:
            self.logger.warn(f"NVML no inicializó · sin telemetría GPU: {e}")

        psutil.cpu_percent(interval=None)
        last_log = 0.0
        try:
            while self._telem_running:
                try:
                    cpu = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory()
                    parts = [
                        f"CPU {cpu:5.1f}%",
                        f"RAM {mem.used/(1024**3):4.1f}/{mem.total/(1024**3):.0f}GB",
                    ]
                    if nvml and nvml_handle:
                        try:
                            util = nvml.nvmlDeviceGetUtilizationRates(nvml_handle)
                            vram = nvml.nvmlDeviceGetMemoryInfo(nvml_handle)
                            temp = nvml.nvmlDeviceGetTemperature(nvml_handle, nvml.NVML_TEMPERATURE_GPU)
                            try:
                                enc_util, _ = nvml.nvmlDeviceGetEncoderUtilization(nvml_handle)
                            except Exception:
                                enc_util = None
                            try:
                                power = nvml.nvmlDeviceGetPowerUsage(nvml_handle) / 1000.0
                            except Exception:
                                power = None
                            gpu_str = f"GPU {util.gpu:3d}%"
                            if enc_util is not None:
                                gpu_str += f" NVENC {enc_util:3d}%"
                            parts.append(gpu_str)
                            parts.append(f"VRAM {vram.used/(1024**3):4.2f}/{vram.total/(1024**3):.1f}GB")
                            parts.append(f"{temp:3d}°C")
                            if power is not None:
                                parts.append(f"{power:.0f}W")
                        except Exception as e:
                            self.logger.debug(f"NVML sample fail: {e}")
                    line = "  ·  ".join(parts)
                    self.after(0, lambda l=line: self.telem_label.configure(text=l))
                    now = time.time()
                    if now - last_log >= 5:
                        self.logger.telem(line)
                        last_log = now
                except Exception:
                    self.logger.exception("muestreo de telemetría")
                time.sleep(2)
        finally:
            if nvml:
                try:
                    nvml.nvmlShutdown()
                except Exception:
                    pass

    def _setup_ffmpeg(self):
        self._log_hardware()
        self.logger.info("Buscando ffmpeg/ffprobe...")
        ff, fp, source = find_ffmpeg(prefer="system")
        if not ff or not fp:
            self.logger.warn("ffmpeg no encontrado. Iniciando descarga.")
            try:
                self.after(0, lambda: self._set_status("Primera ejecución: descargando ffmpeg..."))
                self.logger.info(f"URL: {FFMPEG_URL}")
                download_ffmpeg(lambda pct, msg: self.after(0, self._update_setup_progress, pct, msg))
                ff, fp, source = find_ffmpeg(prefer="bundled")
                self.logger.ok("ffmpeg descargado y extraído.")
            except Exception as e:
                self.logger.exception("descarga de ffmpeg")
                self.after(0, lambda: self._set_status(f"Error descargando ffmpeg: {e}", "#ef4444"))
                return
        self.ffmpeg, self.ffprobe = ff, fp
        self.logger.ok(f"ffmpeg ({source}): {ff}")
        self.logger.ok(f"ffprobe ({source}): {fp}")
        try:
            ver = run_quiet([ff, "-version"], capture_output=True, text=True, timeout=5).stdout.splitlines()[0]
            self.logger.info(f"Versión: {ver}")
        except Exception:
            pass
        self.logger.info("Detectando soporte NVENC...")
        self.has_nvenc = False
        if detect_nvenc(self.ffmpeg):
            self.logger.info("h264_nvenc listado · validando con frame real...")
            ok, err = test_nvenc(self.ffmpeg)
            if ok:
                self.has_nvenc = True
                self.logger.ok("Test NVENC pasó · usando GPU")
            else:
                self.logger.warn(f"Test NVENC falló con {source}: {err}")
                if source == "system":
                    if not FFMPEG_EXE.exists():
                        self.logger.info("Probando con ffmpeg bundled (compat con drivers viejos)...")
                        self.after(0, lambda: self._set_status(
                            "Tu ffmpeg de PATH no soporta tu driver. Descargando versión compatible..."
                        ))
                        try:
                            download_ffmpeg(lambda pct, msg: self.after(0, self._update_setup_progress, pct, msg))
                        except Exception:
                            self.logger.exception("descarga de ffmpeg bundled")
                    if FFMPEG_EXE.exists():
                        self.ffmpeg = str(FFMPEG_EXE)
                        self.ffprobe = str(FFPROBE_EXE)
                        self.logger.info(f"Reintentando con bundled: {self.ffmpeg}")
                        ok2, err2 = test_nvenc(self.ffmpeg)
                        if ok2:
                            self.has_nvenc = True
                            self.logger.ok("Test NVENC con bundled pasó · usando GPU")
                        else:
                            self.logger.err(f"NVENC también falla con bundled: {err2}")
                            self.logger.err("Actualizá tu driver NVIDIA o se usará CPU.")
                else:
                    self.logger.err("Bundled no soporta tu driver. Actualizá driver NVIDIA.")
        else:
            self.logger.info("h264_nvenc no listado en este ffmpeg.")
        if not self.has_nvenc:
            self.logger.warn("Usando libx264 (CPU). Más lento pero garantizado.")
        engine = "NVENC (GPU NVIDIA)" if self.has_nvenc else "libx264 (CPU)"
        self.after(0, lambda: self.engine_label.configure(text=f"Motor: H.264 · {engine}"))
        self.after(0, lambda: self._set_status("Listo. Arrastrá un video para empezar."))
        self.after(0, lambda: self.progress.set(0))
        self.logger.info("Setup completo. App lista.")

    def _update_setup_progress(self, pct, msg):
        self.progress.set(pct)
        self._set_status(msg)

    def _parse_drop_paths(self, raw):
        """tkinterdnd2 entrega rutas tipo: {C:/path with spaces/a.mp4} {C:/b.mp4}.
        Devuelve lista de strings."""
        raw = raw.strip()
        paths = []
        if "{" in raw:
            # Bracketed format for paths with spaces
            i = 0
            while i < len(raw):
                if raw[i] == "{":
                    end = raw.find("}", i)
                    if end == -1:
                        break
                    paths.append(raw[i+1:end])
                    i = end + 1
                elif raw[i] == " ":
                    i += 1
                else:
                    # Unquoted path until next space
                    end = raw.find(" ", i)
                    if end == -1:
                        paths.append(raw[i:])
                        break
                    paths.append(raw[i:end])
                    i = end + 1
        else:
            paths = raw.split()
        return [p for p in paths if p]

    def _on_drop(self, event):
        paths = self._parse_drop_paths(event.data)
        if not paths:
            return
        self.logger.info(f"Drop: {len(paths)} archivo(s)")
        for p in paths:
            self._add_to_queue(p)

    def _browse(self):
        if self.encoding and len(self.queue) > 0 and self.queue[0].state == QueueItem.PROCESSING:
            # Allow adding more while a queue is running — but no conflicting "browse during encode"
            pass
        files = filedialog.askopenfilenames(
            title="Seleccioná uno o más videos",
            filetypes=[("Videos", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts *.mpg *.mpeg *.m2ts"),
                       ("Todos", "*.*")],
        )
        for f in files:
            self._add_to_queue(f)

    def _add_to_queue(self, path):
        if not self.ffprobe:
            self.logger.warn(f"Intento de cargar antes de tener ffprobe: {path}")
            self._set_status("Esperá, ffmpeg todavía no está listo.", "#f59e0b")
            return
        p = Path(path)
        if not p.exists():
            self.logger.err(f"Archivo no existe: {path}")
            return
        if not p.is_file():
            return
        # No duplicates
        for it in self.queue:
            try:
                if it.path.resolve() == p.resolve():
                    self.logger.debug(f"Ya en cola: {p.name}")
                    return
            except Exception:
                pass
        item = QueueItem(p)
        item.state = QueueItem.PROBING
        self.queue.append(item)
        self.logger.info(f"En cola: {p.name}")
        self._render_queue()
        threading.Thread(target=self._do_probe_item, args=(item,), daemon=True).start()
        self._update_action_button()

    def _do_probe_item(self, item):
        try:
            self.logger.debug(f"ffprobe sobre {item.path.name}")
            item.info = probe_video(self.ffprobe, str(item.path))
            item.state = QueueItem.READY
            self.logger.ok(
                f"Probe {item.path.name} · {fmt_size(item.info['size'])} · "
                f"{fmt_time(item.info['duration'])} · {item.info['width']}x{item.info['height']}"
            )
        except Exception as e:
            item.state = QueueItem.FAILED
            item.error_msg = f"Error al analizar: {e}"
            self.logger.exception(f"ffprobe {item.path.name}")
        self.after(0, self._render_queue)
        self.after(0, self._update_action_button)

    # Backward-compat shim used by CLI auto-load
    def _load_video(self, path):
        self._add_to_queue(path)

    def _do_probe(self):
        try:
            self.logger.debug(f"ffprobe sobre {self.video_path}")
            info = probe_video(self.ffprobe, self.video_path)
            self.video_info = info
            self.logger.ok(
                f"Probe OK · {fmt_size(info['size'])} · {fmt_time(info['duration'])} · "
                f"{info['width']}x{info['height']} · {info['codec']} · {info['fps']:.2f}fps"
            )
            bitrate = (info['size'] * 8 / info['duration'] / 1000) if info['duration'] else 0
            self.logger.info(f"Bitrate medio estimado: {bitrate:.0f} kbps")
            self.after(0, self._on_probe_done)
        except Exception as e:
            self.logger.exception("ffprobe")
            self.after(0, lambda: self._set_status(f"Error analizando: {e}", "#ef4444"))

    def _on_probe_done(self):
        # Legacy hook — replaced by _do_probe_item per-item flow.
        pass

    def _on_action(self):
        if self.encoding:
            self._cancel_queue()
        else:
            self._start_queue()

    def _start_queue(self):
        if not self.ffmpeg:
            return
        ready = [it for it in self.queue if it.state == QueueItem.READY]
        if not ready:
            return
        self.queue_start_time = datetime.datetime.now()
        self.logger.info("#" * 60)
        self.logger.info(f"Cola iniciada · {len(ready)} video(s) a procesar")
        self._start_next_in_queue()

    def _start_next_in_queue(self):
        nxt = next((it for it in self.queue if it.state == QueueItem.READY), None)
        if nxt is None:
            self._on_queue_done()
            return
        self._start_encoding_item(nxt)

    def _on_queue_done(self):
        done = sum(1 for it in self.queue if it.state == QueueItem.DONE)
        failed = sum(1 for it in self.queue if it.state == QueueItem.FAILED)
        cancelled = sum(1 for it in self.queue if it.state == QueueItem.CANCELLED)
        elapsed = (datetime.datetime.now() - self.queue_start_time).total_seconds() if self.queue_start_time else 0
        self.encoding = False
        self.current_item = None
        self._stop_telemetry()
        self._update_action_button()
        self.progress.set(1.0 if done else 0)
        self.progress_meta.configure(text="")

        if failed or cancelled:
            self._set_status(
                f"Cola: ✓ {done}  ✗ {failed}  ⊘ {cancelled}  (en {fmt_time(elapsed)})",
                "#f59e0b" if cancelled else ("#ef4444" if failed else "#10b981"),
            )
        else:
            self._set_status(
                f"✓ Cola completa · {done} video(s) en {fmt_time(elapsed)}",
                "#10b981",
            )
        self.logger.ok(f"Cola completa · OK:{done} FAIL:{failed} CANCEL:{cancelled} · {fmt_time(elapsed)}")
        self.logger.info("#" * 60)
        self._notify_queue_done(done, failed, cancelled, elapsed)

    def _start_encoding_item(self, item):
        if not item.info or not self.ffmpeg:
            return
        src = item.path
        out_dir = self.output_dir if self.output_dir else src.parent
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.exception(f"crear carpeta destino {out_dir}")
            item.state = QueueItem.FAILED
            item.error_msg = f"Carpeta destino inválida: {e}"
            self._render_queue()
            self.after(50, self._start_next_in_queue)
            return
        dst = out_dir / f"{src.stem}_compressed.mp4"
        if dst.resolve() == src.resolve():
            dst = out_dir / f"{src.stem}_compressed_1.mp4"
        # Auto-overwrite in queue mode (no individual prompts)
        item.output_path = dst

        # Backward-compat: set legacy fields used by progress/telemetry code
        self.video_path = str(src)
        self.video_info = item.info
        self.output_path = dst
        self.current_item = item

        preset = PRESETS[self.selected_preset]
        if self.has_nvenc:
            cmd = [
                self.ffmpeg, "-y", "-i", str(src),
                "-c:v", "h264_nvenc",
                "-preset", "p7",
                "-tune", "hq",
                "-profile:v", "high",
                "-rc", "vbr",
                "-cq", str(preset["cq"]),
                "-b:v", "0",
                "-multipass", "fullres",
                "-spatial-aq", "1",
                "-temporal-aq", "1",
                "-bf", "3",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats",
                str(dst),
            ]
        else:
            cmd = [
                self.ffmpeg, "-y", "-i", str(src),
                "-c:v", "libx264",
                "-preset", "medium",
                "-profile:v", "high",
                "-crf", str(preset["crf"]),
                "-bf", "3",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats",
                str(dst),
            ]
        self.encoding = True
        item.state = QueueItem.PROCESSING
        self._encode_start = datetime.datetime.now()
        self._last_logged_pct = -1
        self._render_queue()
        self._update_action_button()
        self.progress.set(0)
        # Status with queue position
        ready_idx = self.queue.index(item) + 1
        total = len(self.queue)
        self._set_status(f"Comprimiendo {ready_idx}/{total} · {item.path.name}", "#3b82f6")
        self.logger.info("=" * 60)
        self.logger.info(f"Encode start · {ready_idx}/{total} · preset: {self.selected_preset} · H.264 · "
                          f"{'h264_nvenc (GPU)' if self.has_nvenc else 'libx264 (CPU)'}")
        self.logger.info(f"Input:  {src}")
        self.logger.info(f"Output: {dst}")
        self.logger.cmd("Comando: " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        if not getattr(self, "_telem_running", False):
            self._start_telemetry()
        threading.Thread(target=self._run_ffmpeg, args=(cmd,), daemon=True).start()

    def _run_ffmpeg(self, cmd):
        total = self.current_item.info["duration"] if self.current_item else 0
        try:
            self.process = popen_quiet(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            self.logger.info(f"PID: {self.process.pid}")
            for line in self.process.stdout:
                if not self.encoding:
                    break
                self._parse_progress(line, total)
            rc = self.process.wait()
            self.logger.info(f"ffmpeg exit code: {rc}")
            if rc == 0 and self.encoding:
                self.after(0, self._on_done)
            elif not self.encoding:
                self.logger.warn("Proceso cancelado por el usuario.")
                self.after(0, self._on_cancelled)
            else:
                self.logger.err(f"ffmpeg terminó con código {rc}")
                self.after(0, self._on_failed, rc)
        except Exception as e:
            self.logger.exception("ejecución de ffmpeg")
            if self.current_item is not None:
                self.current_item.state = QueueItem.FAILED
                self.current_item.error_msg = str(e)
            self.after(0, lambda: self._set_status(f"Error: {e}", "#ef4444"))
            self.after(0, self._render_queue)
            self.after(100, self._start_next_in_queue)

    PROGRESS_KEYS = (
        "frame=", "fps=", "stream_", "bitrate=", "total_size=", "out_time_us=",
        "out_time_ms=", "out_time=", "dup_frames=", "drop_frames=", "speed=",
        "progress=",
    )

    def _parse_progress(self, line, total):
        line = line.rstrip()
        if not line:
            return
        if line.startswith("out_time_ms="):
            try:
                us = int(line.split("=")[1])
                cur = us / 1_000_000
                if total > 0:
                    pct = min(cur / total, 1.0)
                    self.after(0, self.progress.set, pct)
                    eta = (total - cur) / max(self._last_speed, 0.01) if hasattr(self, "_last_speed") and self._last_speed else None
                    self.after(0, self._update_meta, cur, total, eta)
                    self._maybe_log_milestone(pct, cur, total)
            except Exception:
                pass
        elif line.startswith("speed="):
            try:
                v = line.split("=")[1].rstrip("x").strip()
                self._last_speed = float(v) if v not in ("N/A", "") else 0
            except Exception:
                self._last_speed = 0
        elif line.startswith("total_size="):
            try:
                self._last_out_size = int(line.split("=")[1])
            except Exception:
                pass
        elif line.startswith(self.PROGRESS_KEYS):
            return
        else:
            self.logger.ffmpeg(line)

    def _maybe_log_milestone(self, pct, cur, total):
        bucket = int(pct * 10)
        if bucket > self._last_logged_pct:
            self._last_logged_pct = bucket
            speed = getattr(self, "_last_speed", 0)
            out_size = getattr(self, "_last_out_size", 0)
            eta = (total - cur) / max(speed, 0.01) if speed else None
            parts = [f"{bucket*10}%", f"{fmt_time(cur)}/{fmt_time(total)}"]
            if speed:
                parts.append(f"{speed:.2f}x")
            if eta is not None:
                parts.append(f"ETA {fmt_time(eta)}")
            if out_size:
                parts.append(f"out {fmt_size(out_size)}")
            self.logger.info("Progreso · " + " · ".join(parts))

    def _update_meta(self, cur, total, eta):
        speed = getattr(self, "_last_speed", 0)
        out_size = getattr(self, "_last_out_size", 0)
        parts = [f"{fmt_time(cur)} / {fmt_time(total)}"]
        if speed:
            parts.append(f"{speed:.2f}x")
        if eta is not None and speed:
            parts.append(f"ETA {fmt_time(eta)}")
        if out_size:
            parts.append(f"salida: {fmt_size(out_size)}")
        self.progress_meta.configure(text="  ·  ".join(parts))

    def _cancel_queue(self):
        self.logger.warn("Cancelación de cola solicitada por el usuario.")
        self.encoding = False
        # Mark current as cancelled, remaining ready as cancelled
        if self.current_item:
            self.current_item.state = QueueItem.CANCELLED
        for it in self.queue:
            if it.state in (QueueItem.PENDING, QueueItem.READY):
                it.state = QueueItem.CANCELLED
        if self.process:
            try:
                self.process.terminate()
                self.logger.info(f"terminate() enviado a PID {self.process.pid}")
            except Exception as e:
                self.logger.err(f"No pude terminar proceso: {e}")
        if hasattr(self, "output_path") and self.output_path and self.output_path.exists():
            try:
                self.output_path.unlink()
                self.logger.info("Archivo parcial borrado.")
            except Exception as e:
                self.logger.warn(f"No pude borrar archivo parcial: {e}")
        self._stop_telemetry()
        self._render_queue()
        self._update_action_button()
        self._set_status("Cola cancelada.", "#f59e0b")
        self.progress.set(0)
        self.progress_meta.configure(text="")

    def _on_cancelled(self):
        # User-cancelled current item. The cancel handler already marked it.
        # If queue was cancelled, do nothing more here.
        pass

    def _on_failed(self, rc):
        item = self.current_item
        if item is not None:
            item.state = QueueItem.FAILED
            item.error_msg = f"ffmpeg exit {rc}"
            item.elapsed = (datetime.datetime.now() - self._encode_start).total_seconds()
        self.logger.err(f"Item falló: {item.path.name if item else '?'} (exit {rc})")
        self._render_queue()
        # Continue queue with next ready item
        self.current_item = None
        if hasattr(self, "output_path") and self.output_path and self.output_path.exists():
            try:
                if self.output_path.stat().st_size == 0:
                    self.output_path.unlink()
            except Exception:
                pass
        self.after(100, self._start_next_in_queue)
        if hasattr(self, "output_path") and self.output_path.exists():
            try:
                if self.output_path.stat().st_size == 0:
                    self.output_path.unlink()
                    self.logger.info("Archivo de salida vacío eliminado.")
            except Exception as e:
                self.logger.warn(f"No pude limpiar archivo vacío: {e}")

    def _on_done(self):
        item = self.current_item
        if item is None:
            return
        orig = item.info["size"]
        new = item.output_path.stat().st_size if item.output_path.exists() else 0
        elapsed = (datetime.datetime.now() - self._encode_start).total_seconds()
        item.state = QueueItem.DONE
        item.size_after = new
        item.elapsed = elapsed
        ratio = (new / orig * 100) if orig else 0
        saved = (1 - new / orig) * 100 if orig else 0
        self.logger.ok(f"Item terminado: {item.path.name} en {fmt_time(elapsed)}")
        self.logger.ok(f"Tamaño: {fmt_size(orig)} → {fmt_size(new)}  ({ratio:.1f}% · ahorro {saved:.1f}%)")
        if elapsed > 0:
            realtime = item.info["duration"] / elapsed
            self.logger.info(f"Velocidad media: {realtime:.2f}x realtime")
        self.logger.info("=" * 60)
        self._render_queue()
        # Continue with next
        self.current_item = None
        self.after(100, self._start_next_in_queue)

    def _notify_queue_done(self, done, failed, cancelled, elapsed):
        title = "✓ Cola completa" if not (failed or cancelled) else "Cola terminada"
        if failed:
            title = "⚠ Cola con fallos"
        msg_parts = [f"{done} comprimido(s)"]
        if failed:
            msg_parts.append(f"{failed} fallado(s)")
        if cancelled:
            msg_parts.append(f"{cancelled} cancelado(s)")
        msg_parts.append(f"Tiempo total: {fmt_time(elapsed)}")
        try:
            from winotify import Notification
            n = Notification(
                app_id="Compresser",
                title=title,
                msg="\n".join(msg_parts),
                duration="short",
            )
            try:
                if self.output_dir:
                    n.add_actions(label="Abrir carpeta", launch=str(self.output_dir))
                elif done and self.queue:
                    first_done = next((it for it in self.queue if it.state == QueueItem.DONE), None)
                    if first_done and first_done.output_path:
                        n.add_actions(label="Abrir carpeta", launch=str(first_done.output_path.parent))
            except Exception:
                pass
            n.show()
            self.logger.info("Toast 'cola completa' enviado")
            return
        except Exception as e:
            self.logger.debug(f"Toast falló: {e}")
        try:
            import winsound
            if failed:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            else:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

    def _notify_done(self):
        toast_ok = False
        try:
            from winotify import Notification
            orig = self.video_info["size"]
            new = self.output_path.stat().st_size if self.output_path.exists() else 0
            saved = (1 - new / orig) * 100 if orig else 0
            n = Notification(
                app_id="Compresser",
                title="✓ Compresión terminada",
                msg=f"{Path(self.video_path).name}\n"
                    f"{fmt_size(orig)} → {fmt_size(new)} · ahorrás {saved:.0f}%",
                duration="short",
            )
            try:
                n.add_actions(label="Abrir carpeta", launch=str(self.output_path.parent))
            except Exception:
                pass
            n.show()
            toast_ok = True
            self.logger.info("Toast 'completado' enviado")
        except Exception as e:
            self.logger.debug(f"Toast falló: {e}")
        if not toast_ok:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

    def _notify_failed(self, rc):
        toast_ok = False
        try:
            from winotify import Notification
            n = Notification(
                app_id="Compresser",
                title="✗ Compresión falló",
                msg=f"{Path(self.video_path).name if self.video_path else 'Video'}\n"
                    f"ffmpeg exit {rc} · revisá los logs",
                duration="short",
            )
            n.show()
            toast_ok = True
            self.logger.info("Toast 'falló' enviado")
        except Exception as e:
            self.logger.debug(f"Toast falló: {e}")
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass

    def _reset_action_btn(self):
        # Legacy alias — current code uses _update_action_button.
        self._update_action_button()


if __name__ == "__main__":
    app = App()
    # Si se pasó un archivo como argumento (asociación de menú contextual),
    # cargarlo automáticamente cuando ffprobe esté listo.
    if len(sys.argv) > 1:
        cli_paths = [p for p in sys.argv[1:] if Path(p).exists()]
        missing = [p for p in sys.argv[1:] if not Path(p).exists()]
        for m in missing:
            app.logger.warn(f"Argumento CLI no existe: {m}")
        if cli_paths:
            app.logger.info(f"Argumentos CLI: {len(cli_paths)} archivo(s)")
            def _try_autoload(paths=cli_paths):
                if app.ffprobe:
                    for p in paths:
                        app._add_to_queue(p)
                else:
                    app.after(200, _try_autoload)
            app.after(300, _try_autoload)
    app.mainloop()
