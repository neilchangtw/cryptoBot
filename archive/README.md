# 本機封存區

此目錄用來說明不屬於目前策略、但可能需要人工回溯的舊產物。

- `local_backtests/` 已被 Git 忽略：只存本機回測輸出，避免把一次性明細誤納入提交。
- 這裡的檔案不是程式依賴、不是實盤資料來源，也不能取代 `doc/` 內的正式研究結論。
- 需要重現時，以當前 `run_backtest.py`、當時的資料快取與明確參數重新執行；不要直接將歷史輸出當成目前策略績效。

## `legacy_dashboard/`

已封存、不參與現行執行的 Windows 儀表板與啟動流程。內容包含 FastAPI／PyWebView 儀表板、其前端資源、`start.bat`、`dashboard.bat`、`stop.bat`、`go_live.bat` 和 `_go_live_check.py`。現行實盤一律由 VPS systemd 啟動，日常診斷以 Telegram、`analyze.py`、`check_signal.py` 與 `run_backtest.py` 進行。

詳細還原條件與檔案清單見 [legacy_dashboard/README.md](legacy_dashboard/README.md)。

## `local_backtests/2026-07_margin_comparison/`

2026-07-30 產生的 V14+R + V25-D 回測明細，用於比較全程 500U 與 700U 保證金，均採貼近實盤成交、0bp 滑價。它們是保證金線性縮放的歷史快照，沒有被目前程式、部署或研究文件引用。

| 檔案 | 範圍 | 保證金 | SHA256 |
|---|---|---:|---|
| `backtest_500U_2026-07_detail.txt` | 2026-07 | 500U | `6A7A7B926858C3304B49B05421200F5EBE114E9256CAEE88D17E76C19CB83DBA` |
| `backtest_500U_full_detail.txt` | 2024-07-30～2026-07-30 | 500U | `C2CB32AAA494E52F6637884B36B3BCC619C15B1ADDDBA81EF81DEEFE3CD932B5` |
| `backtest_700U_2026-07_detail.txt` | 2026-07 | 700U | `CA5A55A389AC23EAEEFD227942A736E87E2096C0EA85DCA8CC5D818101B1CBAF` |
| `backtest_700U_full_detail.txt` | 2024-07-30～2026-07-30 | 700U | `84A4080028A809A9D19D4E39D623AAD63412122BC79E8F63426108B6D6D9CE62` |

如未來需要正式納入研究，應將結論與可重跑的參數寫入 `doc/`／研究腳本，而非直接提交這些舊輸出。
