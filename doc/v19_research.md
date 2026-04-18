# V19 Research: 跳脫框架自由探索

> V19 goal: 跳脫「ETH OHLCV 時序分析」框架，嘗試所有可取得的外部數據和新方法論。
> V6-V18 共 25,000+ 配置已證明 OHLCV 單因子和多因子技術指標無法在非 breakout bar 上產生 alpha。
> V19 是最後的嘗試：引入完全外部的數據源（宏觀、情緒）和新方法論（HMM）。
>
> **最終結論：ETH 在所有可取得的免費數據源和分析方法下，唯一可交易的 alpha = 15-bar close breakout。**
> **非 breakout bar 是 random walk（MFE 對稱、方向不可預測），無論用什麼信號、數據或方法論。**

---

## 研究範圍

### 已排除（V6-V18 已窮舉）
- 所有 OHLCV 技術指標（RSI、BB、MACD、EMA、ATR...）
- 所有 K 線形態（engulfing、pin bar、inside bar...）
- 所有時段/星期效應
- BTC-ETH 相關性/背離
- Funding rate 極端值
- TBR (Taker Buy Ratio)
- 所有時框（15m/30m/1h）

### V19 嘗試的新方向
1. **Binance OI + L/S Ratio** → 數據不足（僅 30 天），放棄
2. **跨市場宏觀（SPX/DXY/VIX/US10Y）** → R1 測試，零預測力
3. **Crypto 情緒（Fear & Greed Index）** → R1 測試，零預測力
4. **Deribit 選擇權** → 使用者無數據源，跳過
5. **HMM Regime Detection + 統計結構分析** → R2 測試，零 alpha

---

## R0: 數據下載 (`v19_r0_download_data.py`)

### 數據可用性

| 數據源 | API | 費用 | 結果 |
|--------|-----|------|------|
| Binance OI (1h) | `/futures/data/openInterestHist` | 免費 | **僅 30 天歷史** — 不夠回測 |
| Binance L/S Ratio (1h) | `/futures/data/globalLongShortAccountRatio` | 免費 | **僅 30 天歷史** — 不夠回測 |
| Binance Top Trader LSR | `/futures/data/topLongShortPositionRatio` | 免費 | **僅 30 天歷史** — 不夠回測 |
| Binance Taker Volume | `/futures/data/takerlongshortRatio` | 免費 | **僅 30 天歷史** — 不夠回測 |
| SPX daily | Yahoo Finance `^GSPC` | 免費 | **501 天** ✓ |
| DXY daily | Yahoo Finance `DX-Y.NYB` | 免費 | **503 天** ✓ |
| VIX daily | Yahoo Finance `^VIX` | 免費 | **501 天** ✓ |
| US10Y daily | Yahoo Finance `^TNX` | 免費 | **501 天** ✓ |
| Fear & Greed Index | Alternative.me API | 免費 | **730 天** ✓ |

**方向 1 (OI/LSR) 因數據不足直接放棄。** 只有付費數據源（CoinGlass 等）才有足夠歷史。

---

## R1: 跨市場宏觀 + 情緒可行性研究 (`v19_r1_macro_feasibility.py`)

### B. 相關性分析

所有宏觀變量（前一天）vs ETH forward return（1-5 天）：

| Predictor | fwd_1d | fwd_2d | fwd_3d | fwd_5d |
|-----------|--------|--------|--------|--------|
| SPX_ret (lag1) | r=+0.032 p=0.39 | r=+0.050 p=0.18 | r=+0.021 p=0.57 | r=+0.038 p=0.31 |
| DXY_ret (lag1) | r=-0.028 p=0.45 | r=-0.033 p=0.38 | r=-0.012 p=0.76 | r=+0.004 p=0.92 |
| VIX_ret (lag1) | r=-0.053 p=0.15 | r=-0.061 p=0.10 | r=-0.062 p=0.10 | r=-0.077 p=0.04 |
| US10Y_ret (lag1) | r=+0.025 p=0.50 | r=+0.032 p=0.39 | r=+0.045 p=0.22 | r=+0.049 p=0.19 |
| FGI (lag1) | r=+0.032 p=0.40 | r=+0.039 p=0.29 | r=+0.052 p=0.17 | r=+0.069 p=0.07 |
| FGI_delta (lag1) | r=+0.019 p=0.60 | r=-0.011 p=0.76 | r=+0.008 p=0.83 | r=-0.007 p=0.85 |

**全部不顯著**（唯一例外：VIX vs fwd_5d, r=-0.077, p=0.04 — 邊際顯著但 r 極小）。

SPX lag 分析：lag-0 (同日) r=+0.106 (顯著) → lag-1 r=+0.032 (不顯著)。
**ETH 對宏觀消息的反應是即時的，不存在可交易的滯後。**

### C. 五分位條件報酬

所有宏觀變量的 Q1-Q5 五分位：ETH forward return 無統計顯著差異。
FGI Q5-Q1 spread: +0.010%, p=0.98 — 零。

### D. FGI 極端值

| Regime | N | fwd_1d | fwd_5d |
|--------|---|--------|--------|
| Extreme Fear (0-20) | 100 | +0.171% | -0.600% |
| Extreme Greed (80-100) | 20 | +0.488% | +1.662% |

看似 greed 好於 fear（反直覺），但 N=20, SE=0.76% → 0.488% 在一個標準誤內，不顯著。

### E. Risk-On/Off 多因子

| Regime | N | fwd_1d | p |
|--------|---|--------|---|
| Risk-On (SPX↑ DXY↓ VIX↓) | 112 | -0.143% | |
| Risk-Off (SPX↓ DXY↑ VIX↑) | 89 | -0.510% | |
| Mixed | 520 | +0.136% | |

Risk-On vs Risk-Off: t=+0.88, **p=0.381** — 不顯著。

### F. 信號原型 IS/OOS

| Signal | IS PnL | OOS PnL | IS+/OOS+? |
|--------|--------|---------|-----------|
| FGI Contrarian | +$88 | -$394 | ❌ |
| SPX Follow | +$903 | -$1,543 | ❌ |
| **DXY Inverse** | **+$828** | **+$265** | ✓ 但 WR 50% |
| VIX MeanRev | -$2,936 | +$1,103 | ❌ |
| Risk Composite | +$3,019 | -$2,218 | ❌ |
| FGI Momentum | +$2,758 | -$268 | ❌ |
| VIX Level | -$1,747 | +$228 | ❌ |

唯一 IS+/OOS+ 的 DXY Inverse：OOS 僅 $265/年，WR 50.5% ≈ 丟硬幣。經濟上無意義。

### R1 結論

**跨市場宏觀和 crypto 情緒對 ETH 日報酬的預測力為零。**
- 所有 Pearson r < 0.08, 全部 p > 0.05（VIX-5d 邊際例外）
- 同日 SPX-ETH r=0.106 → 即時反應，無滯後套利
- 8 個信號原型中，0 個同時 IS+/OOS+ 且有經濟意義

---

## R2: 統計結構 + HMM Regime Detection (`v19_r2_regime_detection.py`)

### A. 自相關結構

| 測試 | 關鍵發現 | 交易含義 |
|------|---------|---------|
| Return ACF | 所有 \|r\| < 0.02 | 報酬率序列相關為零 |
| Sign ACF | Lag 1: r=-0.047 (t=-6.27***) | 微弱均值回歸，太小無法獲利 |
| Variance Ratio | 全部 VR ≈ 1.0 | Random walk 確認 |
| Squared ACF | Lag 1: r=+0.111 (t=14.67***) | 波動率可預測，方向不可預測 |
| Non-BRK ACF | Lag 1: r=-0.074 (t=-8.53***) | 非 BRK 有較強均值回歸，但仍 < fee |

**ETH 1h 報酬率的可預測結構只有兩個：(1) 波動率叢集（方向無關），(2) 微弱均值回歸（太小無法覆蓋費用）。**

### B. HMM Walk-Forward (3 state)

Non-breakout only:

| State | N | Mean Fwd_1 | p-value | 意義 |
|-------|---|-----------|---------|------|
| 0 | 4,985 | -0.0005% | 0.958 | = 零 |
| 1 | 4,403 | -0.0012% | 0.907 | = 零 |
| 2 | 3,450 | +0.0039% | 0.738 | = 零 |

**3 個 HMM state 的 forward return 全部統計等於零。HMM 無法發現非 breakout 的方向性 alpha。**

另一個關鍵發現：3 個 state 的 breakout 比例幾乎相同（23.8%-25.0%），代表 HMM 沒有把 breakout 和非 breakout 分成不同 regime — breakout 在所有 regime 中均勻分佈。

### C. 交易信號測試

| Signal Group | IS PnL | OOS PnL | 結論 |
|-------------|--------|---------|------|
| Momentum regime (non-BRK) | -$3,602 ~ -$14,128 | -$850 ~ -$12,226 | 全部巨虧 |
| Vol compression \|fwd\| (straddle) | +$22,138 | +$21,069 | 正數但無方向 |
| HMM best/worst state | -$14,356 | -$16,306 | 全部巨虧 |
| Consecutive bars | 混雜 (N<53) | 不可靠 | 噪音 |
| Skewness regime | -$11,123 ~ -$15,588 | -$13,543 ~ -$23,648 | 全部巨虧 |

唯一正數的 Signal Group 2 (Vol compression |fwd|) 是 straddle 等價物 — 它確認了 GK 壓縮後波動率放大，但無法告訴你方向。不可用單一合約交易。

### D. MFE 對稱性（終極證明）

Non-breakout bars, IS 和 OOS 分別計算：

| Hold | L_MFE | S_MFE | Ratio | Mean Ret | Up% | p |
|------|-------|-------|-------|----------|-----|---|
| 1h | 0.450% | 0.454% | **0.991** | +0.001% | 50.2% | 0.957 |
| 3h | 0.788% | 0.811% | **0.972** | -0.004% | 50.5% | 0.803 |
| 6h | 1.168% | 1.196% | **0.977** | -0.015% | 49.9% | 0.490 |
| 12h | 1.672% | 1.798% | **0.930** | -0.032% | 50.9% | 0.317 |

**L_MFE / S_MFE = 0.93 ~ 0.99。Mean return = 0。Up% = 50%。**

**這是 random walk 的數學特徵。在非 breakout bar 上，ETH 價格等概率上漲或下跌，幅度相等。沒有任何信號、算法或方法論可以從 random walk 中提取方向性 alpha。**

---

## V19 最終結論

### 已嘗試的所有方向

| 方向 | 數據 | 結果 |
|------|------|------|
| Binance OI/LSR | 僅 30 天 | 數據不足，無法回測 |
| SPX/DXY/VIX/US10Y 宏觀 | 501 天日線 | 零預測力（所有 r < 0.08, p > 0.05） |
| Fear & Greed Index | 730 天日線 | 零預測力（Q5-Q1: +0.01%, p=0.98） |
| Deribit 選擇權 | 無數據 | 跳過 |
| HMM 3-state regime | Walk-forward | 所有 state 的 non-BRK fwd_1 = 0（p>0.7） |
| Return ACF | 17,520 bars | Random walk（VR≈1, ACF<0.02） |
| Sign 均值回歸 | 17,520 bars | r=-0.074 太小，覆蓋不了 fee |
| Momentum regime | Non-BRK | IS/OOS 全部巨虧 |
| Skewness regime | Non-BRK | IS/OOS 全部巨虧 |
| Consecutive patterns | Non-BRK | N<53, 噪音 |
| Vol compression direction | Non-BRK | |fwd| 正（vol 可預測）但方向隨機 |

### 累計研究規模

| 版本 | 範圍 | 配置數 | 方向 |
|------|------|--------|------|
| V6-V12 | OHLCV 技術指標 | 25,000+ | 單因子/多因子/形態/量價 |
| V13-V15 | 進場過濾/出場優化 | 1,000+ | GK 窗口/TP 優化/ATR gate |
| V16 | TBR Flow Reversal | 500+ | 量價流轉 |
| V17 | 非 breakout alpha (1h) | 572 | 均值回歸/rpos/形態/時段 |
| V18 | 非 breakout (15m/30m/1h) | 644 | 跨時框非 breakout |
| **V19** | **跨市場/情緒/HMM** | **50+ signals** | **宏觀/FGI/regime** |

### 根本原因

ETH 1h 報酬率的可預測結構只有兩個：

1. **波動率叢集**（squared ACF r=+0.11）：低波動之後傾向低波動，高波動之後傾向高波動。GK 壓縮指標利用了這一點。但波動率叢集不提供**方向**信息。

2. **微弱均值回歸**（sign ACF r=-0.074）：上漲之後有 52.4% 機率下跌（vs 50%）。但 2.4% 的額外機率在 fee ($4/$4K = 0.1%) 面前不夠 — 均值回歸的幅度太小。

**15-bar close breakout 之所以是唯一有效的信號，是因為它是 ETH 上唯一提供高置信度方向預測的事件。** Breakout 的 directional accuracy 遠高於 52.4%，足以覆蓋 fee 和 SafeNet 損失。沒有其他信號（無論來自 OHLCV、宏觀、情緒、或 HMM）能接近這個水準。

### 對 V14 策略的啟示

V14 (GK compression + 15-bar breakout + TP + MFE trail + Conditional MH) 是 **globally optimal** — 不是因為沒有更好的策略，而是因為在可取得的數據和約束條件下，breakout 以外不存在可交易的 alpha。

可能存在但無法驗證的 alpha 來源（需要付費數據或無法回測）：
- 歷史 OI/LSR（需 CoinGlass 付費 API）
- Deribit 選擇權 IV skew/GEX（需付費歷史數據）
- 鏈上數據（需 Glassnode/CryptoQuant 付費）
- L2 訂單簿微觀結構（需即時 WebSocket 快照，無歷史）

---

## Script Index

| Script | Description |
|--------|------------|
| `v19_r0_download_data.py` | Download external data (Binance OI, Yahoo Finance macro, FGI) |
| `v19_r1_macro_feasibility.py` | Macro + sentiment feasibility study (SPX/DXY/VIX/US10Y/FGI vs ETH) |
| `v19_r2_regime_detection.py` | Statistical structure (ACF, VR) + HMM 3-state regime detection |
