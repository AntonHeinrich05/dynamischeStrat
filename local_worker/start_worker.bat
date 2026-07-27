@echo off
REM Lokalen Worker starten (Windows). Beim ersten Start Server-URL + Token angeben:
REM   worker.py --server https://DEINE-WEBSITE --token DEIN_TOKEN
cd /d "%~dp0"
python worker.py %*
pause
