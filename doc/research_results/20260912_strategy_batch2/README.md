# 本批離線核對與重現

PowerShell在專案根目錄執行；單一前景程序、不連網：

```powershell
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_strategy_batch2*.py"
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 backtest/research/report_strategy_batch2_20260912.py"
```

報告命令只核對原有來源／產物並重建本批報告，不回測候選。候選在資料／事件門檻停止，收益皆未計算。

首次執行次序（既存registration／diagnostic不可覆寫，勿重新執行來變更當初時間）：

1. strategy_batch2_20260912.py --phase diagnose-j
2. strategy_batch2_round2_20260912.py
3. strategy_batch2_round3_20260912.py
4. report_strategy_batch2_20260912.py

可直接呼叫lead_features或extension_events，對凍結輸入重算並與本批CSV比對；不得改公式、閾值或切分救結果。summary.csv與results.json彙整全部狀態；verification.json有實際測試命令與結果；history_index_delta.json供下一輪定向查重；artifact_manifest.json含全部本批產物SHA256（不包含自身）。

預登記在doc/strategy_batch2_round1_20260912.md、round2、round3；共同契約在round1。基準及原始資料維持本機原路徑，data被gitignore，fresh clone不等於具備全部凍結輸入。J暫計88／34群沒有通過可用時點認證，不能拿此計數宣稱可回測。
