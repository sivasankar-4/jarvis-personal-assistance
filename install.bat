@echo off
REM ==============================================================================
REM  JARVIS AI PERSONAL ASSISTANT - 1-CLICK WINDOWS SETUP SCRIPT
REM ==============================================================================

setlocal enabledelayedexpansion

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
echo.REM Install additional audio packages
echo [*] Installing audio packages (pyttsx3, pyaudio)...
pip install pyttsx3
pip install pyaudio 2>nul
if errorlevel 1 (
    echo [!] Note: pyaudio may need manual installation on Windows
    echo [!] If audio doesn't work, visit: https://www.lfd.uci.edu/~gohlke/pythonlibs/
    echo [!] Download pyaudio wheel and run: pip install [filename].whl
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
python jarvis_voice.py
