

@echo off
cd /d "%~dp0"
title Jarvis AI Assistant

echo Starting Jarvis...

".venv\Scripts\python.exe" jarvis_voice.py

echo.
echo Jarvis has stopped. Press any key to close.
pause >nul
