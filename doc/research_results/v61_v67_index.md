# V61～V67 研究索引（2026-11-13～14）

固定條件：研究帳戶起始 2,000U、不補款、逐倉、單一交易累計保證金 ≤300U。門檻：70 個月（UTC 2021-01～2026-10）淨利 ≥10,500U、每年為正、帳戶淨值 MDD ≤1,400U。
全部歷史資料為已曝光的探索資料；V61～V67 沒有任何候選通過，沒有新增前瞻或 shadow。試驗台帳到 T046（`trial_ledger.csv`），下一個編號 V68，接續狀態見 `v57_next.md`。

| 輪次 | 內容 | 結論 | 登記 | 結果 | 程式 |
|---|---|---|---|---|---|
| V61 | 偏斜模組＋低槓桿＋獨立額度（V60 追加探索） | REJECTED | `v61/v61_plan.md` | `v61/v61_results.md` | `v61_run.py` |
| V62 | 選擇權期限結構 | REJECTED（診斷） | 同上第 3 節 | 同上 | `v62_v63_d0.py` |
| V63 | 買賣權成交量比 | REJECTED（診斷） | 同上第 4 節 | 同上 | `v62_v63_d0.py` |
| 補查 | 滑價對帳、模組三筆強平、統計與資金說明 | 原結論不變 | 無 | `v61/v61_audit.md` | `v61_audit.py` |
| 有限帳戶驗收 | 2,000U 完整狀態重播六個版本 | 全數未過 | `v61/finite_account_plan.md` | `v61/finite_account_results.md` | `v61_finite.py` |
| 強平順序稽核 | 現行策略 20 倍 14 筆，分鐘線、標記價、逐筆成交 | 無確定回測錯誤；DATA_LIMITED 保留 | 無 | `v61/liq_audit.md` | `v61_liq_audit.py` |
| V64 | 美股時段日內動能 | REJECTED（診斷） | `v64/v64_plan.md` | `v64/v64_results.md` | `v64_v65_d0.py` |
| V65 | 高成交量日隔日反轉 | REJECTED（診斷，空方未過） | 同上 | 同上 | `v64_v65_d0.py` |
| V66 | 依事前波動同步調整止損與名目（配置） | REJECTED（總淨利下降） | 同上 | 同上 | `v66_volstop.py` |
| V67 | 放量下跌後隔日做多（V65 追加探索） | REJECTED（診斷 G1） | `v67/v67_plan.md` | `v67/v67_results.md` | `v67_d0.py` |

程式都在 `backtest/research/`。資料來源、重建方法與校驗碼見 `v64/DATA_SOURCES.md`、`v64/DATA_SHA256.txt`。分鐘線與逐筆成交的補查原始檔（`data/audit_1m/`、`data/audit_agg/`）不在 git，可由 data.binance.vision 重新下載。
登記檔的 sha256（`*_plan.sha256`）只供版本追蹤：V61～V67 是在研究完成後才一次提交，commit 時間不能證明登記早於結果。
本分支含行情資料，請勿在公司電腦拉取，也不要合併進 master。實盤、VPS、`.env`、既有 shadow 在這幾輪都沒有更動。
