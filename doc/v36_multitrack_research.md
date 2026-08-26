# V36 多軌研究：恢復機制、episode、執行、資料與 Beta sleeve

日期：2026-08-26

狀態：**五題均未產生線上策略升級；`strategy.py`、`executor.py`、`.env` 與 VPS 未修改。**

所有已量化的回測均使用 Binance ETHUSDT 已收盤 1h K、現行 V14+R+V25-D 與
`realistic=True`，資料至 2026-07-30；除 V29 的保證金階梯測試外，其餘策略研究
均採固定 200U 基準。任何只可從未來
開始收集的市場微結構資料，不冒充成歷史 OOS 證據。

## 1. V29 紅燈後事件式恢復

研究腳本 `backtest/research/v36_r1_v29_event_recovery.py` 從現行引擎注入唯一一項
研究 hook：green/yellow/red 依當下 CUSUM 選擇保證金；紅燈仍以低部位交易，僅在
最近 N 筆**已平倉**紅燈後交易的標準化 PnL 合計為正且 PF>1 時重置 CUSUM。
每一筆均按進場時部位重算名目、費用、日/月熔斷與 cooldown。policy=None 的 PnL
與原引擎 parity PASS。

事前固定 9 組：部位階梯 `500/200/100`、`500/300/200`、`300/200/100` ×
恢復窗 N=`20/30/40`。它們全部低於相同 green 保證金的常數部位基準：

| 最佳取捨 | 相對常數 green PnL | MDD | 常數 green MDD |
|---|---:|---:|---:|
| 500/300/200, N20 | -$880 | $2,285 | $2,679 |
| 500/200/100, N20 | -$1,135 | $2,105 | $2,679 |
| 300/200/100, N20 | -$579 | $1,330 | $1,608 |

**結論：REJECTED for automatic deployment。** N20 可用收益換取約 15%~17% 的
絕對回撤下降；N30/40 沒有穩定改善。這是風險偏好降倉，不是 edge 恢復 alpha。
現行 V29 維持警報與人工降倉建議。

## 2. Breakout episode-level 狀態

只在每筆原始進場的 t0，查看同方向且已平倉的前序 TP。診斷「過去 72/168/336h
至少一筆同向 TP」對下一筆 PnL 的條件期望，IS 截至 2024-08-25、OOS 自 2024-08-26。

| 窗口 | IS：episode vs 非 episode 每筆 PnL | OOS：episode vs 非 episode每筆 PnL |
|---|---:|---:|
| 72h | $4.04 vs $5.78 | $33.05 vs $28.25 |
| 168h | $3.45 vs $6.37 | $27.88 vs $30.19 |
| 336h | $9.29 vs $2.34 | $26.56 vs $33.29 |

方向隨窗口與 IS/OOS 翻轉，沒有可預先鎖定的 episode gate。且 V21 對虧損後
延長 cooldown、連敗跳過訊號已做完整序列測試並 REJECTED。

**結論：REJECTED。** 不把已完成 TP 群聚、連敗或 cooldown 改成主策略規則。

## 3. 高 GK 實盤執行品質

V31 已逐筆核對 2026-06-02~2026-07-23 的 23 筆正式盤：進出時間、方向、原因、
持倉及 regime 23/23 一致，實盤/回測 PnL correlation 0.9966；entry 中位不利滑價
0.64bp，排除單一急跌 outlier 後 entry 平均絕對偏差 2.47bp、exit 3.97bp。

唯一 #24 的 entry 偏差為 82.8bp，說明高波動時可能有極端 fill；但本機沒有
`data_live/` 原始逐筆資料，也沒有足夠高 GK 正式盤樣本可分桶。

**結論：未證實高 GK 執行品質是近期錯過行情的原因；樣本不足，不能回測式下結論。**
應新增 signal-close、order-sent、exchange-fill、commission、GK percentile 的逐筆記錄，
累積至少 50 筆後才做 pre-registered GK 分桶檢驗。

## 4. Order book / OI / CVD / options prospective shadow

目前資料流只有 1h OHLCV 與 taker-buy volume。Funding 已於 V30 REJECTED；可免費回補的
OI/LSR/taker 歷史不足以對齊本策略長 OOS，order-book、CVD、liquidation 與 options 資料
更沒有可稽核的長歷史。用今天取得的資料回填 2022/2024 會有時間戳與修訂風險。

**結論：沒有可做的歷史 OOS 回測，故沒有 alpha 結論，也不得部署。**

唯一正確下一步是 prospective shadow：每根 1h 收盤前保存原始 `event_ts`、接收時間、
book depth/spread、OI、CVD、basis/options snapshot，無論是否交易皆記錄。特徵只取
`event_ts <= decision_ts`。預先登記至少 12 個月或 100 個 breakout events 才首次解封，
並以 2/5/10bp 成本壓力、完整交易序列與固定 OOS 檢驗。

## 5. 獨立 ETH Beta sleeve

以 $1,000 起始、每日已實現 Alpha PnL、ETH 現貨日終價重放 2021-03~2026-07；Beta 比例
固定為 0/10/20/30%，Alpha 部位按剩餘資本線性縮放。這是資產配置近似，未重跑保證金
cascade，不能當成部署指令。

| ETH Beta | 終值 | 最大回撤 | 最差 30 日 |
|---:|---:|---:|---:|
| 0% | $11,196 | -51.4% | -44.7% |
| 10% | $10,206 | -45.5% | -40.7% |
| 20% | $9,216 | -45.6% | -37.1% |
| 30% | $8,226 | -48.5% | -34.2% |

同期 ETH buy-and-hold 報酬約 +29.4%、最大回撤 -79.6%。

**結論：不配置為提高期望值的策略升級。** 10% Beta 可略降歷史回撤，但少約 $990 終值；
若使用者的授權目標是「必須參與 ETH 長多」，它可作為獨立風險偏好 sleeve，需另行決定
可承受熊市曝險與資金來源；不能將它算作 Alpha 改善。
