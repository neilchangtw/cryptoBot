# 實盤資料規格說明書

> 供回測分析比對使用。描述實盤交易系統的資料格式、策略邏輯、執行機制，以及與回測的已知差異。

---

## 目錄

1. [策略規格](#1-策略規格)
2. [資料檔案總覽](#2-資料檔案總覽)
3. [trades.csv 欄位定義](#3-tradescsv-欄位定義)
4. [bar_snapshots.csv 欄位定義](#4-bar_snapshotscsv-欄位定義)
5. [position_lifecycle.csv 欄位定義](#5-position_lifecyclecsv-欄位定義)
6. [daily_summary.csv 欄位定義](#6-daily_summarycsv-欄位定義)
7. [K 線原始資料格式](#7-k-線原始資料格式)
8. [指標計算公式](#8-指標計算公式)
9. [進出場邏輯詳述](#9-進出場邏輯詳述)
10. [執行機制與回測差異](#10-執行機制與回測差異)
11. [狀態檔 eth_state.json](#11-狀態檔-eth_statejson)
12. [回測重建指南](#12-回測重建指南)

---

## 1. 策略規格

### 1.1 共用參數

| 參數 | 值 | 說明 |
|------|------|------|
| 標的 | ETHUSDT Perpetual | Binance Futures |
| 時間框架 | 1h | 每小時一根 K 棒 |
| 保證金 | $100 / 筆 | 固定 |
| 槓桿 | 20x | 固定 |
| 名目金額 | $2,000 / 筆 | = $100 × 20 |
| 交易成本 | $2 / 筆 | taker 0.04%×2 + slip 0.01%×2 |
| SafeNet | ±5.5% | 硬止損，L/S 共用 |
| Session Filter | Block hours: {0,1,2,12} UTC+8 | 流動性不足時段 |
| Session Filter | Block days: {Mon,Sat,Sun} | weekday index: Mon=0, Sat=5, Sun=6 |
| Hedge Mode | dualSidePosition=true | L/S 倉位互不影響 |

### 1.2 L 策略（做多）— 趨勢跟隨

**進場條件（全部 AND）：**

| # | 條件 | 說明 |
|---|------|------|
| 1 | OR-entry 任一成立 | GK pctile < 30 **OR** Skew(20) > 1.0 **OR** RetSign(15) > 0.60 |
| 2 | Close Breakout 10 bar 向上 | `close[t-1] > max(close[t-2], ..., close[t-10])` |
| 3 | Session Filter PASS | 非封鎖時段/星期 |
| 4 | L Exit Cooldown ≥ 12 bar | 自上次 L 出場起算 |
| 5 | L 持倉數 < 9 | 最多同時 9 筆 L |

**出場條件（優先順序高→低）：**

| # | 名稱 | 條件 | 出場價 |
|---|------|------|--------|
| 1 | SafeNet | `bar_low ≤ entry × (1 - 5.5%)` | `safenet_level - (safenet_level - bar_low) × 0.25` |
| 2 | EarlyStop | bar 7-11, `close ≤ entry × (1 - 1%)` 且 Trail 未觸發 | `bar_close` |
| 3 | Trail | bar 7-11: `close ≤ EMA20`; bar ≥12: `close ≤ EMA20` | `bar_close` |

> 注意：bar 7-11 時 Trail 和 EarlyStop 是 OR 關係。如果 `close ≤ EMA20`，無論虧損多少都觸發 Trail。如果 Trail 未觸發但 `close ≤ entry×0.99`，觸發 EarlyStop。

**OOS 回測基準**：428t, $13,882, PF 2.94, WR 45%, MDD 12.3%

### 1.3 S 策略（做空）— CMP-Portfolio

4 個子策略並行，各自獨立進場/出場/計數。

| 子策略 | GK 閾值 | Breakout Lookback | maxSame | Exit Cooldown |
|--------|---------|-------------------|---------|---------------|
| S1 | < 40 | 8 bar | 5 | 6 bar |
| S2 | < 40 | 15 bar | 5 | 6 bar |
| S3 | < 30 | 10 bar | 5 | 6 bar |
| S4 | < 40 | 12 bar | 5 | 6 bar |

**進場條件（每個子策略獨立，全部 AND）：**

| # | 條件 |
|---|------|
| 1 | GK pctile < 子策略 threshold |
| 2 | Close Breakout {8/10/12/15} bar 向下 |
| 3 | Session Filter PASS |
| 4 | 子策略 Cooldown ≥ 6 bar |
| 5 | 子策略持倉 < 5 |

**出場條件（優先順序高→低）：**

| # | 名稱 | 條件 | 出場價 |
|---|------|------|--------|
| 1 | SafeNet | `bar_high ≥ entry × (1 + 5.5%)` | `safenet_level + (bar_high - safenet_level) × 0.25` |
| 2 | TP | `bar_low ≤ entry × (1 - 2%)` | `entry × 0.98`（固定） |
| 3 | MaxHold | `bars_held ≥ 12` | `bar_close` |

**OOS 回測基準**：1124t, $10,113, PF 1.73, WR 65%, MDD 17.6%

---

## 2. 資料檔案總覽

所有檔案位於 `data/` 目錄（Paper 模式）或 `data_live/`（Live 模式）。

| 檔案 | 粒度 | 寫入時機 | 用途 |
|------|------|----------|------|
| `trades.csv` | 每筆交易一行 | 進場時建立，出場時回填 | 逐筆交易分析、回測比對 |
| `bar_snapshots.csv` | 每根 K 棒一行 | 每小時，無論有無交易 | 指標驗證、信號還原 |
| `position_lifecycle.csv` | 持倉×每 bar | 持倉期間每小時 | 持倉路徑分析、MAE/MFE |
| `daily_summary.csv` | 每日一行 | 每日收盤 | 快速瀏覽日報 |
| `ETHUSDT_1h_latest730d.csv` | 原始 K 棒 | data_feed.py 快取 | 回測用 730 天歷史資料 |

---

## 3. trades.csv 欄位定義

### 3.1 交易識別

| 欄位 | 型態 | 範例 | 說明 |
|------|------|------|------|
| `trade_id` | string | `20260408_070000_L` | 格式: `YYYYMMDD_HHMMSS_子策略`，時間為 UTC+8 |
| `trade_number` | int | `1` | 累計流水號，從 1 開始 |

### 3.2 進場資訊

| 欄位 | 型態 | 範例 | 說明 |
|------|------|------|------|
| `entry_time_utc` | datetime | `2026-04-07 23:00:00` | 進場時間 UTC |
| `entry_time_utc8` | datetime | `2026-04-08 07:00:00` | 進場時間 UTC+8 |
| `entry_weekday` | int | `2` | 星期幾（0=Mon, 6=Sun），基於 UTC+8 |
| `entry_hour_utc8` | int | `7` | UTC+8 小時 |
| `direction` | string | `LONG` / `SHORT` | 方向 |
| `sub_strategy` | string | `L` / `S1` / `S2` / `S3` / `S4` | 子策略 ID |
| `entry_price` | float | `2241.19` | 實際成交價（Testnet MARKET 單） |
| `entry_signal_bar_close` | float | `2239.14` | 觸發信號的 bar 之 close（理論進場價） |

> **entry_price vs entry_signal_bar_close**：差異為 Testnet 市價單滑價，通常 <0.1%。回測應使用 `entry_signal_bar_close` 或下一根 bar 的 open 作為進場價。

### 3.3 進場指標快照

| 欄位 | 型態 | 說明 |
|------|------|------|
| `gk_pctile_at_entry` | float | 進場時 GK percentile（0-100） |
| `gk_ratio_at_entry` | float | 進場時 GK ratio（短期/長期） |
| `breakout_bar_close` | float | 突破 bar 的 close |
| `breakout_10bar_max` | float | 前 10 bar close 最高值（L 用） |
| `breakout_10bar_min` | float | 前 10 bar close 最低值（S 用） |
| `breakout_strength_pct` | float | 突破強度 = `(close - max/min) / close × 100` |
| `ema20_at_entry` | float | 進場時 EMA20 值 |
| `ema20_distance_pct` | float | 進場價與 EMA20 距離百分比 |
| `was_cooldown_trade` | bool | 是否在冷卻期結束後立即進場 |
| `bars_since_last_exit` | int | 距上次同子策略出場的 bar 數 |

### 3.4 持倉過程統計

| 欄位 | 型態 | 說明 |
|------|------|------|
| `hold_bars` | int | 持倉 bar 數 |
| `hold_hours` | int | 持倉小時數（= hold_bars，因為 1h 時間框架） |
| `max_adverse_excursion_pct` | float | MAE：持倉期間最大不利偏移 %（負值） |
| `max_adverse_excursion_usd` | float | MAE 對應的 USD 金額（負值） |
| `max_favorable_excursion_pct` | float | MFE：持倉期間最大有利偏移 % |
| `max_favorable_excursion_usd` | float | MFE 對應的 USD 金額 |
| `mae_time_bar` | int | MAE 發生在第幾根 bar |
| `mfe_time_bar` | int | MFE 發生在第幾根 bar |
| `pnl_at_bar7` | float | 第 7 根 bar 時的未實現 PnL %（評估 EarlyStop 效果） |
| `pnl_at_bar12` | float | 第 12 根 bar 時的未實現 PnL %（評估 MaxHold 效果） |

### 3.5 出場資訊

| 欄位 | 型態 | 說明 |
|------|------|------|
| `exit_time_utc` | datetime | 出場時間 UTC |
| `exit_time_utc8` | datetime | 出場時間 UTC+8 |
| `exit_type` | string | `SafeNet` / `EarlyStop` / `Trail` / `TP` / `MaxHold` |
| `exit_price` | float | 出場價格 |
| `exit_trigger_bar` | int | 在持倉第幾根 bar 觸發出場 |

**exit_type 說明**：

| exit_type | 適用策略 | 觸發機制 |
|-----------|----------|----------|
| `SafeNet` | L, S | 硬止損 ±5.5%，含滑價模型（見 §9） |
| `EarlyStop` | L only | bar 7-11，虧損 > 1%，Trail 未觸發 |
| `Trail` | L only | bar ≥ 7，close ≤ EMA20 |
| `TP` | S only | bar_low 觸及 entry × 0.98 |
| `MaxHold` | S only | 持倉達 12 bar |

### 3.6 損益計算

| 欄位 | 型態 | 說明 |
|------|------|------|
| `gross_pnl_usd` | float | 毛利 = `(exit - entry) / entry × $2000`（LONG），SHORT 反向 |
| `commission_usd` | float | 固定 $2 / 筆 |
| `net_pnl_usd` | float | 淨利 = gross - commission |
| `net_pnl_pct` | float | 淨利佔保證金百分比 = `net_pnl / $100 × 100` |
| `win_loss` | string | `WIN` / `LOSS` |

**PnL 計算公式**：

```
LONG:  gross = (exit_price - entry_price) / entry_price × $2,000
SHORT: gross = (entry_price - exit_price) / entry_price × $2,000
net   = gross - $2
pct   = net / $100 × 100
```

### 3.7 市場背景

| 欄位 | 型態 | 說明 |
|------|------|------|
| `btc_close_at_entry` | float | 進場時 BTC 收盤價 |
| `eth_btc_ratio_at_entry` | float | ETH/BTC 比率 |
| `eth_24h_change_pct` | float | ETH 過去 24h 漲跌幅 % |

### 3.8 回測比對欄位（事後填入）

| 欄位 | 型態 | 說明 |
|------|------|------|
| `backtest_had_same_trade` | string | `YES` / 空白 |
| `backtest_entry_price` | float | 回測的進場價 |
| `backtest_exit_type` | string | 回測的出場類型 |
| `backtest_pnl_usd` | float | 回測的 PnL |
| `discrepancy_note` | string | 差異說明 |

### 3.9 主觀複盤欄位（手動填入）

| 欄位 | 說明 |
|------|------|
| `review_note` | 事後複盤筆記 |
| `pattern_tag` | 模式標籤（如 `trend_reversal`, `range_breakout`） |
| `lesson` | 學到的教訓 |

### 3.10 未平倉交易

trades.csv 中 `exit_time_utc` 為空的行代表**尚未平倉的交易**。此時出場相關欄位（`exit_type`, `exit_price`, `hold_bars`, MAE/MFE 等）均為空值。持倉過程追蹤由 position_lifecycle.csv 即時記錄。

---

## 4. bar_snapshots.csv 欄位定義

每小時一行，記錄 K 棒數據 + 所有指標 + 信號評估結果。

### 4.1 時間與 K 棒

| 欄位 | 型態 | 說明 |
|------|------|------|
| `bar_time_utc` | datetime | K 棒時間 UTC |
| `bar_time_utc8` | datetime | K 棒時間 UTC+8 |
| `bar_weekday` | int | 星期（0=Mon, 6=Sun），基於 UTC+8 |
| `open` | float | 開盤價 |
| `high` | float | 最高價 |
| `low` | float | 最低價 |
| `close` | float | 收盤價 |
| `volume` | float | 成交量（ETH 計） |
| `taker_buy_volume` | float | 主動買入量 |

### 4.2 GK 指標

| 欄位 | 型態 | 說明 |
|------|------|------|
| `gk_ratio` | float | GK 短長期比率（5bar / 20bar 均值），越大越波動 |
| `gk_pctile` | float | GK ratio 在過去 100 bar 的百分位（0-100），已 shift(1) |

> `gk_pctile` 已做前瞻防護（shift(1)）：使用的是上一根 bar 的 GK ratio 在滾動窗口中的位置。

### 4.3 Breakout 指標

| 欄位 | 型態 | 說明 |
|------|------|------|
| `breakout_long` | bool | `close[t-1] > max(close[t-2..t-10])` 向上突破 |
| `breakout_short` | bool | `close[t-1] < min(close[t-2..t-10])` 向下突破（BL10） |

> S 子策略各有不同 lookback 的 breakout，但 bar_snapshots 只記錄 BL10。S 子策略的 BL8/BL12/BL15 資訊需從原始 K 線重算。

### 4.4 L 專用指標

| 欄位 | 型態 | 說明 |
|------|------|------|
| `skew_20` | float | 20 bar 報酬率偏態，shift(1)。正偏 = 上漲傾向 |
| `ret_sign_15` | float | 15 bar 正報酬比例，shift(1)。>0.6 = 連續上漲 |

### 4.5 其他指標

| 欄位 | 型態 | 說明 |
|------|------|------|
| `ema20` | float | 20 期指數移動平均（L 出場用） |
| `session_allowed` | bool | 是否在交易時段 |

### 4.6 信號評估

| 欄位 | 型態 | 範例 | 說明 |
|------|------|------|------|
| `long_signal` | string | `HOLD` / `L:OR:gk=12.0+skew=1.25` | L 信號狀態 |
| `short_signals` | string | `HOLD` / `S1,S2,S4` | 觸發的 S 子策略列表 |
| `signal_detail` | string | `S1:S1:gk40_BL8\|S2:...` | 各子策略信號詳情，`|` 分隔 |

**long_signal 格式**：
- `HOLD`：無信號
- `L:OR:gk=12.0+skew=1.25+ret_sign=0.67`：OR 條件中哪些通過

**signal_detail 格式**：
- 多子策略用 `|` 分隔
- 每段：`子策略ID:原因`

### 4.7 持倉狀態

| 欄位 | 型態 | 說明 |
|------|------|------|
| `long_positions` | int | 當前 L 持倉數 |
| `short_positions` | int | 當前 S 持倉數（所有子策略加總） |
| `total_unrealized_pnl` | float | 所有持倉未實現 PnL（USD） |

---

## 5. position_lifecycle.csv 欄位定義

每個持倉在持有期間，每根 bar 記錄一行。用於分析持倉路徑。

| 欄位 | 型態 | 說明 |
|------|------|------|
| `trade_id` | string | 對應 trades.csv 的 trade_id |
| `lifecycle_bar` | int | 持倉的第幾根 bar（1-based） |
| `bar_time_utc` | datetime | 當前 bar UTC |
| `bar_time_utc8` | datetime | 當前 bar UTC+8 |
| `open/high/low/close` | float | 本根 bar 的 OHLC |
| `entry_price` | float | 進場價（固定，方便直接分析） |
| `current_price` | float | 本 bar 收盤價 = close |
| `unrealized_pnl_usd` | float | 未實現 PnL（USD） |
| `unrealized_pnl_pct` | float | 未實現 PnL（%） |
| `max_adverse_so_far` | float | 截至本 bar 的累計 MAE（%） |
| `max_favorable_so_far` | float | 截至本 bar 的累計 MFE（%） |
| `ema20` | float | 本 bar 的 EMA20 |
| `distance_to_ema20_pct` | float | close 與 EMA20 距離（%） |
| `safenet_distance_pct` | float | close 與 SafeNet 觸發價距離（%） |
| `earlyStop_eligible` | bool | 本 bar 是否在 EarlyStop 有效範圍（bar 7-11，僅 L） |
| `exit_triggered` | bool | 本 bar 是否觸發出場 |
| `exit_type` | string | 出場類型（僅出場 bar 有值） |
| `exit_price` | float | 出場價格（僅出場 bar 有值） |
| `exit_pnl_usd` | float | 出場 PnL（僅出場 bar 有值） |

---

## 6. daily_summary.csv 欄位定義

| 欄位 | 型態 | 說明 |
|------|------|------|
| `date` | string | 日期 YYYY-MM-DD（UTC+8） |
| `total_trades` | int | 當日平倉交易數 |
| `long_trades` | int | L 平倉數（部分版本可能為空） |
| `short_trades` | int | S 平倉數（部分版本可能為空） |
| `wins` | int | 獲利筆數 |
| `losses` | int | 虧損筆數 |
| `gross_pnl` | float | 毛利（USD） |
| `net_pnl` | float | 淨利（USD） |
| `safenet_count` | int | SafeNet 出場筆數 |
| `earlyStop_count` | int | EarlyStop 出場筆數 |
| `trail_count` | int | Trail 出場筆數 |
| `tp_count` | int | TP 出場筆數 |
| `maxhold_count` | int | MaxHold 出場筆數 |
| `avg_hold_hours` | float | 平均持倉小時 |
| `longest_hold_hours` | int | 最長持倉小時 |
| `account_balance` | float | Testnet 帳戶餘額（非策略計算值） |
| `cumulative_pnl` | float | 累計 PnL（自系統啟動起） |
| `open_position` | string | 收盤時持倉狀態，如 `L:3 S:4` |
| `system_alerts` | int | 系統告警數 |

---

## 7. K 線原始資料格式

### 7.1 檔案：`data/ETHUSDT_1h_latest730d.csv`

Binance Futures 公開 API 取得的 730 天 1h K 線快取。回測時使用此檔案作為歷史資料。

| 欄位 | 型態 | 說明 |
|------|------|------|
| `ot` | int | Open time（Unix ms） |
| `open` | float | 開盤價 |
| `high` | float | 最高價 |
| `low` | float | 最低價 |
| `close` | float | 收盤價 |
| `volume` | float | 成交量（ETH 計） |
| `ct` | int | Close time（Unix ms） |
| `qv` | float | Quote volume（USDT 計） |
| `trades` | int | 成交筆數 |
| `tbv` | float | Taker buy volume（ETH 計） |
| `tbqv` | float | Taker buy quote volume（USDT 計） |
| `ig` | int | 忽略欄位（Binance API 保留） |
| `datetime` | datetime | UTC+8 時間字串 |

### 7.2 即時資料來源

- 端點：`https://fapi.binance.com/fapi/v1/klines`
- 參數：`symbol=ETHUSDT, interval=1h, limit=150`
- 每小時整點 +10 秒自動抓取
- 55 秒記憶體快取，避免同 cycle 重複呼叫
- datetime 轉為 UTC+8（與回測 CSV 格式一致）

---

## 8. 指標計算公式

### 8.1 Garman-Klass Volatility (GK)

```python
ln_hl = ln(high / low)
ln_co = ln(close / open)
gk = 0.5 × ln_hl² - (2×ln2 - 1) × ln_co²

gk_short = rolling_mean(gk, 5)   # 短期均值
gk_long  = rolling_mean(gk, 20)  # 長期均值
gk_ratio = gk_short / gk_long    # 壓縮指數

# Percentile: shift(1) + rolling(100)
gk_ratio_shifted = gk_ratio.shift(1)
gk_pctile = (current - min) / (max - min) × 100 over 100 bars
```

- `gk_pctile` 接近 0：極端壓縮（波動率極低），預期突破
- `gk_pctile` 接近 100：波動率已擴張

### 8.2 Breakout

```python
close_shift1 = close.shift(1)          # 前一根 bar 的 close
lookback_max = close.shift(2).rolling(lookback - 1).max()
lookback_min = close.shift(2).rolling(lookback - 1).min()

breakout_long  = close_shift1 > lookback_max  # 向上突破
breakout_short = close_shift1 < lookback_min  # 向下突破
```

- L 策略固定用 lookback=10
- S 子策略各自用 lookback=8/10/12/15

### 8.3 Skew (L 專用)

```python
ret = close.pct_change()
skew_20 = ret.rolling(20).skew().shift(1)
```

- 正偏態（>1.0）暗示有大幅上漲的傾向

### 8.4 RetSign (L 專用)

```python
ret_sign_15 = (ret > 0).astype(float).rolling(15).mean().shift(1)
```

- 過去 15 bar 中正報酬的比例
- \>0.60 暗示連續上漲趨勢

### 8.5 EMA20

```python
ema20 = close.ewm(span=20).mean()
```

- L 策略 Trail 出場用：close ≤ EMA20 觸發出場

### 8.6 暖機期

```
WARMUP_BARS = GK_WIN(100) + GK_LONG(20) + 20 = 140 bars
```

前 140 根 bar 的指標不穩定，不應產生信號。

---

## 9. 進出場邏輯詳述

### 9.1 執行時序

```
每小時整點 +10 秒：
  1. 抓取最新 150 根 1h K 線（Binance Futures API）
  2. compute_indicators()：計算所有指標
  3. 遍歷現有持倉，檢查出場（L 用 check_exit, S 用 check_exit_cmp）
  4. 已出場的倉位在本 bar 結算，記錄到 trades.csv
  5. 評估新進場信號（L: evaluate_long_signal, S: evaluate_short_signals）
  6. 先出場再進場：同一根 bar 可以出場 + 進場
  7. 記錄 bar_snapshot + position_lifecycle
```

### 9.2 進場價格決定

```python
# main_eth.py line 355, 384
fill_price = bar_data["close"]  # 信號 bar 的 close
trade_id = executor.open_position(entry_price=fill_price, ...)
```

- 信號在 bar close 後判斷，理論進場價 = 信號 bar 的 close
- 實際下單到 Testnet（MARKET order），成交價略有不同
- `entry_signal_bar_close` = 理論價，`entry_price` = 實際成交價

### 9.3 SafeNet 滑價模型

SafeNet 觸發時，出場價不直接用 SafeNet 水位，而是套用滑價模型：

```python
# LONG SafeNet
safenet_level = entry × (1 - 0.055)
if bar_low <= safenet_level:
    exit_price = safenet_level - (safenet_level - bar_low) × 0.25

# SHORT SafeNet
safenet_level = entry × (1 + 0.055)
if bar_high >= safenet_level:
    exit_price = safenet_level + (bar_high - safenet_level) × 0.25
```

含義：假設價格穿越 SafeNet 水位後繼續走 25%，模擬真實滑價。

### 9.4 S 子策略獨立性

4 個 S 子策略**完全獨立**：
- 各自維護 cooldown 計數器
- 各自維護持倉計數（maxSame=5 是每個子策略獨立）
- 同一根 bar 可以同時觸發 0~4 個子策略
- 出場時按 trade_id 個別結算

### 9.5 bar_counter 與 cooldown

```
bar_counter: 系統啟動後累計處理的 bar 數（含暖機期跳過的 bar）
last_exits: {"L": bar_counter, "S1": bar_counter, ...}
cooldown = bar_counter - last_exits[sub_strategy] >= required_cooldown
```

- L 出場後需等 12 bar（= 12 小時）
- S 各子策略出場後需等 6 bar（= 6 小時）

---

## 10. 執行機制與回測差異

### 10.1 已確認差異

| 項目 | 回測 | 實戰 | 影響 |
|------|------|------|------|
| **進場價** | `O[i+1]`（下一根 bar 的 open） | Testnet MARKET 單成交價 | 差異 <0.1%，在 $2 fee budget 內 |
| **信號 bar close vs open** | 在 bar close 時判斷，用 next open 成交 | 在 bar close 時判斷，用 MARKET 單即時成交 | 幾乎相同，因 close ≈ next open |
| **SafeNet 執行** | 每 bar 結算，用 bar_low/bar_high 判斷 | 同上（非即時止損單） | 完全一致 |
| **SL 單** | 無 | Testnet 有 Algo Order SL（作為額外保護） | SL 單是備用防護，正常情況不影響 |
| **Testnet 特殊行為** | 無 | MARKET 單有時回傳 avgPrice=0，需輪詢 | 極少數情況 entry_price 可能為 0 |

### 10.2 已知問題

| 問題 | 說明 | 影響 |
|------|------|------|
| 第一筆交易遺失 | 系統升級時 20260407_170000_L（$2127.26 進場）未遷移到 trades.csv | 漏記一筆預估 +$61 的 L 勝出交易 |
| compare_backtest.py 過期 | 仍引用舊常數 `GK_THRESH`、`EXIT_COOLDOWN` | 工具無法使用，需更新到雙策略 API |
| daily_summary 早期格式 | 前幾日缺少 `tp_count`、`maxhold_count` 欄位 | 可能顯示為空值 |

### 10.3 不影響比對的差異

| 項目 | 說明 |
|------|------|
| account_balance | Testnet 餘額，非策略 PnL 計算值。比對時以 `net_pnl_usd` 加總為準 |
| Hedge Mode SL 限制 | 每個方向只能有 1 個 closePosition SL，不影響策略邏輯 |
| Testnet 流動性 | 實際成交價偏差由 entry_price vs entry_signal_bar_close 可量化 |

---

## 11. 狀態檔 eth_state.json

系統每 bar 更新的持久化狀態。

```json
{
  "positions": {
    "<trade_id>": {
      "trade_id": "20260410_200000_L",
      "side": "long",
      "sub_strategy": "L",
      "entry_price": 2217.99,
      "entry_time_utc": "2026-04-10 12:00:00",
      "entry_time_utc8": "2026-04-10 20:00:00",
      "entry_bar_counter": 134,
      "qty": 0.902,
      "bars_held": 11,
      "mae_pct": -0.487,
      "mfe_pct": 1.783,
      "mae_time_bar": 1,
      "mfe_time_bar": 3,
      "pnl_at_bar7": 1.524,
      "pnl_at_bar12": null
    }
  },
  "last_exits": {"L": 120, "S1": 142, "S2": 142, "S3": 142, "S4": 142},
  "account_balance": 4314.24,
  "bar_counter": 145,
  "last_bar_time": "2026-04-11 07:00:00",
  "trade_number": 26,
  "daily_stats": { ... }
}
```

| 欄位 | 說明 |
|------|------|
| `positions` | 所有未平倉持倉 |
| `last_exits` | 各子策略最後出場的 bar_counter（cooldown 計算用） |
| `bar_counter` | 累計處理 bar 數 |
| `last_bar_time` | 最後處理的 bar 時間 UTC（防重複處理） |
| `trade_number` | 下一筆交易的流水號 |
| `daily_stats` | 每日統計快取 |

---

## 12. 回測重建指南

### 12.1 用歷史 K 線重跑回測

```python
import pandas as pd
import strategy

# 1. 載入歷史資料
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

# 2. 計算指標
df = strategy.compute_indicators(df)

# 3. 模擬交易迴圈
W = strategy.WARMUP_BARS  # 140
for i in range(W, len(df) - 1):
    # 出場檢查 → L: strategy.check_exit(), S: strategy.check_exit_cmp()
    # 進場信號 → strategy.evaluate_long_signal(), strategy.evaluate_short_signals()
    # 進場價 = df.iloc[i+1]["open"]  ← 回測用 next bar open
    pass
```

### 12.2 比對步驟

1. **篩選時間範圍**：回測結果限定在實盤運行期間
2. **交易匹配**：按方向 + 進場時間（±2h 容差）配對
3. **比對項目**：
   - 進場價差異 = `|回測 O[i+1] - 實盤 entry_price|`
   - 出場類型是否一致
   - PnL 差異
   - 信號觸發條件一致性（比對 bar_snapshots 中的 long_signal / short_signals）

### 12.3 已知比對陷阱

| 陷阱 | 說明 |
|------|------|
| K 線資料版本 | 實盤運行時的即時 K 線可能與事後下載的 730d CSV 有微小差異（Binance 偶爾更正歷史數據） |
| Warmup 期差異 | 實盤從第 bar_counter=1 開始累計，回測從 df index=WARMUP_BARS 開始。需對齊絕對時間 |
| 同 bar 多筆進場 | 同一根 bar 可開多筆 S 子策略，但回測迴圈的遍歷順序可能影響結果（應按 S1→S4 順序） |
| Cooldown 初始值 | 實盤系統啟動時 `last_exits` 初始為 -9999，回測也需一致 |
| S Breakout lookback | bar_snapshots 只記錄 BL10，若需驗證 S2(BL15) 或 S4(BL12) 信號，需從原始 K 線重算 |

---

## 附錄：實盤首週統計摘要（2026-04-07 ~ 2026-04-11）

| 指標 | L 策略 | S 策略 | 合計 |
|------|--------|--------|------|
| 已平倉 | 8 筆 | 14 筆 | 22 筆 |
| 勝 / 負 | 1 / 7 | 10 / 4 | 11 / 11 |
| WR | 12.5% | 71.4% | 50.0% |
| 淨 PnL | -$285.10 | -$87.35 | -$372.45 |
| 出場分佈 | Trail:5, EarlyStop:2, SafeNet:0 | TP:3, MaxHold:11, SafeNet:0 | — |
| 未平倉 | 3 筆 | 0 筆 | 3 筆 |

> 4 天 / 22 筆交易在統計上不具代表性，無法驗證回測預期。需累計 200+ 筆（約 2-3 個月）後再做正式統計驗證。
