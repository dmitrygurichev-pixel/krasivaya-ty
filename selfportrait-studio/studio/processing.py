"""Конвейер обработки снимка: последовательность шагов из конфигурации."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


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


def monotone_lut(points) -> list[int]:
    """Гладкая монотонная кривая (Фритч–Батлер) через точки [[вход, выход], ...] -> таблица на 256 значений."""
    pts = sorted((float(x), float(y)) for x, y in points)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    n = len(xs)
    if n < 2:
        raise ValueError("для кривой нужно минимум 2 точки")
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else 2 / (1 / d[i - 1] + 1 / d[i]) for i in range(1, n - 1)] + [d[-1]]
    lut = []
    for x in range(256):
        if x <= xs[0]:
            y = ys[0]
        elif x >= xs[-1]:
            y = ys[-1]
        else:
            i = max(j for j in range(n - 1) if xs[j] <= x)
            t = (x - xs[i]) / h[i]
            y = ((2 * t**3 - 3 * t**2 + 1) * ys[i] + (t**3 - 2 * t**2 + t) * h[i] * m[i]
                 + (-2 * t**3 + 3 * t**2) * ys[i + 1] + (t**3 - t**2) * h[i] * m[i + 1])
        lut.append(min(255, max(0, round(y))))
    return lut


def _curve(img, points=None, r=None, g=None, b=None, **_):
    """Тоновая кривая. `points` — для всех каналов сразу, либо отдельно `r`, `g`, `b`."""
    chans = [monotone_lut(c or points) if (c or points) else list(range(256)) for c in (r, g, b)]
    return img.point(chans[0] + chans[1] + chans[2])


def _gray_mix(img, weights=(0.299, 0.587, 0.114), **_):
    """Чёрно-белое с выбором вклада каналов (больше красного — светлее кожа)."""
    total = sum(float(x) for x in weights)
    matrix = tuple(float(x) / total for x in weights) + (0.0,)
    return img.convert("RGB").convert("L", matrix).convert("RGB")


def _unsharp(img, radius=1.2, percent=60, threshold=0, **_):
    return img.filter(ImageFilter.UnsharpMask(radius, percent, threshold))

def _warm_boost(img, center=22, width=22, gamma=1.3, saturation=1.0, **_):
    """Выборочно осветляет и насыщает тёплые оттенки (волосы, кожа), не трогая нейтральные тона.

    center/width — оттенок в шкале Pillow 0..255 (22 ≈ 31°, оранжевый); gamma > 1 осветляет середину.
    """
    h, s, v = img.convert("HSV").split()
    window = h.point([round(255 * max(0.0, math.cos(math.pi / 2 * min(1.0, abs(((i - center + 128) % 256) - 128) / width))))
                      for i in range(256)])
    mask = ImageChops.multiply(window, s.point(lambda x: min(255, x * 2)))  # серые пиксели не затрагиваем
    v2 = v.point([round(255 * (i / 255) ** (1 / gamma)) for i in range(256)])
    s2 = s.point([min(255, round(i * saturation)) for i in range(256)])
    return Image.merge("HSV", (h, Image.composite(s2, s, mask), Image.composite(v2, v, mask))).convert("RGB")



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
    "curve": _curve,
    "gray_mix": _gray_mix,
    "unsharp": _unsharp,
    "warm_boost": _warm_boost,
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
