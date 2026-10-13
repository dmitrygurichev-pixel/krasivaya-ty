@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul || (echo Установите Python 3.11+ с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^) и запустите снова & pause & exit /b 1)
if not exist .venv (
  echo Первый запуск: устанавливаю компоненты, подождите 1-2 минуты...
  py -3 -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install -q -r requirements.txt || (pause & exit /b 1)
)
start "" .venv\Scripts\pythonw.exe run_gui.py
