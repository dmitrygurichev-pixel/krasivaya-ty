from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config
from .app import App
from .yadisk import YandexDisk, YaDiskError


def main() -> int:
    ap = argparse.ArgumentParser(prog="studio", description="Студия автопортрета: камера -> обработка -> Яндекс Диск")
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--once", action="store_true", help="один проход и выход (для проверки)")
    ap.add_argument("--check", action="store_true", help="проверить токен и доступ к Яндекс Диску")
    args = ap.parse_args()

    cfg = config.load(args.config)
    logs = Path(cfg["paths"]["work_dir"]) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(logs / "studio.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"),
                  logging.StreamHandler()],
    )
    if args.check:
        try:
            info = YandexDisk(config.get_token(cfg), cfg["yandex_disk"]["timeout"]).check()
        except (YaDiskError, RuntimeError) as e:
            print("ОШИБКА:", e)
            return 1
        free = (info["total_space"] - info["used_space"]) / 2**30
        print(f"OK. Диск пользователя {info.get('user', {}).get('login', '?')}, свободно {free:.1f} ГБ")
        return 0
    app = App(cfg)
    if args.once:
        app.run_once()
        print(app.db.counts())
    else:
        app.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
