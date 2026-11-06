@echo off
cd /d "%~dp0"
start "" "D:\python\pythonw.exe" "dashboard.py" --port 8765
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
exit /b 0
