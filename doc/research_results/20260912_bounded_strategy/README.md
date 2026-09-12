# 離線核對與重現

目前結果：G 44／14群、H 6／3群（樣本不足）；I缺可稽核執行資料（資料受限）。候選收益0次。不得執行候選收益或放寬門檻。

PowerShell於專案根目錄執行，全部單一前景程序：

```powershell
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 -m unittest discover -s tests -p test_bounded*.py"
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 backtest/research/report_bounded_strategy_20260912.py"
```

以上只核對並重建本輪報告，不重算候選或舊研究。若需重算本輪基準no-op（通常無需）：

```powershell
cmd /c "call .venv\Scripts\activate.bat && python -B -X utf8 backtest/research/bounded_strategy_20260912.py --phase baseline"
```

首次研究原始次序為baseline、bounded_strategy --phase diagnose-g、bounded_round2、bounded_round3、report。收益前的registration與diagnostic採不可覆寫；不要刪除它們或原事件檔來重新選規則。如需重核G/H事件，可在Python中呼叫direct_tp_events／direct並與保存events_pre_pnl.csv逐欄比對，不必生成另一輪。

完整本機凍結data、前輪引擎／修訂和帳本是必要依賴；data被gitignore，fresh clone不代表可重現。registration記錄有效來源雜湊、原HEAD及原未提交狀態；verification含測試命令與獨立帳本驗證。artifact_manifest記錄本輪全部程式、測試、預登記、結果及baseline帳本雜湊（不含manifest自身）。
