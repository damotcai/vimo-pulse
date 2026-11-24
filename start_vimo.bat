@echo off
chcp 65001 >nul
cd /d "%~dp0"
title VI MO PULSE — Ban tin Kinh te Vi mo Sang

echo ============================================================
echo   VI MO PULSE — Dang khoi dong may chu kinh te vi mo...
echo   Website: http://127.0.0.1:8765
echo ============================================================

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8765"
python server.py --port 8765
pause
