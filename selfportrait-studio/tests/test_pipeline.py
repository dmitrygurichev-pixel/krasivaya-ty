import os
import time
from pathlib import Path

import pytest
from PIL import Image

from studio import config, processing
from studio.app import App
from studio.yadisk import YaDiskError


class FakeDisk:
    def __init__(self):
        self.files, self.fail = {}, 0

    def ensure_dir(self, path): pass

    def upload(self, local, remote):
        if self.fail:
            self.fail -= 1
            raise YaDiskError("сеть: down")
        self.files[remote] = Path(local).read_bytes()

    def publish(self, remote): return "https://disk.yandex.ru/i/x"


def make_cfg(tmp_path, steps=None):
    (tmp_path / "config.yaml").write_text(
        f"camera: {{inbox: ./inbox, stable_seconds: 0}}\n"
        f"runtime: {{keep_originals: true}}\n"
        f"processing: {{variants: [{{name: '', steps: {steps or []}}}]}}\n", encoding="utf-8")
    return config.load(tmp_path / "config.yaml")


def shot(inbox, name="IMG_1.jpg", size=(1000, 600)):
    inbox.mkdir(exist_ok=True)
    p = inbox / name
    Image.new("RGB", size, (120, 90, 60)).save(p)
    os.utime(p, (time.time() - 10,) * 2)
    return p


def test_full_cycle(tmp_path):
    cfg = make_cfg(tmp_path, '[{op: crop_ratio, ratio: "4:5"}, {op: resize, max_side: 400}]')
    disk = FakeDisk()
    app = App(cfg, disk)
    shot(tmp_path / "inbox")
    app.run_once()
    assert len(disk.files) == 1
    remote = next(iter(disk.files))
    assert remote.startswith("/Автопортрет/") and remote.endswith("_IMG_1.jpg")
    out = tmp_path / "out.jpg"
    out.write_bytes(disk.files[remote])
    assert Image.open(out).size == (320, 400)       # 4:5 и длинная сторона 400
    assert list((tmp_path / "data" / "archive").iterdir())  # оригинал заархивирован
    assert not list((tmp_path / "data" / "processed").iterdir())
    assert app.db.counts() == {"uploaded": 1}
    app.run_once()                                   # повторно не выгружает
    assert len(disk.files) == 1


def test_upload_retry_after_network_failure(tmp_path):
    app = App(make_cfg(tmp_path), disk := FakeDisk())
    disk.fail = 1
    shot(tmp_path / "inbox")
    app.run_once()
    assert app.db.counts() == {"processed": 1} and not disk.files
    app.db.c.execute("UPDATE jobs SET next_try=0"); app.db.c.commit()
    app.run_once()
    assert app.db.counts() == {"uploaded": 1}


def test_corrupt_file_goes_to_failed(tmp_path):
    app = App(make_cfg(tmp_path), FakeDisk())
    bad = tmp_path / "inbox" / "bad.jpg"
    bad.parent.mkdir(exist_ok=True)
    bad.write_bytes(b"not an image")
    os.utime(bad, (time.time() - 10,) * 2)
    for _ in range(3):
        app.run_once()
        app.db.c.execute("UPDATE jobs SET next_try=0"); app.db.c.commit()
    assert app.db.counts() == {"failed": 1}
    assert list((tmp_path / "data" / "failed").iterdir())


def test_fresh_file_is_not_taken(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg["camera"]["stable_seconds"] = 60
    app = App(cfg, FakeDisk())
    p = shot(tmp_path / "inbox")
    os.utime(p, None)
    app.run_once()
    assert app.db.counts() == {}


def test_watermark_and_unknown_step(tmp_path):
    Image.new("RGBA", (50, 20), (255, 0, 0, 255)).save(tmp_path / "logo.png")
    src = shot(tmp_path)
    out = processing.process(src, tmp_path / "o.jpg",
                             [{"op": "watermark", "path": str(tmp_path / "logo.png"), "scale": 0.2}])
    px = Image.open(out).getpixel((870, 530))
    assert px[0] > 200 and px[1] < 80                # красный логотип в правом нижнем углу
    with pytest.raises(ValueError):
        processing.validate_steps([{"op": "nope"}])


def test_two_variants_and_original_archived_once(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "camera: {stable_seconds: 0}\n"
        "processing:\n  variants:\n"
        "    - {name: color, steps: [{op: curve, r: [[0,0],[128,200],[255,255]]}]}\n"
        "    - {name: bw, steps: [{op: gray_mix, weights: [0.5,0.4,0.1]}]}\n", encoding="utf-8")
    disk = FakeDisk()
    app = App(config.load(tmp_path / "config.yaml"), disk)
    shot(tmp_path / "inbox")
    app.run_once()
    names = sorted(k.rsplit("_", 1)[1] for k in disk.files)
    assert names == ["bw.jpg", "color.jpg"]
    assert len(list((tmp_path / "data" / "archive").iterdir())) == 1
    assert not list((tmp_path / "inbox").iterdir())
    bw = tmp_path / "bw.jpg"
    bw.write_bytes(next(v for k, v in disk.files.items() if k.endswith("_bw.jpg")))
    r, g, b = Image.open(bw).getpixel((5, 5))
    assert abs(r - g) < 3 and abs(g - b) < 3          # действительно чёрно-белый


def test_curve_brightens_and_is_monotone():
    lut = processing.monotone_lut([[0, 0], [64, 111], [128, 178], [255, 255]])
    assert lut[64] == 111 and lut[0] == 0 and lut[255] == 255
    assert all(a <= b for a, b in zip(lut, lut[1:]))


def test_warm_boost_lifts_warm_but_not_neutral(tmp_path):
    warm = Image.new("RGB", (8, 8), (160, 110, 90))
    grey = Image.new("RGB", (8, 8), (120, 120, 120))
    w = processing.STEPS["warm_boost"](warm, gamma=1.3)
    g = processing.STEPS["warm_boost"](grey, gamma=1.3)
    assert sum(w.getpixel((0, 0))) > sum(warm.getpixel((0, 0))) + 15
    assert g.getpixel((0, 0)) == grey.getpixel((0, 0))
