@echo off
rem Double-click to open the assistant window without a console.
rem This file lives in scripts\, so move to the repo root first.
cd /d "%~dp0.."
uv sync --quiet
start "" ".venv\Scripts\pythonw.exe" app.py