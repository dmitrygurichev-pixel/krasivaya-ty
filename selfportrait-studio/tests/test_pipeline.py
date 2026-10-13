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
        f"processing: {{steps: {steps or []}}}\n", encoding="utf-8")
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
