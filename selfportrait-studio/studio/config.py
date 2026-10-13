"""Загрузка конфигурации (YAML) с значениями по умолчанию."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "camera": {
        "mode": "folder",  # folder | gphoto2
        "inbox": "./inbox",
        "extensions": ["jpg", "jpeg", "png", "tif", "tiff"],
        "stable_seconds": 3,
        "gphoto2": {"binary": "gphoto2", "keep_on_camera": True},
    },
    "paths": {"work_dir": "./data"},
    "processing": {"quality": 92, "variants": [{"name": "", "steps": []}]},
    "yandex_disk": {
        "token_env": "YADISK_TOKEN",
        "token_file": None,
        "remote_root": "/Автопортрет",
        "publish": False,
        "timeout": 60,
    },
    "runtime": {
        "poll_interval": 2,
        "keep_originals": True,
        "keep_processed": False,
        "max_process_attempts": 3,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike) -> dict:
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    # относительные пути считаем от папки с конфигом, а не от cwd (важно для служб)
    base = p.resolve().parent
    for section, key in (("camera", "inbox"), ("paths", "work_dir")):
        v = Path(cfg[section][key])
        cfg[section][key] = str(v if v.is_absolute() else (base / v).resolve())
    tf = cfg["yandex_disk"].get("token_file")
    if tf and not Path(tf).is_absolute():
        cfg["yandex_disk"]["token_file"] = str((base / tf).resolve())
    for variant in cfg["processing"]["variants"]:
        for step in variant.get("steps", []):
            if step.get("path") and not Path(step["path"]).is_absolute():
                step["path"] = str((base / step["path"]).resolve())
    return cfg


def get_token(cfg: dict) -> str:
    yd = cfg["yandex_disk"]
    token = os.environ.get(yd["token_env"], "").strip()
    if not token and yd.get("token_file"):
        token = Path(yd["token_file"]).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(
            f"Не найден OAuth-токен Яндекс Диска: задайте переменную {yd['token_env']} "
            "или yandex_disk.token_file"
        )
    return token
