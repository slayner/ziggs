@echo off
cd /d "%~dp0"
chcp 65001 > nul
call "scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8000
