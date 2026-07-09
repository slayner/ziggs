@echo off
start "bot"     cmd /k "cd /d "%~dp0bot-v2" && venv\Scripts\python.exe -u main.py"
