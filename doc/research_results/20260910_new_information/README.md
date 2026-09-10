# 2026-09-10 新資訊研究重現

結論：NO PROMOTION；六個主候選全部REJECTED。完整說明見[研究報告](../../new_information_results_20260910.md)，固定定義見[預登記](../../new_information_plan_20260910.md)。

## 執行

在專案根目錄，以既有.venv執行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_new_information_research.py -v
.\.venv\Scripts\python.exe backtest/research/fetch_new_information_20260910.py probe
.\.venv\Scripts\python.exe backtest/research/fetch_new_information_20260910.py metrics
.\.venv\Scripts\python.exe backtest/research/fetch_new_information_20260910.py premium
.\.venv\Scripts\python.exe backtest/research/new_information_20260910.py oi
.\.venv\Scripts\python.exe backtest/research/new_information_20260910.py premium
.\.venv\Scripts\python.exe backtest/research/audit_new_information_20260910.py oi
.\.venv\Scripts\python.exe backtest/research/audit_new_information_20260910.py premium
.\.venv\Scripts\python.exe backtest/research/write_new_information_report_20260910.py
```

下載遵守原始使用者授權與平台權限；不繞過代理、不重試ProxyError，不同環境需正常網路權限。共764個小ZIP及checksum，9.05MB；hard ceiling50MB。已有快取驗SHA256後跳過。來源可能事後修訂，新下載版本不能直接當成本輪原樣重現；保留原raw檔與manifest。

必要本機輸入：

- data/maxhold_review_20260908/candles.csv
- data/public_cost_history_20260908/{mark_1h_full.csv,funding_full.csv,manifest.json}
- data/optimization_execution_20260908/base_{flat,hist}_{0,2,5}_trades.csv（逐筆parity參考）
- data/new_information_20260910/raw/、download_manifest.json（本輪公開來源）

單靠fresh clone不保證完整重現，缺少凍結來源時會拒絕執行；不以合成資料取代。7個純fixture測試不需歷史下載。資料輸出保留所有來源窗口，feature函数只按決策時間讀取。

## 檔案

results.json包含兩家族完整前/後/近期、L/S、成本、風險、品質對照、配對、7/30日bootstrap與六主候選Holm校正。summary.csv有66個唯一完整情境；兩家族合計保存72份，含重複baseline。

每主候選另有matched_random.csv（100次完整狀態負對照），original_affected.csv（直接原事件與24h群），monthly_delta.csv。更大的每情境trade/equity/funding/events及paired全保留data/new_information_20260910。download_manifest和source audit列checksum、原始網址、欄位、日期、缺口與延遲限制。

history_index.json是V9～V37與最近結果的101份文件索引，不是宣稱全數重跑。初始registration不回寫；最終執行與交付檔hash見artifact_manifest.json。

未執行項目：因基本經濟條件失敗而停止10bp/鄰域/進一步延遲/WF/真未見資料驗證。不改策略、不部署、不push。
