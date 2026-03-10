@echo off
title Amazon FBA Tracking Upload
color 0A
echo ============================================================
echo  Amazon FBA Tracking Number Uploader
echo ============================================================
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

python run.py
echo.
pause
