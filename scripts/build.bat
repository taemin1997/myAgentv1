@echo off
rem Builds dist\PersonalAssistant.exe. Run again after changing the code.
rem ui/web (HTML/CSS/JS) is bundled with --add-data; pywebview uses Edge WebView2 on Windows.
rem This file lives in scripts\, so move to the repo root first.
cd /d "%~dp0.."
uv sync --quiet
uv run pyinstaller app.py --name PersonalAssistant --onefile --windowed --collect-data customtkinter --collect-all tkinterdnd2 --collect-all webview --add-data "ui/web;ui/web" --noconfirm --clean
rem The exe reads .env from its own folder, so copy the key next to it.
if exist .env copy /y .env dist\.env >nul
echo.
echo Done: dist\PersonalAssistant.exe