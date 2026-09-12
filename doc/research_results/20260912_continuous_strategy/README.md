# 持續研究：離線驗證與原始重現順序

```powershell
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_continuous_case*.py"
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 backtest/research/report_continuous_strategy_20260912.py"
```

原始首次執行（既有registration/diagnostic/results不可覆寫，不要刪除來重選）：

- continuous_strategy_20260912.py --case continuous_case01_20260912 --phase diagnose；然後--phase evaluate
- continuous_case02_20260912.py --phase diagnose；然後--phase evaluate
- continuous_case03_20260912.py --phase diagnose；然後--phase evaluate
- continuous_case04_20260912.py --phase sample（只小樣本；已DATA LIMITED，不全量下载）
- continuous_case05_20260912.py --phase diagnose；僅READY才--phase evaluate
- continuous_case06_20260912.py --phase diagnose（已INSUFFICIENT SAMPLE，不計收益）
- continuous_case07_20260912.py --phase diagnose（已DATA LIMITED，不計收益）

報告命令核對來源／保護檔、逐案獨立帳本，不重新選規則。基準資料與原研究依賴是本機凍結輸入，fresh clone沒有data時不能重現。逐輪三成本摘要在summary.csv，所有失敗原因和歸因在各results.json。研究仍ACTIVE，後續只可新機制或新資料；不得調參救已拒方向。
