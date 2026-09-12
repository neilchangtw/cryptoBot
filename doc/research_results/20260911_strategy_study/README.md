# 2026-09-11 可重現研究

結果：NO PROMOTION。A資料受限且後期10群；B後期14群、樣本不足。兩者均在候選收益計算前停止。

在專案根目錄以PowerShell執行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_strategy_study_20260911.py
.\.venv\Scripts\python.exe backtest/research/strategy_study_20260911.py
.\.venv\Scripts\python.exe backtest/research/audit_strategy_study_20260911.py
.\.venv\Scripts\python.exe backtest/research/write_strategy_study_report_20260911.py
```

需要本機既有：data/maxhold_review_20260908/candles.csv、data/public_cost_history_20260908/、data/optimization_execution_20260908/base_flat_{0,2,5}_trades.csv、data/new_information_20260910/（全部原ZIP、checksum、download_manifest及oi_source_merged.csv）。data被gitignore，不代表fresh clone能離線重現。重跑會重建本輪自己的研究輸出，最多重做兩個匿名3點API核對，不重抓歷史行情。

- registration.json：收益前鎖定時間、Git版本、計畫／程式／輸入雜湊、既有索引差異；後續同條件重跑另存implementation_run.json。
- sources.json：官方定義與匿名抽樣結果；本次ProxyError，0 bytes、不重試。
- pre_pnl_counts.json：候選收益前的事件數與品質。
- results.json／summary.csv：基準三成本完整指標、分期、parity及停止狀態。
- independent_audit.json：Decimal、當前strategy純函式、事件數重查。
- data/strategy_study_20260911/：三組基準逐筆、mark淨值、funding帳本、gate、bar2觀測、全特徵及B直接原事件。沒有候選逐筆檔，因未進行候選回測。

收盤t+1h為決策／成交模型時間；metrics q=決策−15min，假設q+10min可用。歷史received_ts及修訂狀態未知；此假設不是實盤可用性證明。原版收盤價成交及SafeNet穿透只作基準模型，不是已證實零延遲成交。
