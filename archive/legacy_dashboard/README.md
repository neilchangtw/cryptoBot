# 已封存：舊 Windows 儀表板與啟動流程

封存日期：2026-08-12。

本目錄保存已停用、但可供歷史追查的本機儀表板。它**不是**目前的啟動入口，不會被 VPS systemd 載入，也不應在有正式盤服務運行時另外啟動。

## 封存內容

| 原路徑 | 封存路徑 | 用途 |
|---|---|---|
| `dashboard/` | `dashboard/` | FastAPI + PyWebView 儀表板與靜態前端 |
| `start.bat` | `start.bat` | 舊的一鍵啟動儀表板與機器人 |
| `dashboard.bat` | `dashboard.bat` | 與 `start.bat` 相同的舊入口 |
| `stop.bat` | `stop.bat` | 舊的 Windows 停止腳本 |
| `go_live.bat` | `go_live.bat` | 舊的本機正式盤前檢查與儀表板啟動流程 |
| `_go_live_check.py` | `_go_live_check.py` | `go_live.bat` 的互動式前檢 |

## 現行替代方式

- 實盤啟動／重啟：VPS 的 `systemctl restart cryptobot`。
- 績效與交易明細：`.venv/bin/python analyze.py`。
- 即時 gate：`.venv/bin/python check_signal.py`。
- 回測：`.venv/bin/python run_backtest.py`。
- 詳細部署步驟：[deploy/cheatsheet.txt](../../deploy/cheatsheet.txt)。

## 如需還原

先在隔離的本機環境中確認相依套件、`.env`、Paper/Live 模式與單一執行個體，避免和 VPS 或其他交易程序共用 API key／Telegram polling。確認要恢復維護後，再將這一組檔案移回專案根目錄並同步修訂 `AGENTS.md`、`CLAUDE.md` 和部署文件；不可直接以封存版作為實盤入口。
