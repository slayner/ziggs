@echo off
cd /d "%~dp0"
chcp 65001 > nul
call "venv\Scripts\python.exe" scripts\companion_dashboard.py