"""Основной цикл: снимок из камеры -> обработка -> Яндекс Диск. Без участия человека."""
from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import processing
from .config import get_token
from .db import JobDB
from .yadisk import YandexDisk, YaDiskError

log = logging.getLogger("studio")


class Gphoto2Tether:
    """Держит запущенным `gphoto2 --capture-tethered`; перезапускает при обрыве/отключении камеры."""

    def __init__(self, cfg: dict, inbox: Path):
        g = cfg["camera"]["gphoto2"]
        self.cmd = [g["binary"], "--capture-tethered", "--filename", str(inbox / "%Y%m%d_%H%M%S_%n.%C")]
        self.cmd.append("--keep" if g["keep_on_camera"] else "--no-keep")
        self.proc: subprocess.Popen | None = None
        self.last_start = 0.0

    def ensure_running(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if self.proc:
            log.warning("gphoto2 завершился (код %s), перезапуск", self.proc.returncode)
        if time.time() - self.last_start < 5:
            return
        self.last_start = time.time()
        try:
            self.proc = subprocess.Popen(self.cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("gphoto2 запущен: %s", " ".join(self.cmd))
        except OSError as e:
            log.error("не удалось запустить gphoto2: %s", e)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class App:
    def __init__(self, cfg: dict, disk: YandexDisk | None = None):
        self.cfg = cfg
        cam, rt = cfg["camera"], cfg["runtime"]
        self.inbox = Path(cam["inbox"])
        self.exts = {"." + e.lower().lstrip(".") for e in cam["extensions"]}
        work = Path(cfg["paths"]["work_dir"])
        self.processed_dir, self.archive_dir, self.failed_dir = (work / n for n in ("processed", "archive", "failed"))
        for d in (self.inbox, work, self.processed_dir, self.archive_dir, self.failed_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db = JobDB(str(work / "jobs.sqlite"))
        processing.validate_steps(cfg["processing"]["steps"])
        yd = cfg["yandex_disk"]
        self.disk = disk or YandexDisk(get_token(cfg), yd["timeout"])
        self.remote_root = "/" + yd["remote_root"].strip("/")
        self.rt = rt
        self.tether = Gphoto2Tether(cfg, self.inbox) if cam["mode"] == "gphoto2" else None
        self._stop = False

    # --- 1. обнаружение новых снимков -------------------------------------------------
    def scan(self) -> None:
        now = time.time()
        stable = self.cfg["camera"]["stable_seconds"]
        for p in self.inbox.rglob("*"):
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in self.exts:
                continue
            st = p.stat()
            if st.st_size == 0 or now - st.st_mtime < stable:
                continue  # файл ещё записывается
            key = f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"
            if self.db.add(key, str(p)):
                log.info("новый снимок: %s", p.name)

    # --- 2. обработка -----------------------------------------------------------------
    def process_all(self) -> None:
        for job in self.db.due("new"):
            src = Path(job["src"])
            ts = datetime.fromtimestamp(src.stat().st_mtime) if src.exists() else datetime.now()
            out = self.processed_dir / f"{job['id']:06d}_{src.stem}.jpg"
            remote = f"{self.remote_root}/{ts:%Y-%m-%d}/{ts:%Y%m%d_%H%M%S}_{src.stem}.jpg"
            try:
                processing.process(src, out, self.cfg["processing"]["steps"], self.cfg["processing"]["quality"])
            except Exception as e:  # битый/недописанный файл
                n = job["attempts"] + 1
                if n >= self.rt["max_process_attempts"]:
                    log.error("снимок %s не обработан, переносим в failed: %s", src.name, e)
                    if src.exists():
                        shutil.move(src, self.failed_dir / f"{job['id']:06d}_{src.name}")
                    self.db.update(job["id"], status="failed", attempts=n, error=str(e))
                else:
                    log.warning("ошибка обработки %s (попытка %d): %s", src.name, n, e)
                    self.db.update(job["id"], attempts=n, error=str(e), next_try=time.time() + 5 * n)
                continue
            self.db.update(job["id"], status="processed", processed_path=str(out), remote_path=remote,
                           attempts=0, error=None)
            log.info("обработан: %s", src.name)

    # --- 3. выгрузка ------------------------------------------------------------------
    def upload_all(self) -> None:
        for job in self.db.due("processed"):
            try:
                self.disk.ensure_dir(str(Path(job["remote_path"]).parent.as_posix()))
                self.disk.upload(job["processed_path"], job["remote_path"])
                url = self.disk.publish(job["remote_path"]) if self.cfg["yandex_disk"]["publish"] else None
            except YaDiskError as e:
                n = job["attempts"] + 1
                delay = min(300, 5 * 2 ** min(n, 6))  # повторяем бесконечно: интернет/токен могут вернуться
                log.error("выгрузка %s не удалась (попытка %d, повтор через %dс): %s", job["remote_path"], n, delay, e)
                self.db.update(job["id"], attempts=n, error=str(e), next_try=time.time() + delay)
                if e.status is None:  # нет сети — остальные пробовать бессмысленно
                    return
                continue
            self.db.update(job["id"], status="uploaded", public_url=url, error=None)
            log.info("выгружено: %s %s", job["remote_path"], url or "")
            self._cleanup(job)

    def _cleanup(self, job) -> None:
        src, proc = Path(job["src"]), Path(job["processed_path"])
        if src.exists():
            if self.rt["keep_originals"]:
                shutil.move(src, self.archive_dir / f"{job['id']:06d}_{src.name}")
            else:
                src.unlink()
        if not self.rt["keep_processed"]:
            proc.unlink(missing_ok=True)

    # --- цикл -------------------------------------------------------------------------
    def run_once(self) -> None:
        if self.tether:
            self.tether.ensure_running()
        self.scan()
        self.process_all()
        self.upload_all()

    def run_forever(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: setattr(self, "_stop", True))
        log.info("запущено; очередь: %s", self.db.counts())
        last_report = time.time()
        while not self._stop:
            try:
                self.run_once()
            except Exception:  # служба не должна падать ни при каких ошибках
                log.exception("неожиданная ошибка цикла")
            if time.time() - last_report > 3600:
                log.info("состояние очереди: %s", self.db.counts())
                last_report = time.time()
            time.sleep(self.rt["poll_interval"])
        if self.tether:
            self.tether.stop()
        log.info("остановлено")
