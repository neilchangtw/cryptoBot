# 第三批離線核對／重現

在專案根目錄PowerShell執行，單一前景程序，不連網：

```powershell
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_strategy_batch3*.py"
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 backtest/research/report_strategy_batch3_20260912.py"
```

報告只讀saved ledgers，獨立核對並重建本批報告，不重跑候選。results.json／summary.csv列結果；registration與artifact_manifest列來源／產物hash；verification記錄驗證結果；history_index_delta供下批定向查重。

首次執行次序（registration／diagnostic／results不可覆寫，勿刪除重選）：

1. strategy_batch3_20260912.py --phase diagnose-m
2. strategy_batch3_20260912.py --phase evaluate-m
3. strategy_batch3_round2_20260912.py
4. strategy_batch3_mark_compat_20260912.py（保留原round3原始程式，使用補充登記的timestamp適配）
5. report_strategy_batch3_20260912.py

可呼叫Study.run('M', slip, save=False)離線重算已登記的0/2/5bp並比对saved ledger；不改公式或新增掃描。M已經濟淘汰，不執行鄰域；N/O前置門檻未過，不實作收益回放。

依賴本機凍結data、舊研究引擎／帳本及本批全部檔案；data被gitignore，fresh clone不等於有輸入。所有Python經.venv activation。若驗證到來源hash漂移應先定位，不能拿原數字冒充新基準。
