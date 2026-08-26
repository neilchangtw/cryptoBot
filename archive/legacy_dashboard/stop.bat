@echo off
chcp 65001 >nul
echo 正在停止 CryptoBot...

:: 停止儀表板（機器人作為子進程會自動停止）
for /f "tokens=2 delims=," %%a in ('wmic process where "commandline like '%%dashboard\app.py%%'" get processid /format:csv ^| findstr /r "[0-9]"') do (
    taskkill /pid %%a /f >nul 2>&1
)

:: 備用：直接停止機器人（如果子進程未正常結束）
for /f "tokens=2 delims=," %%a in ('wmic process where "commandline like '%%main_eth.py%%'" get processid /format:csv ^| findstr /r "[0-9]"') do (
    taskkill /pid %%a /f >nul 2>&1
)

echo CryptoBot 已停止
pause
