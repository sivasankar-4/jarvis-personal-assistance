@echo off
REM ==============================================================================
REM  JARVIS AI PERSONAL ASSISTANT - 1-CLICK WINDOWS SETUP SCRIPT
REM ==============================================================================

setlocal enabledelayedexpansion

cd /d "%~dp0"

echo.
echo ==================================================================
echo           WELCOME TO JARVIS AI ASSISTANT SETUP
echo ==================================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python is NOT installed or not in PATH
    echo.
    echo Please install Python 3.10+ from: https://python.org
    echo.
    echo IMPORTANT: During installation, CHECK the box:
    echo "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo [OK] Python detected successfully!
echo.

REM Create virtual environment if it doesn't exist
echo DEBUG: Checking virtual environment

if exist ".venv\Scripts\python.exe" goto venv_exists

echo [*] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 goto venv_failed

echo [OK] Virtual environment created!
goto venv_done

:venv_exists
echo [OK] Virtual environment already exists!
goto venv_done

:venv_failed
echo [X] Failed to create virtual environment
pause
exit /b 1

:venv_done
echo [*] Upgrading pip in the virtual environment...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [X] Failed to upgrade pip in the virtual environment
    pause
    exit /b 1
)
echo.

echo [*] Installing required packages...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [X] Failed to install required packages from requirements.txt
    echo [!] PyAudio may need a compatible Windows wheel if audio installation failed
    echo [!] For help, visit: https://www.lfd.uci.edu/~gohlke/pythonlibs/
    pause
    exit /b 1
)
echo.

REM Setup complete
echo.
echo ==================================================================
echo [+] JARVIS SETUP COMPLETED SUCCESSFULLY!
echo ==================================================================
echo.
echo NEXT STEPS:
echo.
echo 1. Get your FREE Groq API key:
echo    Visit: https://console.groq.com/keys
echo.
echo 2. To start Jarvis, run these commands:
echo    .venv\Scripts\activate.bat
echo    python jarvis_voice.py
echo.
echo 3. The first time, Jarvis will ask for your Groq API key
echo    (You can skip it and use offline mode)
echo.
pause

REM Start Jarvis
echo.
echo [*] Starting Jarvis AI Assistant...
echo.
".venv\Scripts\python.exe" jarvis_voice.py
