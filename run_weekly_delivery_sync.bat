@echo off
title Amazon FBA Weekly Delivery Window Sync
color 0A
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not installed. Run setup.bat first. > logs\weekly_delivery_sync_launch_error.txt
    exit /b 1
)

python run.py --weekly-delivery-sync
