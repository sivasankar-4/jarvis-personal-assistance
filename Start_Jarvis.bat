

@echo off
cd /d "%~dp0"
title Jarvis AI Assistant

echo Starting Jarvis...

".venv\Scripts\python.exe" -c "import pyttsx3; e=pyttsx3.init(); e.say('Hi boss, welcome back. Jarvis is online and ready to assist you.'); e.runAndWait()"

".venv\Scripts\python.exe" jarvis_voice.py

echo.
echo Jarvis has stopped. Press any key to close.
pause >nul
