"""Клиент REST API Яндекс Диска (https://yandex.ru/dev/disk-api/)."""
from __future__ import annotations

from pathlib import PurePosixPath

import requests

API = "https://cloud-api.yandex.net/v1/disk"


class YaDiskError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class YandexDisk:
    def __init__(self, token: str, timeout: float = 60, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"OAuth {token}"
        self.timeout = timeout
        self._dirs: set[str] = set()

    def _call(self, method: str, url: str, **kw) -> requests.Response:
        try:
            r = self.s.request(method, url, timeout=self.timeout, **kw)
        except requests.RequestException as e:  # сеть недоступна, таймаут и т.п.
            raise YaDiskError(f"сеть: {e}") from e
        return r

    @staticmethod
    def _err(r: requests.Response, what: str) -> YaDiskError:
        hint = " (токен недействителен или нет прав cloud_api:disk.write)" if r.status_code in (401, 403) else ""
        return YaDiskError(f"{what}: HTTP {r.status_code} {r.text[:200]}{hint}", r.status_code)

    def check(self) -> dict:
        r = self._call("GET", API)
        if r.status_code != 200:
            raise self._err(r, "проверка доступа")
        return r.json()

    def ensure_dir(self, path: str) -> None:
        """Создаёт все уровни папки (409 = уже существует)."""
        cur = ""
        for part in PurePosixPath(path).parts:
            if part == "/":
                continue
            cur = f"{cur}/{part}"
            if cur in self._dirs:
                continue
            r = self._call("PUT", f"{API}/resources", params={"path": cur})
            if r.status_code not in (201, 409):
                raise self._err(r, f"создание папки {cur}")
            self._dirs.add(cur)

    def upload(self, local: str, remote: str) -> None:
        r = self._call("GET", f"{API}/resources/upload", params={"path": remote, "overwrite": "true"})
        if r.status_code == 404:  # папку удалили вручную — сбросим кэш
            self._dirs.clear()
        if r.status_code != 200:
            raise self._err(r, f"получение ссылки загрузки {remote}")
        href = r.json()["href"]
        with open(local, "rb") as f:
            # ссылка загрузки не требует заголовка авторизации
            try:
                up = requests.put(href, data=f, timeout=max(self.timeout, 300))
            except requests.RequestException as e:
                raise YaDiskError(f"сеть при загрузке: {e}") from e
        if up.status_code not in (201, 202):
            raise self._err(up, f"загрузка {remote}")

    def publish(self, remote: str) -> str:
        r = self._call("PUT", f"{API}/resources/publish", params={"path": remote})
        if r.status_code != 200:
            raise self._err(r, f"публикация {remote}")
        r = self._call("GET", f"{API}/resources", params={"path": remote, "fields": "public_url"})
        if r.status_code != 200:
            raise self._err(r, f"получение ссылки {remote}")
        return r.json().get("public_url", "")
