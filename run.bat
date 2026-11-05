@echo off
rem MySQL Backup Viewer - launcher (ASCII only: Persian text in .bat files
rem gets mangled by the cmd codepage and breaks commands - keep this ASCII)
setlocal
cd /d "%~dp0"
title MySQL Backup Viewer - Flask
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
  echo [!] Python was not found. Install it from: https://www.python.org/downloads/
  echo     IMPORTANT: check "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)

python -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Installing Flask ...
  python -m pip install -r requirements.txt
)

echo.
echo   ============================================
echo     MySQL Backup Viewer - Flask
echo     Open in your browser:  http://127.0.0.1:5000
echo     Keep this window open while using the app.
echo   ============================================
echo.
python app.py %*
pause
