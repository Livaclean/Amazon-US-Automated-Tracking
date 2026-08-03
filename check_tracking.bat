@echo off
title Amazon FBA Check Tracking
color 0A
echo ============================================================
echo  Amazon FBA Check Tracking
echo ============================================================
echo.
echo  Checks each shipment's carrier tracking page (UPS/FedEx/DHL)
echo  and records label-created / delivery dates and status in
echo  logs\tracking_status.xlsx. Does NOT touch Amazon or upload
echo  anything.
echo.
echo  IMPORTANT: Close Google Chrome before continuing.
echo  (This tool opens Chrome automatically with your saved login.)
echo.
pause

cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not installed. Run setup.bat first.
    pause
    exit /b 1
)

python run.py --check-tracking
echo.
pause
