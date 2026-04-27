@echo off
cd /d "%~dp0"
if exist "python\pythonw.exe" (
    "python\pythonw.exe" launcher.py
) else if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\pythonw.exe" launcher.py
) else (
    pythonw.exe launcher.py
)
