@echo off
chcp 65001 >nul
echo 正在停止 CryptoBot...
for /f "tokens=2" %%a in ('tasklist /fi "WINDOWTITLE eq CryptoBot - ETH 雙策略" /fo list ^| findstr "PID"') do (
    taskkill /pid %%a /f >nul 2>&1
)
for /f "tokens=2 delims=," %%a in ('wmic process where "commandline like '%%main_eth.py%%'" get processid /format:csv ^| findstr /r "[0-9]"') do (
    taskkill /pid %%a /f >nul 2>&1
)
echo CryptoBot 已停止
pause
