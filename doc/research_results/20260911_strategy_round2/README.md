# 2026-09-11 第二輪重現

結論：C現貨先行主動量淘汰。0bp淨收益$5,344.22，原策略$7,926.70；事件93／31群充足。A/B未重啟。

在專案根目錄以PowerShell執行（不連網、不重跑舊研究）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_strategy_round2_20260911.py
.\.venv\Scripts\python.exe -X utf8 backtest/research/strategy_round2_20260911.py --phase diagnose
.\.venv\Scripts\python.exe -X utf8 backtest/research/strategy_round2_20260911.py --phase evaluate
.\.venv\Scripts\python.exe -X utf8 backtest/research/audit_report_strategy_round2_20260911.py
```

只想檢查已保存結果並重建報告時，執行最後一條即可；不重跑任何候選。

需要本機已存在的原始輸入：data/maxhold_review_20260908/candles.csv、data/public_cost_history_20260908全部manifest列出的檔案、data/spot_futures_sync_20260909的manifest／CSV／18個raw JSON、data/strategy_study_20260911/base_{0,2,5}bp_{trades,equity}.csv、前輪registration列出的程式／文件。data被gitignore，fresh clone不能保證離線重現。

- registration.json：第一次收益前公式／程式／輸入／807項保護雜湊。implementation_revision.json：僅修正截斷資料空時段摘要，C收益計算前記錄；原登記不覆寫。
- initial_hash_audit.json：先前13項registration核對與已讀JSON雜湊。
- sources.json：API欄位、原JSON逐檔雜湊、重建／Decimal核對、接收與修訂限制；本輪沒有發API請求。
- pre_pnl_counts.json、diagnostic_status.json：收益前品質及108個直接事件、93／31群。
- results.json、summary.csv：三成本基準與主候選、兩個0bp對照、分期／完整風險指標／歸因／no-op／前綴／期末倉位及停止狀態。
- independent_output_audit.json：不使用回測引擎重新計算收益，從保存逐筆與funding重建8條淨值並核對6個截點資產。
- data/strategy_round2_20260911/：完整特徵、原事件、8情境逐筆／mark淨值／funding／gate／每bar策略狀態、3成本配對歸因。所有全期情境末倉為空；真實前綴測試保留L/S未平倉倉位。
- artifact_manifest.json：新程式、測試、報告、JSON／CSV的交付雜湊與大小；產生manifest時不包含它自己。

重跑僅覆寫第二輪自己產出的結果；舊輸入、前輪資料、原registration及事件第一次記錄時間不覆寫。不得把本檔重現命令理解成允許修改正式策略、下載新資料或部署。
