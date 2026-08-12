# 現行實盤資料與策略規格

> 最後同步：2026-08-12。程式真實來源為 `strategy.py`、`executor.py`、`recorder.py` 與 `paths.py`；本文件是給維運與分析使用的現行規格摘要。舊版 V5/V10 的 OR 進場、100U 保證金與多個 S 子策略已退役，不可再據此判讀實盤紀錄。

## 1. 運行範圍與資金

| 項目 | 現行設定 |
|---|---|
| 商品／時框 | Binance Futures `ETHUSDT` 永續合約、已收盤 1h K 棒 |
| 模式 | Hedge Mode；Long／Short 各最多一筆，合計最多兩筆 |
| 線上策略 | **V14+R + V25-D** |
| 部位大小 | `.env` 的 `MARGIN_PER_TRADE × LEVERAGE`；程式預設為 200U × 20x。實際 VPS 數值必須以執行中的 `.env` 為準，不可由本機文件或回測推定。 |
| 動態美元風控 | Fee、日虧與各方向月虧上限都以 200U 為基準，乘以 `MARGIN_PER_TRADE / 200`；調整保證金無須改程式 |
| 資料目錄 | Paper 用 `data/`，Live 用 `data_live/`；多實例時由 `INSTANCE_DIR` 隔離資料、日誌與狀態 |

回測的預設保證金排程是歷史還原用途：200U → 300U（2026-07-03）→ 500U（2026-08-01）。`run_backtest.py --flat` 才是固定 200U 的研究比較單位；不可把這兩種單位混在一起比較。

## 2. 共用指標與訊號時點

### Garman-Klass 壓縮

每根已收盤 1h K 棒先計算：

```text
gk_t = 0.5 × ln(H_t / L_t)^2 − (2 ln 2 − 1) × ln(C_t / O_t)^2
```

接著產生兩組 ratio：

```text
ratio_L = mean(gk, 5)  / mean(gk, 20)
ratio_S = mean(gk, 10) / mean(gk, 30)
```

`gk_pctile`／`gk_pctile_s` 是 **前一根** ratio 在其前 100 根歷史值中的嚴格 percentile：

```text
pctile_t = percentile_rank(ratio_{t-1} within historical 100 ratios)
```

因此第 t 根判斷進場時，不會把 t 根正在計算的 ratio 放進自己的 percentile 分布。完整 shift A/B 成交時點比較見 [v34_dialogue_research.md](v34_dialogue_research.md)。

### 突破與 R regime

- Long 突破：`close_t > max(close_{t-1} ... close_{t-15})`。
- Short 突破：`close_t < min(close_{t-1} ... close_{t-15})`。
- R regime 使用前一根的 SMA200 及其 100 根斜率：`sma_slope = SMA200_{t-1} / SMA200_{t-101} - 1`。
  - `UP`：`sma_slope > +4.5%`
  - `SIDE`：`|sma_slope| < 1.0%`
  - `DOWN`：`sma_slope < -1.0%`
  - 其餘為 `MILD_UP`／`MILD_DOWN`

暖機至少 310 根；不足時不產生可交易訊號。

## 3. Long（L）策略

### 進場 gate（必須全部通過）

1. `gk_pctile < 25`（L 5/20 ratio、100 根 percentile）。
2. 收盤價突破前 15 根收盤最高價。
3. UTC+8 時段不是 `{0, 1, 2, 12}`，且不是週六或週日。
4. 不是強多頭：`sma_slope ≤ +4.5%`。這是 V23 的 R gate。
5. L 無既有倉位、通過 L 出場 cooldown（6 bars）、當月進場未滿 20 筆，且未命中帳戶風控。

### 出場順序

1. **SafeNet**：低點觸及入場價 -3.5%。
2. **TP**：預設 +3.5%；若入場 regime 是 `DOWN`，V25-D 改為 +4.0%。
3. **MFE-trail**：持倉的最高浮盈曾達 +1.0%，而現收盤相對該高點回吐 ≥0.8%，持倉至少 1 bar。
4. **延長期**：到 MaxHold 時若收盤仍為正收益，延長 2 bars；延長期間跌回入場價以 **BE** 出場，否則期末以 **MH-ext** 收盤出場。
5. **Conditional MaxHold**：第 2 根持倉 bar 的收盤報酬 ≤ -1.0% 時，MaxHold 由 6 縮為 5；否則預設 6。若入場 regime 是 `MILD_UP`，V25-D 將滿持倉改為 7（但 Conditional 規則仍優先）。負收益到期則以 **MaxHold** 收盤出場。

## 4. Short（S）策略

### 進場 gate（必須全部通過）

1. `gk_pctile_s < 35`（S 10/30 ratio、100 根 percentile）。
2. 收盤價跌破前 15 根收盤最低價。
3. UTC+8 時段不是 `{0, 1, 2, 12}`，且不是週一、週六或週日。
4. 不是盤整：`|sma_slope| ≥ 1.0%`。這是 V23 的 R gate。
5. S 無既有倉位、通過 S 出場 cooldown（8 bars）、當月進場未滿 20 筆，且未命中帳戶風控。

### 出場順序

1. **SafeNet**：高點觸及入場價 +4.0%。
2. **TP**：低點觸及入場價 -2.0%。
3. **延長期**：MaxHold 時若收盤正收益，延長 2 bars；期間回到入場價以 **BE** 出場，期末為 **MH-ext**。
4. **MaxHold**：預設 10 bars；若入場 regime 是 `UP`，V25-D 改為 8 bars。負收益到期以收盤出場。

`SIDE < 1.3%` 不是目前設定；它是 V34 的 shadow candidate，尚未通過完整升級稽核，程式仍使用 `< 1.0%`。

## 5. 帳戶熔斷與 V29 健康度

| 規則（200U 基準） | 現行行為 |
|---|---|
| 日虧 -$200 | L+S 合計停止進場至日切換 |
| L 月虧 -$75／S 月虧 -$150 | 對應方向停單至月切換 |
| 連虧 4 筆 | 24 bars 冷卻 |
| V29 健康度 🟡 ≤25% | 通知「凍結加碼」；**不自動封鎖進場** |
| V29 健康度 🔴 0% | 通知「退回 200U」；**不自動改保證金或停單** |

V29 CUSUM 在每次**出場**後更新，故是 edge 衰退的人工決策警報，不是逐根 K 的訊號 gate。將黃燈／紅燈寫成自動 200U／100U 或完全停單，需要獨立回測、shadow log 與明確部署授權；目前未實作。

## 6. 實盤流程與成交語意

1. 主循環於每小時整點後取得已收盤 K 棒，先更新指標與既有倉位，再評估新進場。
2. `strategy.py` 只計算資料、gate 與出場判斷；`executor.py` 負責 Hedge Mode 部位、交易所下單、風控、持久化與通知。
3. 實盤進出場價格以 Binance 實際成交結果為準。`run_backtest.py` 預設採「貼近實盤」的 TP／BE 市價收盤成交；`--ideal` 是理論價對照，`--slip` 用於滑價壓力測試。
4. L/S 可同時各持一筆；同方向不會因另一方向持倉而解除自己的 gate。

## 7. 資料檔與欄位責任

| 檔案 | 寫入者 | 用途與重要欄位 |
|---|---|---|
| `trades.csv` | `recorder.py` | 每筆完整交易：方向、入出場時間／價格、數量、保證金、名目、PnL、報酬、出場原因、MAE/MFE、entry regime、持倉 bars。`breakout_10bar_max/min` 是歷史欄名，現行寫入的是 **15-bar** 值。 |
| `bar_snapshots.csv` | `recorder.py` | 每根 K 的指標、L/S gate、`gk_pctile`、突破值、session、SMA slope／regime、倉位與帳戶快照；用於解釋「為何沒開單」。 |
| `position_lifecycle.csv` | `recorder.py` | 每個持倉、每根持倉 bar 的價位、未實現 PnL、MAE/MFE、running MFE、MaxHold／延長狀態。 |
| `daily_summary.csv` | `recorder.py` | 每日交易數、PnL、勝率、SafeNet／TP／MaxHold 等彙總。 |
| `eth_state_live.json`（或多實例 state） | `executor.py` | Live 持倉、冷卻、月度／日度計數、V29 健康狀態；不可手動修改。 |
| `logs/system.log`、`signal.log`、`alerts.log` | 主程式 logging | 系統心跳、gate／進出場、告警；均採輪替。 |

Live 模式讀寫 `data_live/`；Paper 模式讀寫 `data/`。若啟用多實例，每個 `INSTANCE_DIR` 有自己的上述檔案，共用 K 線快取依 `data_feed.py` 的鎖機制處理。

## 8. 維運與研究文件的分工

- 真實線上策略、風控與部署狀態：本文件、[AGENTS.md](../AGENTS.md)。
- 完整回測歷史與本輪決策摘要：[backtest_history.md](backtest_history.md)。
- 2026-08 診斷的原始結論、敏感度表、2,000 天結果與未來稽核清單：[v34_dialogue_research.md](v34_dialogue_research.md)。
- V29 警報統計與設計依據：[v29_research.md](v29_research.md)。
- V31 的 ten-gate 回測可信度與 shadow 升級標準：[v31_research.md](v31_research.md)。

研究候選沒有通過升級稽核前，只能寫入研究文件或 shadow log；不得以文件中的候選值覆蓋 `.env`／`strategy.py` 的現行值。
