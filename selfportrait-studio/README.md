# Студия автопортрета: камера → обработка → Яндекс Диск

Работает без участия человека: следит за снимками с камеры, прогоняет каждый через цепочку обработки из
`config.yaml` и выкладывает на Яндекс Диск в папку по дате. Всё на бесплатных инструментах:
Python, Pillow, requests, PyYAML, SQLite, REST API Яндекс Диска (и, по желанию, gphoto2).

```
камера ──► inbox/ ──► обработка (Pillow) ──► Яндекс Диск /Автопортрет/2026-10-13/20261013_142501_IMG_0042.jpg
             ▲                                  ▲
   папка или gphoto2             очередь в SQLite: повторы при обрыве интернета, переживает перезагрузку
```

## Надёжность
- **Нет интернета / ошибка Диска** — снимок остаётся в очереди, выгрузка повторяется с паузой до 5 мин, бесконечно.
- **Перезагрузка ПК** — очередь в `data/jobs.sqlite`, ничего не теряется и не выгружается дважды.
- **Недозаписанный файл** — ждём `stable_seconds`, битые файлы после 3 попыток уходят в `data/failed/`.
- **Обрыв камеры** (режим gphoto2) — процесс перезапускается автоматически.
- Оригиналы после выгрузки лежат в `data/archive/`; лог — `data/logs/studio.log` (с ротацией).

## Установка
```bash
cd selfportrait-studio
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

### 1. Токен Яндекс Диска (один раз)
1. https://oauth.yandex.ru/client/new → название любое, платформа «Веб-сервисы», Callback URI
   `https://oauth.yandex.ru/verification_code`, права **Яндекс.Диск REST API → Доступ к папке приложения** или
   **Запись в любое место на Диске** (`cloud_api:disk.write`, `cloud_api:disk.read`).
2. Откройте `https://oauth.yandex.ru/authorize?response_type=token&client_id=<ID_приложения>` под аккаунтом студии
   и скопируйте `access_token`.
3. Сохраните: `export YADISK_TOKEN=...` или в `token.txt` + `yandex_disk.token_file: ./token.txt`.
4. Проверка: `python -m studio --check`.

Если выбрали «папку приложения», поставьте `remote_root: app:/Автопортрет`.
Токен выдаётся на срок до года — поставьте напоминание перевыпустить; при ошибке 401 в логе причина указана.

### 2. Подключение камеры
- **`mode: folder`** (универсально, Windows/Mac/Linux): настройте программу съёмки по USB/Wi-Fi
  (digiCamControl, Canon EOS Utility, Sony Imaging Edge, darktable tether и т.п.) сохранять JPEG в папку `inbox`.
  Вложенные папки (сессии) обрабатываются.
- **`mode: gphoto2`** (Linux/macOS): `sudo apt install gphoto2`, камера в режиме PTP/«Подключение к ПК».
  Программа сама держит `gphoto2 --capture-tethered`: любой снимок по кнопке/пульту сразу скачивается.
  На Linux может мешать автомонтирование (gvfs): `systemctl --user mask gvfs-gphoto2-volume-monitor`.
- Снимайте в **JPEG** (RAW не обрабатывается).

### 3. Алгоритм обработки
Секция `processing.steps` в `config.yaml` — шаги применяются сверху вниз:

| op | параметры |
|---|---|
| `crop_ratio` | `ratio: "4:5"` — центральная обрезка |
| `autocontrast` | `cutoff` (%) |
| `brightness` `contrast` `color` `sharpness` | `factor` (1.0 — без изменений) |
| `grayscale` | — |
| `resize` | `max_side` (только уменьшение) |
| `border` | `size`, `color` |
| `watermark` | `path` (PNG), `position` (`bottom-right`, `top-left`, `center-center`…), `scale`, `margin`, `opacity` |

Автоматически перед шагами применяется поворот по EXIF. Ваш «заранее подготовленный алгоритм» нужно перенести в эти
шаги; если нужно что-то особое (ретушь, замена фона, лицо по центру) — добавляется функция в `studio/processing.py`
в словарь `STEPS`.

### 4. Запуск
```bash
python -m studio --once      # один проход: проверка
python -m studio             # постоянная работа
```
Автозапуск: `deploy/selfportrait.service` (Linux, systemd) или `deploy/windows_autostart.md` (Windows).
На ПК студии отключите сон, автообновление с перезагрузкой в рабочие часы и поставьте автологин/службу.

## Тесты
```bash
pip install -r requirements-dev.txt && pytest
```
