@echo off
chcp 65001 >nul
title CryptoBot Dashboard
cd /d "%~dp0"
call .venv\Scripts\activate
python dashboard\app.py
pause
