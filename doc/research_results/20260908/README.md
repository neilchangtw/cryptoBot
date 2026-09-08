# 2026-09-08 策略優化研究彙總

決議：**NO PROMOTION**，現行V14+R+V25-D維持。完整解讀見[研究報告](../../optimization_execution_20260908.md)。

## 已提交的證據

- `summary.csv`：15組規則的固定200U、額外0bp比較，包含funding後PnL、mark淨值回撤、MH、SN與判定。
- `results.json`：全部90組成本／保證金測試、向前驗證選擇、區塊bootstrap、10bp壓測、候選定義、來源雜湊及研究當時Git HEAD。
- `download_manifest.json`：逐年公開funding／mark-price下載時間、筆數、大小與檔案雜湊。

上述是研究執行當時的快照；報告／規劃文件後續更新狀態時，其檔案SHA256會改變，不回寫歷史結果。Git提交不代表實盤部署。

## 本輪主要結果

固定200U／20x、包含公開funding估算、額外0bp：基準PnL +7,926.70、mark淨值MDD368.53、MH75。S/DOWN TP3%為+8,443.64、MDD368.53、MH76，但只有29筆變動，7日區塊95%增量區間約−249～+1,352，判INCONCLUSIVE。

MH延長在早期退化；成交量、ADX、回測確認與單純延遲未通過收益或風險門檻。兩個參數家族未同時合格，故依計畫跳過組合。

## 重現

專案根目錄，使用`.venv`：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_optimization_research.py -v
.\.venv\Scripts\python.exe backtest/research/execute_optimization_plan_20260908.py
.\.venv\Scripts\python.exe backtest/research/write_optimization_report_20260908.py
```

完整研究及其中一個整合測例需要本機固定行情，純邏輯測例不需連網。必要輸入如下，均未提交：

- `data/maxhold_review_20260908/candles.csv`：已凍結17,519根ETHUSDT 1h成交K線，SHA256見results及download manifest。
- `data/public_cost_history_20260908/funding_full.csv`、`mark_1h_full.csv`與`manifest.json`。

公開成本行情可按順序執行`fetch_public_cost_years.py year1`、`year2`、`merge`取得，請先確認執行環境與網路適合下載；合併檢查還使用原本`data/ETHUSDT_funding.csv`的重疊費率。本次沒有提供假資料替代缺少的歷史輸入，單靠fresh clone不能宣稱完整重現通過。

`maxhold_review_20260908.py`及`phase1_cost_risk_20260908.py`是附件稽核工具，另需使用者提供的回測／實戰文字明細，目前保留原工作機的SOURCE設定。這些私人附件不提交。最終候選研究`execute_optimization_plan_20260908.py`不讀私人附件、不呼叫交易API。

`price_candle_trade_analysis.py`與`dynamic_tp_regime_analysis.py`是本輪依賴的既有研究工具，一併提交；交易檢視器與V37尚未執行的計畫不在本輪提交範圍。

驗證：7個獨立測例、6組原引擎一致性、45個候選前綴、6個WF前綴通過。未將`git diff --check`或語法編譯當作策略有效性的證據。
