import pytest
import requests

from studio import yadisk
from studio.yadisk import YandexDisk, YaDiskError


class Resp:
    def __init__(self, status, js=None):
        self.status_code, self._js, self.text = status, js or {}, str(js)

    def json(self): return self._js


class Sess:
    def __init__(self, handler):
        self.headers, self.handler, self.calls = {}, handler, []

    def request(self, method, url, **kw):
        self.calls.append((method, kw.get("params", {}).get("path")))
        return self.handler(method, url, kw)


def test_ensure_dir_creates_each_level_once():
    s = Sess(lambda m, u, kw: Resp(409 if kw["params"]["path"] == "/a" else 201))
    d = YandexDisk("t", session=s)
    d.ensure_dir("/a/b/c")
    d.ensure_dir("/a/b/c")
    assert [c[1] for c in s.calls] == ["/a", "/a/b", "/a/b/c"]
    assert s.headers["Authorization"] == "OAuth t"


def test_upload_flow(monkeypatch, tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"data")
    put = {}
    monkeypatch.setattr(yadisk.requests, "put",
                        lambda href, data, timeout: put.update(href=href, body=data.read()) or Resp(201))
    s = Sess(lambda m, u, kw: Resp(200, {"href": "https://up/x"}))
    YandexDisk("t", session=s).upload(str(f), "/a/x.jpg")
    assert put == {"href": "https://up/x", "body": b"data"}


def test_auth_error_has_status_and_network_error_has_none(tmp_path):
    d = YandexDisk("bad", session=Sess(lambda m, u, kw: Resp(401, {"message": "x"})))
    with pytest.raises(YaDiskError) as e:
        d.check()
    assert e.value.status == 401

    def boom(m, u, kw): raise requests.ConnectionError("down")
    with pytest.raises(YaDiskError) as e:
        YandexDisk("t", session=Sess(boom)).check()
    assert e.value.status is None
