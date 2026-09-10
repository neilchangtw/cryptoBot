# V37 研究計畫：微結構資料的輕量歷史診斷與 prospective shadow

日期：2026-08-26

狀態：**PLANNED — 尚未執行、未修改 `strategy.py`／`.env`／VPS。**

## 一句話目標

V37 不是把 V35 已拒絕的「高 GK 直接補單」重新包裝成可部署規則；它要證偽或支持一個更窄的問題：

> 在 1h 收盤、原策略已因 GK 壓縮 gate 擋下做多突破的當下，是否存在可即時取得、可重現、且成本合理的「接受度／主動量」資訊，能辨識少量較可能延續的事件？

若答案不夠穩健，結果就是 **REJECTED**，維持 V14+R+V25-D，不再為這個問題細調 OHLCV 參數。

## 已取消的做法

不回補 2021-03～2026-07 的 futures `aggTrades` 全量逐筆成交。單月可達數千萬至近一億筆，成本、時間與本機網路暴露都不符合研究效率；V37 不保留該下載快取或程式。

也不以不完整的歷史 OI、order book、liquidation 或 options 快照假裝做長期 OOS；這些資料沒有可稽核且完整對齊的長歷史時，只能作 prospective shadow。

## 資料與執行邊界

1. 不在公司網路或公司設備下載市場歷史資料；若重啟資料工作，只能在個人環境或既有 VPS 執行。
2. 每個訊號的特徵必須滿足 `event_ts <= decision_ts`；`decision_ts` 為該 1h K 棒收盤時刻。
3. 原始事件、接收時間與衍生特徵分開保存；衍生檔必須可由原始資料重建。
4. V37 研究檔不 import 進 `main_eth.py`、`strategy.py` 或 `executor.py`；沒有明確升級決議前，所有候選均為研究／shadow。

## Phase A：輕量歷史診斷（僅 5m K 線）

### 固定假說

只針對下列事件做研究標記，不改動基準策略：

- 做多 15h close breakout 已成立；
- 其餘現行 session、Path R、cooldown、月上限及風控仍由原引擎判定；
- 唯一被放寬的原因是假設性地越過 L 的 GK 壓縮 gate；
- 信號小時內已完成的 12 根 5m close 中，至少 9 根在已知的前 15h breakout boundary 之上（接受度 `>= 9/12`）。

`9/12` 在第一次執行前鎖定。不得為了結果改成 8、10、11/12，或再加入 RSI、EMA、CLV、成交量等新 family；任何新 family 必須另開版本與新 holdout。

### 無前視對齊

現有 1h 快取的 `datetime` 是 UTC+8 的 K 棒開盤標籤；策略在該 K 棒收盤才做決策。對同一根 1h K：

- 只使用該小時內 12 根已收盤的 5m K；
- breakout boundary 只由前 15 根**已完成** 1h close 計算；
- 不使用下一個小時的 5m K、未完成 K 或後續交易結果；
- Binance 公開月檔以 UTC 切月，讀取時必須按 UTC+8 的 `[月初 08:00, 下月初 08:00)` 精確拼接，不能產生每月前 8 小時的缺口。

### 固定評估流程

1. 基準：現行 V14+R+V25-D、`realistic=True`、固定 200U。
2. 切分：IS `2021-03-05 ~ 2024-08-25`；OOS `2024-08-26 ~ 2026-07-30`。2026-08 不參與選擇。
3. 引擎 parity：overlay 關閉時，進出場時間、方向、出場原因與 PnL 必須與未修改引擎逐筆一致。
4. 成本：固定重跑 0／2／5 bp 滑價，不能只挑 0 bp。
5. 報告：交易數、候選新增筆數／PnL、總 PnL、PF、MDD、月度 PnL、最差 30 日，以及六段 walk-forward 的方向一致性。

### Phase A 判定

僅在以下全部達成時，才可進入 Phase B 的「值得收集」結論；不是部署條件：

- OOS 相對基準的 PnL 改善在 0／2／5 bp 皆為正；
- OOS 新增事件至少 30 筆；樣本不足一律 **INCONCLUSIVE**，不可用少數漂亮交易升級；
- OOS MDD 不增加超過基準的 10%，且六段 walk-forward 至少 4 段相對基準不為負；
- 參數不做事後搜尋，且 baseline parity、資料覆蓋率與邊界稽核全數通過。

任何一項失敗即 **REJECTED**。即使通過，也只代表「接受度值得做 prospective 觀察」，不代表 V35 的高 GK overlay 被推翻。

## Phase B：prospective microstructure shadow

Phase A 若通過，才新增一個獨立、只寫檔不下單的 collector；若未通過，直接停止本題。

### 每根 1h 收盤前／收盤時保存

| 類別 | 原始資料 | 衍生特徵例 | 必存時間欄位 |
|---|---|---|---|
| 交易流 | trades / aggTrades | 5m、15m、1h signed CVD；主動買賣比 | event_ts、received_ts、decision_ts |
| Order book | top-N bids/asks、spread、depth | imbalance、spread、depth shock | snapshot_ts、received_ts、decision_ts |
| Futures | OI、funding、basis | OI 變化、價格/OI 同向性 | event_ts、received_ts、decision_ts |
| Options（若可合法取得） | IV、skew、expiry | IV shock、skew change | event_ts、received_ts、decision_ts |
| 執行品質 | signal、order sent、exchange fill、fee | latency、滑價、partial fill | 各階段 timestamp |

每個 hourly decision event 都要寫入，不論當下有沒有主策略訊號、是否真的下單；否則只留下成交事件會形成 selection bias。

### prospective 規則

- 先鎖定欄位、缺失值處理、資料品質門檻與評估日期，不因中途表現修改。
- 只產生 `shadow_pass` 與假設性交易紀錄，絕不送出額外訂單。
- 收集門檻為至少 **12 個月或 100 個 breakout events**，取較晚者；若資料缺口超過 1%，重置該段有效觀測，不拿缺失時段補推。
- 解封後先保留最近連續 25% 時間作最終 holdout；其餘資料只允許一次預先登記的模型／閾值選擇。

## Phase C：可能的決策

1. **REJECTED（預設）**：Phase A 失敗、樣本不足，或 prospective OOS 不穩定；停止此題。
2. **SHADOW 延長**：資料品質合格但樣本仍不足；維持不下單。
3. **候選升級審查**：僅在 prospective holdout 通過後，才另開版本進行 V31 同等級 ten-gate、參數鄰域、完整交易序列、MDD、2/5/10bp 成本與部署前 parity 稽核。

任何候選都必須與原 Alpha 策略解耦：若使用者真正目標是參與 ETH 大趨勢，應另討論獨立 Beta sleeve，而不是用未證實的高 GK 補單改寫壓縮突破核心。

## V37 產出與不產出

會產出：資料字典、時間對齊稽核、固定假說的歷史診斷表、prospective shadow schema 與 PASS／FAIL 記錄。

不產出：新的線上 GK 門檻、未驗證的補單程式、公司網路上的大量歷史逐筆下載，或任何基於未來資料的「最佳參數」。
