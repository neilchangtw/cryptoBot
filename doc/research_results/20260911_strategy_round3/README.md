# 2026-09-11 第三輪本機重現

結論：D選擇權隱含波動突升「資料受限」，候選收益0次。首次Deribit探測ProxyError／WinError10061，0 bytes；失敗記錄禁止重試。本輪完成至預登記停止線，不包含合格資料下才允許的回測。

在專案根目錄執行以下離線指令，不連網、不重跑前輪、不修改正式策略：

```powershell
.\.venv\Scripts\python.exe -X utf8 backtest/research/strategy_round3_20260911.py --phase audit
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_strategy_round3_20260911.py
```

雜湊一致時直接沿用保存結果，無需重跑測試。需要重建本輪JSON／CSV／報告時才執行：

```powershell
.\.venv\Scripts\python.exe -X utf8 backtest/research/write_strategy_round3_report_20260911.py
```

此重建指令只讀已保存來源停止證據及前輪結果；不取得候選收益、不請求市場資料。`--phase probe`是第一次探測用程式入口，已執行並保存失敗；不列作重現步驟。請保留source_failure.json與source_requests.json，不刪檔重試。取得新增有效資料須另輪登記，不能把此README當繞過限制的授權。

- registration.json：原始計畫、程式、測試、history_index與869項既有保護雜湊。
- registration_sources/、implementation_revision.json：原始程式／測試副本；Windows暫存fixture調整及有效雜湊，均在來源請求前登記。
- initial_hash_audit.json：807項前輪保護、18項有效registration、61項artifact核對。
- sources.json、source_requests.json、source_failure.json、source_status.json：官方欄位、唯一小量請求、錯誤類型、未取得覆蓋及不可重試證據。
- diagnostic_status.json：品質前停止，事件數與收益均未計算。
- history_index_delta.json：以原history_index為父索引的本輪增量，不覆寫舊索引。
- test_results.json：6項新增合成fixture通過，真實來源與策略驗證未執行。
- results.json、summary.csv：基準三成本完整指標、D未計算欄位、損益歸因缺值及所有未完成項目。
- artifact_manifest.json：本輪研究程式、測試、計畫、報告及證據檔SHA256，不含manifest自身。

依賴前輪manifest列出的本機檔案；data/受gitignore管理，fresh clone不保證具備原始輸入。原始基準驗證按雜湊沿用，若不一致，audit將失敗；不得直接重跑舊README整輪命令修補。
