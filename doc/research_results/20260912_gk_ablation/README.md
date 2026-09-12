# 2026-09-12 離線研究重現

F完整移除GK淘汰（206／74群）；E取消小時限制樣本不足（21／7群，收益0次）。只新增本機研究；不連網、不改正式策略或既有資料。以專案根目錄PowerShell執行。

優先核對保存結果與重建報告，不重跑策略：

```powershell
.\.venv\Scripts\python.exe -X utf8 backtest/research/report_session_ablation_20260912.py
```

必要fixture（F包含E三項原測試，合計5項不同測試）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_gk_ablation_20260912.py
```

只有需要重新產生F結果時，才依序執行以下指令；不會重跑9/10或9/11候選：

```powershell
.\.venv\Scripts\python.exe -X utf8 backtest/research/gk_ablation_20260912.py --phase diagnose
.\.venv\Scripts\python.exe -X utf8 backtest/research/gk_ablation_20260912.py --phase evaluate
.\.venv\Scripts\python.exe -X utf8 backtest/research/report_session_ablation_20260912.py
```

E樣本不足，禁止執行E evaluate。保存E的diagnostic_status／noop作F登記依賴，勿重跑E診斷覆寫原事件登記時間；也不要重跑舊README整輪命令。

- E registration／implementation_revision／implementation_revision_02及registration_sources：初登記、CSV NA解析修正、零已平倉前綴修正，原始來源保留。
- F registration／implementation_revision／registration_sources：F原登記及前綴修正；修正時1主候選、3成本已計算，完整CSV31項雜湊未變。
- diagnostic_status、direct_opportunities_pre_pnl.csv：收益前原狀態事件，無候選未來損益。
- results、summary、noop、independent_output_audit、verification、new_trade_diagnostics、history_index_delta：全部結果、成本／風險、歸因、必要驗證、範圍與試驗數。
- data/gk_ablation_20260912：6情境完整逐筆、每bar狀態、gate、funding、mark淨值、3成本配對及新增單分類。
- data/session_ablation_20260912：E直接機會與三成本基準no-op輸出，沒有E候選交易。
- artifact_manifest：本次程式、測試、计划、報告、JSON及CSV SHA256；不含manifest自身。

需要既有凍結candles、funding/mark、第二/三輪保存資料與登記的研究程式。data受gitignore管理，fresh clone不保證可離線重現。以有效修訂核對自身程式；第一次registration不覆寫。若保護雜湊不一致應停止定位，不用重新跑舊策略掩蓋漂移。
