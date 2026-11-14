@echo off
rem Double-click to open the assistant window without a console.
cd /d "%~dp0"
uv sync --quiet
start "" ".venv\Scripts\pythonw.exe" app.py
