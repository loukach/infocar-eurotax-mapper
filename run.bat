@echo off
echo ============================================================
echo Infocar-Eurotax Mapping Desktop App v4
echo OEM as Scoring Field - Make+Model Candidate Selection
echo ============================================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Check VPN connection
echo Checking VPN connection...
ping -n 1 x-catalogue.motork.io >nul 2>&1
if errorlevel 1 (
    echo WARNING: Cannot reach x-catalogue.motork.io
    echo Please ensure you are connected to the MotorK VPN.
    echo.
)

echo Starting v4 application...
python main.py

pause
