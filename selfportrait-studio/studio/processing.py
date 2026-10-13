"""Конвейер обработки снимка: последовательность шагов из конфигурации."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageEnhance, ImageOps


def _autocontrast(img, cutoff=1, **_):
    return ImageOps.autocontrast(img, cutoff=cutoff)


def _enhancer(cls):
    def f(img, factor=1.0, **_):
        return cls(img).enhance(factor)
    return f


def _grayscale(img, **_):
    return ImageOps.grayscale(img).convert("RGB")


def _crop_ratio(img, ratio="4:5", **_):
    """Центральная обрезка до заданного соотношения сторон (ширина:высота)."""
    rw, rh = (float(x) for x in str(ratio).split(":"))
    target = rw / rh
    w, h = img.size
    if w / h > target:
        nw = round(h * target)
        left = (w - nw) // 2
        return img.crop((left, 0, left + nw, h))
    nh = round(w / target)
    top = (h - nh) // 2
    return img.crop((0, top, w, top + nh))


def _resize(img, max_side=2400, **_):
    """Уменьшает так, чтобы длинная сторона не превышала max_side (не увеличивает)."""
    w, h = img.size
    scale = max_side / max(w, h)
    if scale >= 1:
        return img
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def _border(img, size=40, color="white", **_):
    return ImageOps.expand(img, border=size, fill=color)


def _watermark(img, path, position="bottom-right", scale=0.2, margin=0.03, opacity=1.0, **_):
    """Накладывает PNG-логотип. scale/margin — доли от ширины кадра."""
    logo = Image.open(path).convert("RGBA")
    lw = max(1, round(img.width * scale))
    logo = logo.resize((lw, max(1, round(logo.height * lw / logo.width))), Image.LANCZOS)
    if opacity < 1.0:
        alpha = logo.getchannel("A").point(lambda a: int(a * opacity))
        logo.putalpha(alpha)
    m = round(img.width * margin)
    vert, _, horiz = position.partition("-")
    x = m if horiz == "left" else (img.width - lw) // 2 if horiz == "center" else img.width - lw - m
    y = m if vert == "top" else (img.height - logo.height) // 2 if vert == "center" else img.height - logo.height - m
    base = img.convert("RGBA")
    base.alpha_composite(logo, (x, y))
    return base.convert("RGB")


STEPS: dict[str, Callable] = {
    "autocontrast": _autocontrast,
    "brightness": _enhancer(ImageEnhance.Brightness),
    "contrast": _enhancer(ImageEnhance.Contrast),
    "color": _enhancer(ImageEnhance.Color),
    "sharpness": _enhancer(ImageEnhance.Sharpness),
    "grayscale": _grayscale,
    "crop_ratio": _crop_ratio,
    "resize": _resize,
    "border": _border,
    "watermark": _watermark,
}


def validate_steps(steps: list[dict]) -> None:
    for s in steps:
        if s.get("op") not in STEPS:
            raise ValueError(f"Неизвестный шаг обработки {s.get('op')!r}; доступны: {sorted(STEPS)}")


def process(src: str | Path, dst: str | Path, steps: list[dict], quality: int = 92) -> Path:
    """Применяет шаги к src и сохраняет JPEG в dst. Пишет через временный файл."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.load()  # падает на оборванных файлах — их повторим позже
        img = ImageOps.exif_transpose(im).convert("RGB")
    for s in steps:
        params = {k: v for k, v in s.items() if k != "op"}
        img = STEPS[s["op"]](img, **params)
    tmp = dst.with_suffix(".tmp")
    img.save(tmp, format="JPEG", quality=quality, optimize=True, subsampling=0)
    tmp.replace(dst)
    return dst
