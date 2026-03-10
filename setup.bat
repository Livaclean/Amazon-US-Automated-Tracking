@echo off
title Setup - Amazon FBA Tracking Upload
color 0B
echo ============================================================
echo  One-Time Setup for Amazon FBA Tracking Upload
echo ============================================================
echo.
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo.
    echo Install Python from https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo Then run this setup.bat again.
    pause
    exit /b 1
)

echo [1/4] Installing Python packages...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Package installation failed.
    pause
    exit /b 1
)

echo.
echo [2/4] Installing Playwright Chrome driver...
python -m playwright install chrome
if errorlevel 1 (
    echo ERROR: Playwright install failed.
    pause
    exit /b 1
)

echo.
echo [3/4] Creating folders...
if not exist "input" mkdir input
if not exist "output" mkdir output
if not exist "logs" mkdir logs
if not exist "logs\screenshots" mkdir logs\screenshots

echo.
echo [4/4] Writing your Chrome profile path to config.json...
python -c "
import json, os, pathlib
config_path = 'config.json'
with open(config_path, encoding='utf-8') as f:
    config = json.load(f)
profile_path = os.path.join(os.environ['LOCALAPPDATA'], 'AmazonTrackingChrome')
config['chrome_profile_path'] = profile_path
config['input_folder'] = 'input'
config['output_folder'] = 'output'
config['logs_folder'] = 'logs'
config['us_fc_codes_file'] = 'us_fc_codes.txt'
with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2)
print(f'  Chrome profile will be stored at: {profile_path}')
print('  config.json updated.')
"

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  Next steps:
echo  1. Drop your Excel file into the 'input' folder
echo  2. Double-click run.bat
echo  3. Log in to Amazon Seller Central when the browser opens
echo     (your login is saved for future runs)
echo ============================================================
pause
