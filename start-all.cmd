@echo off
start "run-api"      cmd /k "call "%~dp0backend\run-api.cmd""
start "run-dashboard" cmd /k "call "%~dp0backend\run-dashboard.cmd""
start "run-dev"      cmd /k "call "%~dp0frontend\run-dev.cmd""
start "bot"          cmd /k "cd /d "%~dp0bot-v2" && venv\Scripts\python.exe -u main.py"
