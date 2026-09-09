# 2026-09-09 研究提交索引

本批 9/8–9/9 研究依主題拆成九個 commit，另以文件 commit 維護索引。研究維持 NO PROMOTION；5m 量價空單過濾屬證據不足，沒有實盤部署。

|Commit|研究主題|結果文件|
|---|---|---|
|[c11c550](https://github.com/neilchangtw/cryptoBot/commit/c11c5509e3f7c4bb4e25f36a991c07bafc9a5efc)|新增 MaxHold 進場條件診斷與過濾研究計畫|[報告](../maxhold_entry_diagnostic_20260908.md)|
|[39c2b0e](https://github.com/neilchangtw/cryptoBot/commit/39c2b0ee1bd33966afb18385447fa5515e46f169)|補齊 5m 行情下載工具與來源差異稽核|[報告](../eth_5m_data_audit_20260908.md)|
|[df3f53c](https://github.com/neilchangtw/cryptoBot/commit/df3f53cb0489ab3de7ea880edee82544343be52f)|完成棒內突破接受度比較與完整回測驗證|[報告](../intrahour_entry_results_20260908.md)|
|[a168679](https://github.com/neilchangtw/cryptoBot/commit/a1686799aad5dd376ed226e16ecd6d4ee6bbcaf5)|完成突破失效出場研究與交易差異分析|[報告](../breakout_failure_results_20260908.md)|
|[214b186](https://github.com/neilchangtw/cryptoBot/commit/214b1869e599b0f6e987985d45a1bf8f4bb25ce4)|完成 5m 與 15m 持倉獲利保護研究|[報告](../profit_protection_results_20260909.md)|
|[3815fe6](https://github.com/neilchangtw/cryptoBot/commit/3815fe61889efb136087f10ab8691e690aa95133)|完成執行成本敏感度與實盤成交偏差研究|[報告](../execution_cost_results_20260909.md)|
|[5931d37](https://github.com/neilchangtw/cryptoBot/commit/5931d371ce0f9161ea1add2ce24c1f1cd4f42e7b)|完成固定界線再次突破與再進場研究|[報告](../second_breakout_results_20260909.md)|
|[5b5d18c](https://github.com/neilchangtw/cryptoBot/commit/5b5d18c8ff20ed5da08ab1b04c280b99373e67bc)|完成 ETH 現貨與合約同步突破研究|[報告](../spot_futures_sync_results_20260909.md)|
|[90b7561](https://github.com/neilchangtw/cryptoBot/commit/90b7561e4b6be790e73b8a6a75fe223ec07056c3)|完成 5m 買賣力道與價格反應研究|[報告](../flow_price_results_20260909.md)|

## 驗證及重現邊界

- 本批 83 個研究檔案完成語法／JSON 與常見金鑰格式檢查，52 項相關單元測試通過，各研究的回測 parity／前綴／成本檢查見個別報告。
- Commit 包含研究程式、測試與 fixture、預登記 MD、結果 MD，以及 doc/research_results 下既有摘要。
- 原始 data/ 行情與本機完整交易／淨值輸出仍依 .gitignore 排除；另一台機器要完整重現，需補齊個別報告列出的凍結輸入。
- 較早的 9/4 研究、V37 計畫與本機交易檢視器不屬本批提交。
- 個別研究報告內「尚未 commit／push」是研究完成當時的狀態；本批提交索引記錄其後的 Git 發布整理。
