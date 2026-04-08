@echo off
chcp 65001 >nul
title CryptoBot - ETH 雙策略
cd /d "%~dp0"
call .venv\Scripts\activate
python main_eth.py
pause
