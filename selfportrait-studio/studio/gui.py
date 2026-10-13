"""Окно для проверки: тестовое фото -> обработка -> (по желанию) Яндекс Диск, и запуск автопилота."""
from __future__ import annotations

import logging
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image, ImageTk

from . import config, processing
from .app import App
from .yadisk import YandexDisk, YaDiskError

log = logging.getLogger("studio")


def app_dir() -> Path:
    """Папка с config.yaml: рядом с .exe, иначе текущая."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()


def example_config() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "config.example.yaml"


def ensure_config(d: Path) -> Path:
    cfg = d / "config.yaml"
    if not cfg.exists():
        shutil.copy(example_config(), cfg)
    return cfg


class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


class Window(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Студия автопортрета")
        self.geometry("1100x760")
        self.dir = app_dir()
        self.cfg_path = ensure_config(self.dir)
        self.token_path = self.dir / "token.txt"
        self.logq: queue.Queue = queue.Queue()
        self.app: App | None = None
        self.thread: threading.Thread | None = None
        self.photos: list = []

        h = QueueHandler(self.logq)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(h)
        logging.getLogger().setLevel(logging.INFO)

        self._build()
        self.after(200, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # --- интерфейс -------------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=8); top.pack(fill="x")
        ttk.Label(top, text="Токен Яндекс Диска:").grid(row=0, column=0, sticky="w")
        self.token = tk.StringVar(value=self._saved_token())
        ttk.Entry(top, textvariable=self.token, show="•", width=48).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="Сохранить и проверить", command=self.check_disk).grid(row=0, column=2)
        ttk.Label(top, text="Папка камеры (inbox):").grid(row=1, column=0, sticky="w", pady=4)
        self.inbox = tk.StringVar(value=config.load(self.cfg_path)["camera"]["inbox"])
        ttk.Entry(top, textvariable=self.inbox, width=48).grid(row=1, column=1, padx=6)
        ttk.Button(top, text="Выбрать…", command=self.pick_inbox).grid(row=1, column=2)
        ttk.Label(top, text=f"Настройки обработки: {self.cfg_path}  (откройте в блокноте, чтобы менять)",
                  foreground="#666").grid(row=2, column=0, columnspan=3, sticky="w")

        box = ttk.LabelFrame(self, text="1. Проверка обработки на одном фото", padding=8)
        box.pack(fill="x", padx=8, pady=4)
        self.upload = tk.BooleanVar(value=False)
        ttk.Button(box, text="Выбрать фото и обработать…", command=self.test_photo).pack(side="left")
        ttk.Checkbutton(box, text="ещё и выгрузить на Яндекс Диск (в папку _test)", variable=self.upload
                        ).pack(side="left", padx=12)
        ttk.Button(box, text="Открыть папку с результатами", command=self.open_out).pack(side="right")

        self.preview = ttk.Frame(self); self.preview.pack(fill="x", padx=8)

        auto = ttk.LabelFrame(self, text="2. Автопилот (следит за папкой камеры, обрабатывает, выгружает)", padding=8)
        auto.pack(fill="x", padx=8, pady=4)
        self.btn = ttk.Button(auto, text="Запустить автопилот", command=self.toggle_auto); self.btn.pack(side="left")
        self.status = ttk.Label(auto, text="остановлен"); self.status.pack(side="left", padx=12)

        self.logbox = scrolledtext.ScrolledText(self, height=12, state="disabled")
        self.logbox.pack(fill="both", expand=True, padx=8, pady=8)

    def _saved_token(self) -> str:
        return self.token_path.read_text(encoding="utf-8").strip() if self.token_path.exists() else os.environ.get("YADISK_TOKEN", "")

    def _write(self, text: str):
        self.logbox.configure(state="normal"); self.logbox.insert("end", text + "\n")
        self.logbox.see("end"); self.logbox.configure(state="disabled")

    def _pump(self):
        while not self.logq.empty():
            item = self.logq.get()
            if isinstance(item, tuple):  # сообщение из рабочего потока: показать превью
                self.show(item[1])
            else:
                self._write(item)
        if self.app and self.thread and self.thread.is_alive():
            self.status.config(text=f"работает · очередь: {self.app.db_counts()}")
        self.after(300, self._pump)

    # --- действия --------------------------------------------------------------------
    def _cfg(self) -> dict:
        cfg = config.load(self.cfg_path)
        cfg["camera"]["inbox"] = self.inbox.get()
        return cfg

    def _apply_token(self):
        t = self.token.get().strip()
        if t:
            self.token_path.write_text(t, encoding="utf-8")
            os.environ["YADISK_TOKEN"] = t
        return t

    def pick_inbox(self):
        d = filedialog.askdirectory(initialdir=self.inbox.get() or str(self.dir))
        if d:
            self.inbox.set(d)

    def check_disk(self):
        token = self._apply_token()
        if not token:
            return messagebox.showwarning("Токен", "Вставьте токен Яндекс Диска")
        def work():
            try:
                info = YandexDisk(token).check()
                free = (info["total_space"] - info["used_space"]) / 2**30
                log.info("Яндекс Диск OK: %s, свободно %.1f ГБ", info.get("user", {}).get("login", "?"), free)
            except YaDiskError as e:
                log.error("Яндекс Диск: %s", e)
        threading.Thread(target=work, daemon=True).start()

    def out_dir(self) -> Path:
        d = self.dir / "test_output"; d.mkdir(exist_ok=True); return d

    def open_out(self):
        d = self.out_dir()
        os.startfile(d) if sys.platform == "win32" else log.info("Папка результатов: %s", d)

    def test_photo(self):
        p = filedialog.askopenfilename(title="Тестовое фото", filetypes=[("Фото", "*.jpg *.jpeg *.png *.tif *.tiff")])
        if not p:
            return
        cfg = self._cfg(); token = self._apply_token(); do_upload = self.upload.get()  # tk-переменные читаем только в главном потоке
        def work():
            try:
                outs = []
                for v in cfg["processing"]["variants"]:
                    name = v.get("name", "")
                    out = self.out_dir() / f"{Path(p).stem}{'_' + name if name else ''}.jpg"
                    processing.process(p, out, v.get("steps", []), cfg["processing"]["quality"])
                    outs.append(out); log.info("готово: %s", out.name)
                self.logq.put(("show", [Path(p)] + outs))
                if do_upload:
                    if not token:
                        return log.error("для выгрузки нужен токен")
                    disk = YandexDisk(token); root = "/" + cfg["yandex_disk"]["remote_root"].strip("/") + "/_test"
                    disk.ensure_dir(root)
                    for o in outs:
                        disk.upload(str(o), f"{root}/{o.name}"); log.info("выгружено: %s/%s", root, o.name)
            except Exception as e:
                log.error("ошибка: %s", e)
        threading.Thread(target=work, daemon=True).start()

    def show(self, paths):
        for w in self.preview.winfo_children():
            w.destroy()
        self.photos = []
        for i, p in enumerate(paths):
            im = Image.open(p).convert("RGB"); im.thumbnail((300, 380))
            ph = ImageTk.PhotoImage(im); self.photos.append(ph)
            f = ttk.Frame(self.preview); f.grid(row=0, column=i, padx=6)
            ttk.Label(f, image=ph).pack(); ttk.Label(f, text="оригинал" if i == 0 else p.stem.rsplit("_", 1)[-1]).pack()

    def toggle_auto(self):
        if self.thread and self.thread.is_alive():
            self.app.stop(); self.btn.config(text="Запустить автопилот"); self.status.config(text="останавливается…")
            return
        if not self._apply_token():
            return messagebox.showwarning("Токен", "Сначала вставьте токен и нажмите «Сохранить и проверить»")
        try:
            self.app = App(self._cfg())
        except Exception as e:
            return messagebox.showerror("Не запускается", str(e))
        self.thread = threading.Thread(target=lambda: self.app.run_forever(handle_signals=False), daemon=True)
        self.thread.start(); self.btn.config(text="Остановить автопилот")

    def _close(self):
        if self.app:
            self.app.stop()
        self.destroy()


def main() -> int:
    Window().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
