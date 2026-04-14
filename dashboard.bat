@echo off
chcp 65001 >nul
title CryptoBot V13 - 印鈔機監控台
cd /d "%~dp0"
call .venv\Scripts\activate
python dashboard\app.py
pause
