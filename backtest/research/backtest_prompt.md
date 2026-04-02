# BTC 策略回測 Prompt

以下是用來請 AI 跑回測的完整 prompt。直接複製貼上即可。

---

## 回測 Prompt

你是一位嚴格的量化交易回測工程師。請幫我回測以下 BTC/USDT 永續合約策略。

**最重要的規則：絕對禁止上帝視角。你必須站在真實交易員的角度，模擬真實的交易流程。**

### 嚴格回測規則（每一條都必須遵守）

1. **進場價用下一根 bar 的 open**：信號在 bar[i] 的 close 判斷，進場在 bar[i+1] 的 open
2. **指標用已收盤的 bar**：用 iloc[-2]（最近一根已收盤），不能用 iloc[-1]（正在發展中的）
3. **1h 指標延遲一根**：5m bar 只能用「上一根已收盤的 1h bar」的指標。對齊方式：1h 的 key = datetime.floor("h") + 1小時
4. **Swing H/L 只用已確認的**：Swing(5) 需要未來 5 根確認，最後 5 根不計算，用 ffill 填充
5. **ATR 百分位 warmup**：前 105 根不交易（ATR 百分位需要 100 根基準）
6. **止損冷卻**：被安全網止損後等 3 根 5m bar（15 分鐘）才能同方向再開倉
7. **安全網 SL 有滑價**：安全網觸發時成交價 = SL + 25% × (bar_extreme - SL)
8. **程式限價出場無滑價**：TP1 和 EMA9 trail 用 close 成交（模擬限價單）
9. **手續費 0.04% maker**：每次開倉 + 平倉各扣一次

### 資料來源

```
Binance 公開 API：
  5m K 線：GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m
  1h K 線：GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h
  期間：最近 6 個月
```

### 交易參數

```
交易所：Binance Futures（USDM, 逐倉）
帳戶：1,000 USDT
每單保證金：100 USDT
槓桿：20x（名義 2,000 USDT/單）
手續費：0.04% maker
同方向最多：3 單
```

### 技術指標（5m K 線）

```python
# RSI(14) — Wilder's smoothing
d = close.diff()
gain = d.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
loss = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
rsi = 100 - 100 / (1 + gain / loss)

# Bollinger Bands(20, 2)
bb_mid = close.rolling(20).mean()
bb_std = close.rolling(20).std()
bb_upper = bb_mid + 2 * bb_std
bb_lower = bb_mid - 2 * bb_std

# ATR(14)
tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
atr = tr.rolling(14).mean()

# ATR 百分位
atr_pctile = atr 在 rolling(100) 中的百分位 (0~100)

# EMA9（Phase 2 出場用）
ema9 = close.ewm(span=9).mean()

# EMA21（過濾用）
ema21 = close.ewm(span=21).mean()
price_vs_ema21 = (close - ema21) / ema21 * 100

# Swing High/Low(5)
# 左右各 5 根比較，ffill 向前填充，最後 5 根不計算

# 1h RSI — 用上一根已收盤的 1h bar
# 對齊：1h 的 hour_key = datetime.floor("h") + 1小時
#        5m 的 hour_key = datetime.floor("h")
```

### 進場條件

```
做多（5 個條件全部滿足）：
  1. 5m RSI(14) < 30
  2. 5m close < 5m BB Lower(20, 2)
  3. ATR 百分位 ≤ 75
  4. |price_vs_ema21| < 2%
  5. 上一根已收盤的 1h RSI >= 其前一根的 1h RSI（下跌趨勢已停止）

做空（鏡像）：
  1. 5m RSI(14) > 70
  2. 5m close > 5m BB Upper(20, 2)
  3. ATR 百分位 ≤ 75
  4. |price_vs_ema21| < 2%
  5. 上一根已收盤的 1h RSI <= 其前一根的 1h RSI（上漲趨勢已停止）

信號在 bar[i] 的 close 判斷 → 進場價 = bar[i+1] 的 open
```

### 開倉

```
市價開倉 + 安全網 SL：
  做多安全網 SL = entry × 0.97（-3%）
  做空安全網 SL = entry × 1.03（+3%）
  安全網是 STOP_MARKET，觸發時有 25% 滑價
```

### 出場邏輯（兩階段）

```
Phase 1：等待 TP1
  做多：close >= entry + 1.0 × ATR（進場時的 ATR）
  做空：close <= entry - 1.0 × ATR

  觸發時：
    - 限價平倉 50%（用 close 成交，0 滑價）
    - 安全網 SL 移到 entry（保本）
    - 進入 Phase 2

Phase 2：EMA9 追蹤
  做多：close < EMA9 → 限價平剩餘 50%（用 close，0 滑價）
  做空：close > EMA9 → 限價平剩餘 50%

安全網 SL（任何 Phase）：
  做多：bar 的 low <= entry × 0.97 → 觸發
    成交價 = SL - 25% × (SL - low)
  做空：bar 的 high >= entry × 1.03 → 觸發
    成交價 = SL + 25% × (high - SL)
```

### 驗證方式

```
1. 全樣本（6 個月）：看整體績效
2. Walk-Forward（前 3 個月開發 / 後 3 個月 OOS）：看 OOS 是否獲利
3. OOS 是唯一可信的績效指標，全樣本可能過擬合
```

### 預期績效（上次回測結果）

```
全樣本：391 筆，PnL -$45，WR 77.7%，PF 0.94
OOS（後 3 個月）：215 筆，PnL +$166，WR 81.9%，PF 1.77
安全網觸發：約 11 次（全樣本）
```

### 輸出要求

請輸出：
1. 完整的 Python 回測程式碼（可直接執行）
2. 全樣本績效表（交易數、PnL、勝率、PF、回撤、安全網觸發次數）
3. Walk-Forward OOS 績效
4. 出場類型拆解（SafeNet / TP1 / Trail 各多少筆、各賺虧多少）
5. 如果結果與預期差異 > 20%，說明可能的原因

---

## 如果要測試不同參數

可以修改以下變數重跑：

```python
# 進場
RSI_LONG_THRESHOLD = 30       # 做多 RSI 閾值
RSI_SHORT_THRESHOLD = 70      # 做空 RSI 閾值
MAX_ATR_PCTILE = 75           # ATR 百分位上限
MAX_EMA21_DEVIATION = 2.0     # 偏離 EMA21 上限 (%)
REQUIRE_1H_RSI_TURN = True    # 是否需要 1h RSI 方向確認

# 出場
SAFENET_PCT = 0.03            # 安全網距離 (3%)
TP1_ATR_MULT = 1.0            # TP1 距離 = N × ATR
TP1_CLOSE_PCT = 0.50          # TP1 平倉比例 (50%)
PHASE2_EXIT = "ema9"          # Phase 2 出場方式：ema9 / adaptive
TRAIL_MAX_MULT = 2.5          # adaptive trail 上限（如果用 adaptive）

# 風控
MARGIN_PER_TRADE = 100
LEVERAGE = 20
MAX_SAME_DIRECTION = 3
SL_COOLDOWN_BARS = 3          # 止損冷卻 bar 數
WARMUP_BARS = 105             # 指標 warmup
```
