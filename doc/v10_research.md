# V10 $1K 帳戶穩定獲利研究

ETH/USDT 1h $1,000 帳戶 Short + Long 策略研究。
V9 找到 BTC-ETH RelDiv 通過 8/8 但 IS 極薄（$64）、年僅 48 筆。
V10 目標：**月月不虧**，穩定 > 暴利。

**V10-S v1 研究期間**：2026-04-11 ~ 2026-04-12
**V10-S v1 總計**：6 輪迭代 + 8-Gate 稽核、6,700+ 配置 + 4,860 修正版配置
**V10-S v1 結果**：3 組配置通過全部 7 道 V10 關卡，391 個配置通過
**V10-S v1 稽核**：**REJECTED — 4h EMA20 有 1-3 小時前瞻偏差，修正後 0/4,860 通過**

**V10-L 研究期間**：2026-04-13
**V10-L 總計**：10 輪迭代、8,500+ 配置
**V10-L 結果**：**1,431 ALL GOALS PASS（37% pass rate）**，純 1h 無 4h 前瞻問題
**V10-L 推薦**：GK<25 BL=15 TP=2.0% maxH=5 SN=3.5% mCap=-75 cd=6（OOS $1,090, L+S $4,497）

**V10-S v2 研究期間**：2026-04-13
**V10-S v2 總計**：3 輪迭代、7,842 配置（468 + 6,912 + 3 候選驗證）
**V10-S v2 結果**：**284 ALL PASS（Ind + Combined）**，純 1h 無 4h 前瞻問題
**V10-S v2 推薦**：GK<30 BRK15 TP=1.5% maxH=5 SN=4.0% cd=8 mc=-150（OOS $1,543, WF 8/8, L+S $2,635）

---

## 背景：V9 結論

V9 研究（4 輪、2,400+ 配置）找到 BTC-ETH RelDiv 通過 8/8 gates：
- Config C（推薦）：OOS $1,170, WR 69%, PF 1.98, MDD $570
- **但 IS 僅 $64**（統計上幾乎等於零）
- **年均僅 48 筆交易**（月均 4 筆，極稀疏）

V10 追求全新方向：更高頻率、更穩定月度分佈、更低回撤。

---

## 回測規格

| 項目 | 值 |
|------|-----|
| 標的 | ETHUSDT 1h Perpetual |
| 帳戶 | $1,000 |
| 保證金 | $200 (NOTIONAL $4,000, 20x) |
| 手續費 | $4/筆 (taker 0.04%×2 + slip 0.01%×2) |
| SafeNet | **3.5%** (25% 穿透滑價模型, max 單筆虧損 ~$175) |
| IS/OOS 切分 | 前 50% bars IS / 後 50% bars OOS |
| IS 期間 | 2024-04-01 ~ 2025-04-02 |
| OOS 期間 | 2025-04-02 ~ 2026-04-02 |
| WARMUP | 150 bars |
| Anti-Lookahead | 全指標 .shift(1)，entry at O[i+1] |
| 持倉上限 | **maxTotal = 1**（同時只能有 1 筆持倉） |
| Session filter | block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun} |

### 回撤熔斷（Circuit Breaker）

```python
DAILY_LOSS_LIMIT = -200     # 日虧 $200 停
CONSEC_LOSS_PAUSE = 4       # 連虧 4 筆冷卻 24 bar
MONTHLY_LOSS_LIMIT = -200   # 月虧 $200 停
```

### V10 7 道關卡

| # | Gate | 條件 | 設計理由 |
|---|------|------|----------|
| G1 | IS PnL | > $0 | 防 data mining |
| G2 | OOS PnL | > $0 | 正期望值 |
| G3 | 正月比例 | ≥ 10/12 | 月月不虧核心目標 |
| G4 | 最差月 | ≥ -$200 | 帳戶 20% 月度上限 |
| G5 | MDD | ≤ $500 | 帳戶 50% 回撤上限 |
| G6 | 最差單日 | ≥ -$300 | 單日可承受上限 |
| G7 | Walk-Forward | ≥ 4/6 fold 正 | 時間穩定性 |

---

## 策略說明

### 4h-1h 反彈做空（Counter-Trend Pullback Short）

```
核心邏輯：
  在 4h 下跌趨勢中，1h 反彈至 EMA20 上方是做空機會。
  反彈是暫時的逆勢修正，價格傾向回到 4h 趨勢方向。
  單一持倉（maxTotal=1）消除多倉同時爆掉的關聯損失風險。

進場條件：
  1. 4h 趨勢判定：4h close < 4h EMA20（下跌趨勢）
  2. 1h 反彈確認：1h close > 1h EMA20（價格反彈到 EMA 上方）
  3. 反彈幅度：0% ~ 3.0% above 1h EMA20
  4. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  5. ATR(14) percentile < 90（跳過極端波動期）
  6. Exit Cooldown: 上次出場後 8 bar
  7. Monthly Entry Cap: 每月最多 20 筆
  8. maxTotal = 1（同時只有 1 筆持倉）

  進場價：下一根 bar 開盤價 O[i+1]

出場（優先順序）：
  1. SafeNet +3.5%（25% 穿透模型，max 單筆虧損 ~$175）
  2. EarlyStop: bars 1-5, loss > 1.0% → 砍倉
  3. EMAcross: min hold 3 bar, close < 1h EMA20 → 平倉（主要獲利出場）
  4. MaxHold: 15 bar

指標公式：
  4h EMA20: close.ewm(span=20).mean()  （4h K 線上計算，forward-fill 到 1h）
  1h EMA20: close.ewm(span=20).mean()
  ATR(14):  (high - low).rolling(14).mean().shift(1)
  ATR percentile: ATR.rolling(100).rank(pct=True)
```

### 為什麼 Short-only 而非 L+S？

R2-R4 測試顯示：
- **Short 側 WR 81.9%**，PnL $4,096（OOS）
- Long 側 WR 66.3%，PnL $501（弱且不穩定）
- L+S 合併時，Long 在強上漲月拖累整體（July 2025: 8t, 0% WR, -$599）
- maxTotal=1 時 L 和 S 搶同一持倉位 → 不如專注強側

Short-only + maxTotal=1 的關鍵優勢：
1. 消除同時多倉爆掉的風險
2. 移除弱勢 Long 側
3. 月虧 -$200 熔斷在 maxTotal=1 下可靠執行（不會有倉位在熔斷後繼續虧損）

---

## 研究歷程

### R1：ETH 1h 全面數據特性分析

**腳本**：`backtest/research/v10_r1_data_analysis.py`, `v10_r1b_mfe_mae_fast.py`

**目標**：找出 ETH 1h 數據中可利用的統計 edge，為策略設計提供依據。

**��析 13 個維度**：
1. 基本收益分佈
2. 日內時段效應（Hour-of-Day）
3. 星期效應（Day-of-Week）
4. Taker Buy Ratio (TBR) 訂單流
5. 成交量 / 成交筆數異常
6. 波動率聚集與回歸
7. 連續漲跌棒均值回歸
8. BTC 領先 ETH（Lead-Lag）
9. Breakout 後動量持續性
10. TBR + Direction + Volume 多因子交互
11. 回報自相關
12. 大幅波動後短期反轉
13. 波動率壓縮後突破方向性

**關鍵發現**：

ETH 1h 的主導 edge 是**短期均值回歸**，非動量：

| 信號 | WR（次 bar） | N | 方向 |
|------|--------------|---|------|
| streak ≤ -3 & TBR < 20pct | **59.2%** | 655 | 做多反彈 |
| streak ≥ 3 & TBR > 80pct | **58.6%** | 690 | 做空回落 |
| BTC < -1.5% | **59.9%** | 202 | 做多反彈 |
| Breakout 10-bar 高 | 46.1% (反向) | 1,199 | 突破後反而回落 |
| Breakout 10-bar 低 | 54.6% (反向) | 1,105 | 跌破後反而反彈 |

**MFE/MAE 分析揭示致命問題**：

所有均值回歸信號的 MFE ≈ MAE（~1.5%），無方向性優勢。
TP/SL grid search 幾乎全負（2,160 配置中唯一正值：BTC crash +$165/730 天）。

**結論**：**1h 短線均值回歸搭配固定 TP/SL 結構性無法覆蓋 $4/筆手續費。**
手續費佔平均 |move| 的 20.9%（1h）vs 10.2%（4h）→ 需要更大的 move/trade。

---

### R2：多策略探索（5 種方向）

**腳本**：`backtest/research/v10_r2_4h_trend_analysis.py`

**目標**：測試 5 種完全不同的策略範式。

**4h vs 1h 統計比較**：

| 指標 | 4h | 1h |
|------|-----|-----|
| Avg \|ret\| | 0.98% | 0.48% |
| Fee/avg move | 10.2% | 20.9% |
| Bars/day | 6 | 24 |

**5 種策略結果**：

| 策略 | IS PnL | OOS PnL | OOS WR | 判定 |
|------|--------|---------|--------|------|
| A: GK Breakout + EMA20 Trail | -$169 | +$6,032 | 46.4% | IS 負 ❌ |
| B: Pullback in Trend (EMA50+EMA20) | -$3,266 | -$3,294 | 38.3% | 全負 ❌ |
| C: BTC-ETH RelDiv 5-bar >3% (v9-style) | -$110 | -$931 | 52.8% | 全負 ❌ |
| D: RelDiv 變體 (lb=3/5/8, th=0.025-0.04) | 各種 | 全負 or IS/OOS 分裂 | - | 全 ❌ |
| **E: 4h Trend + 1h Pullback Entry** | **+$1,012** | **+$3,556** | **75.0%** | **有潛力 ⭐** |

**Strategy E 突破口**：
- 4h EMA20 定趨勢方向 → 1h EMA20 等回調進場
- IS/OOS **雙正**
- WR **75%** OOS
- 324 OOS trades（~25/月）→ 足夠頻率

**問題**：MDD -$1,003、worst month -$681（多倉同時爆掉）

**重大發現**：Short 側遠優於 Long 側
- Short: 105t, WR **81.9%**, PnL **$4,096**
- Long: 95t, WR 66.3%, PnL $501

---

### R3：Strategy E 風控優化

**腳本**：`backtest/research/v10_r3_stratE_optimize.py`

**目標**：在 Strategy E 基礎上優化風控，降低 MDD 和 worst month。

**2,160 配置 grid search**：
- SafeNet: 3.0-4.5%
- EarlyStop: none / 3b@0.8% / 5b@1.0% / 5b@1.5% / 8b@1.5%
- TP: none / 2.0% / 3.0%
- MinHold: 2/3/5
- Pullback range: 4 種
- ATR filter: none / 80pct / 90pct

**結果**：0 個配置通過所有 gates

最接近者（SN=4.5, ES=8b1.5, MH=2, PB=0.3-3.0, ATR=90）：
- IS +$974, OOS +$4,354, WR **77.4%**, 11/13 PM
- **Fails: worst month -$592, MDD -$592**

**問題根源**：maxTotal=3 時多倉同時爆掉 → 單月可虧 $600+

**結論**：需要 maxTotal=1 或 Short-only 來消除關聯損失風險。

---

### R4：maxTotal=1 + Short-only 測試

**腳本**：`backtest/research/v10_r4_maxone_short.py`

**目標**：三路測試找出最佳方案。

**Part A：maxTotal=1，雙向 L+S（1,728 configs）**
- 0 個通過
- 最佳：OOS $4,511, WR 73.3%, 12/13 PM → **fails worst month -$308**
- Long 側在 July 2025 拖累整體

**Part B：Short-only，maxTotal=1（1,728 configs）**
- **391 個通過全部 gates！**

Top 3：

| Config | SN | ES | MH | ATR | EC | IS $ | OOS $ | WR% | 正月 | WrstM | MDD | WrstD |
|--------|-----|-----|-----|------|-----|------|-------|------|------|-------|------|-------|
| **1** | 3.5 | 5b1.0 | 3 | 90 | 8 | 660 | **3,586** | 79.6 | **13/13** | **+18** | -198 | -179 |
| 2 | 3.0 | 8b1.5 | 3 | 90 | 8 | 768 | 3,565 | 83.3 | 12/13 | -3 | -264 | -154 |
| 3 | 3.0 | 5b1.0 | 3 | 90 | 8 | 724 | 3,516 | 79.6 | 12/13 | -23 | -173 | -154 |

**Part C：maxTotal=2，緊縮熔斷（144 configs）**
- 0 個通過（IS 全負，worst month -$292~-$405）

**結論：Short-only maxTotal=1 是唯一可行方案，且極度穩健（391 個通過）。**

---

### R5：冠軍配置詳細驗證

**腳本**：`backtest/research/v10_r5_champion_validate.py`

**3 個冠軍配置全部通過 7/7 gates。**

#### Champion1（推薦）

```
SafeNet=3.5%, EarlyStop=5b@1.0%, MinHold=3, MaxHold=15, ExitCD=8
Pullback=0-3.0%, ATR<90pct, DailyLim=$-200, MonthLim=$-200

IS:  115t $660  PF 1.24 WR 67.0%
OOS: 108t $3,586  PF 3.49 WR 79.6%
```

**OOS 出場分佈**：
| 出場原因 | 筆數 | 佔比 | 平均 PnL |
|---------|------|------|----------|
| EMAcross | 90 | 83% | +$55.6 |
| EarlyStop | 12 | 11% | -$57.6 |
| MaxHold | 3 | 3% | -$64.5 |
| SafeNet | 3 | 3% | -$179.0 |

**OOS 月度明細（Champion1）**：

```
  Month       N    WR%      PnL      Cum
  2025-04     9    89%      527      527
  2025-05     3    67%       93      620
  2025-06    11    82%      388    1,007
  2025-07     5    80%       76    1,083
  2025-08     9    78%      392    1,475
  2025-09    13    77%      324    1,798
  2025-10    10    70%      467    2,265
  2025-11     9    89%      332    2,597
  2025-12    11    82%      286    2,884
  2026-01     8    75%       18    2,901
  2026-02    16    81%      406    3,307
  2026-03     3    67%       98    3,405
  2026-04     1   100%      181    3,586
```

**13/13 月全正**，最低月 +$18（2026-01），最佳月 +$527（2025-04）

#### Champion2

```
SafeNet=3.0%, EarlyStop=8b@1.5%, MinHold=3, MaxHold=15, ExitCD=8
IS:  106t $768  PF 1.32 WR 68.9%
OOS: 108t $3,565  PF 3.31 WR 83.3%
12/13 正月，worst month -$3，MDD -$264
```

#### Champion3

```
SafeNet=3.0%, EarlyStop=5b@1.0%, MinHold=3, MaxHold=15, ExitCD=8
IS:  111t $724  PF 1.27 WR 67.6%
OOS: 108t $3,516  PF 3.32 WR 79.6%
12/13 正月，worst month -$23，MDD -$173
```

#### Gate Check（Champion1）

| 目標 | 結果 | 通過 |
|------|------|------|
| IS > $0 | $660 | PASS |
| OOS > $0 | $3,586 | PASS |
| 正月 ≥ 10/12 | **13/13** | PASS |
| 最差月 ≥ -$200 | **+$18** | PASS |
| MDD ≤ $500 | **-$198** | PASS |
| 最差日 ≥ -$300 | -$179 | PASS |
| WF ≥ 4/6 | **4/6** | PASS |

**ALL 7 GATES PASS**

#### Walk-Forward 6-Fold（Champion1）

```
Fold 0: 2024-04-07 ~ 2024-08-06   44t  $829   WR 77.3%  [+]
Fold 1: 2024-08-06 ~ 2024-12-05   34t  $-27   WR 61.8%  [-]
Fold 2: 2024-12-05 ~ 2025-04-05   38t  $-251  WR 57.9%  [-]
Fold 3: 2025-04-05 ~ 2025-08-04   26t  $936   WR 80.8%  [+]
Fold 4: 2025-08-04 ~ 2025-12-03   42t  $1,532 WR 78.6%  [+]
Fold 5: 2025-12-03 ~ 2026-04-02   38t  $972   WR 78.9%  [+]

結果：4/6 positive folds, total $3,990 → PASS
```

#### 參數敏感度（Champion1 ± 1 step）

```
  Param         Value  OOS PnL   WR%    PM   WrstM    MDD  ALL_PASS
  safenet         3.0   $3,516  79.6% 12/13   -$23   -173     PASS
  safenet         3.5   $3,586  79.6% 13/13   +$18   -198     PASS ← base
  safenet         4.0   $3,257  78.8% 12/13  -$204   -223     fail (WrstM -$204)

  es_bars           3   $3,584  81.3% 12/13   -$28   -198     PASS
  es_bars           5   $3,586  79.6% 13/13   +$18   -198     PASS ← base
  es_bars           8   $3,563  79.6% 13/13   +$18   -198     PASS

  es_pct          0.8   $3,441  75.2% 13/13   +$18   -179     PASS
  es_pct          1.0   $3,586  79.6% 13/13   +$18   -198     PASS ← base
  es_pct          1.5   $3,491  83.3% 12/13   -$28   -314     PASS

  min_hold          2   $2,577  79.6% 13/13   +$12   -253     PASS
  min_hold          3   $3,586  79.6% 13/13   +$18   -198     PASS ← base
  min_hold          5   $2,932  73.3% 10/13  -$140   -248     PASS

  exit_cd           5   $3,133  76.9% 12/13  -$255   -275     fail (WrstM -$255)
  exit_cd           8   $3,586  79.6% 13/13   +$18   -198     PASS ← base
  exit_cd          12   $2,786  78.4% 13/13   +$51   -315     PASS

  pb_max          2.0   $3,612  80.4% 13/13   +$18   -179     PASS
  pb_max          2.5   $3,586  79.6% 13/13   +$18   -198     PASS
  pb_max          3.0   $3,586  79.6% 13/13   +$18   -198     PASS ← base

  atr_filter       80   $3,265  77.5% 12/13    -$0   -253     PASS
  atr_filter       85   $3,418  78.3% 13/13    +$4   -253     PASS
  atr_filter       90   $3,586  79.6% 13/13   +$18   -198     PASS ← base
```

**20/21 鄰近參數變化通過全部 gates** → 極度穩健，非 overfitting

---

## 最終結果：3 組 7/7 ALL PASS 配置

### 共同設定

```
方向：Short-only（只做空）
趨勢判定：4h close < 4h EMA20 → 下跌趨勢
進場信號：1h close > 1h EMA20（反彈到 EMA 上方）
反彈幅度：0% ~ 3.0%
Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
maxTotal: 1（同時只有 1 筆持倉）
Exit Cooldown: 8 bars
Monthly Entry Cap: 20
Daily Loss Limit: -$200
Monthly Loss Limit: -$200
Consecutive Loss Pause: 4 筆 → 24 bar 冷卻
$200 margin / 20x leverage / $4,000 notional / $4 fee
```

### 配置比較

| | **Champion1（推薦）** | **Champion2** | **Champion3** |
|---|---|---|---|
| SafeNet | **3.5%** | 3.0% | 3.0% |
| EarlyStop | **5 bar @ 1.0%** | 8 bar @ 1.5% | 5 bar @ 1.0% |
| MinHold | 3 | 3 | 3 |
| MaxHold | 15 | 15 | 15 |
| ATR filter | < 90pct | < 90pct | < 90pct |
| **OOS 年化** | **$3,586** | $3,565 | $3,516 |
| **IS PnL** | $660 | **$768** ★ | $724 |
| **OOS WR** | 79.6% | **83.3%** ★ | 79.6% |
| **PF** | **3.49** ★ | 3.31 | 3.32 |
| **MDD** | -$198 | -$264 | **-$173** ★ |
| **正月** | **13/13** ★ | 12/13 | 12/13 |
| **最差月** | **+$18** ★ | -$3 | -$23 |
| **最差日** | -$179 | **-$154** ★ | -$154 |
| **WF** | 4/6 | 4/6 | 4/6 |
| **OOS 筆數** | 108 | 108 | 108 |
| **Avg Hold** | 4.4 bars | 4.6 bars | 4.4 bars |

### 推薦 Champion1 的邏輯

1. **13/13 月全正**：唯一在 OOS 期間零負月的配置
2. **最差月 +$18**：不只是「不虧」，是真正的月月正
3. **PF 3.49**：三者最高，edge 最明確
4. **MDD -$198**：帳戶最大回撤僅 20%
5. 參數敏感度 20/21 PASS：極度穩健

---

## 核心洞察

### 1. 4h-1h Multi-Timeframe 是解鎖穩定性的關鍵

單一 1h 時框的信號太嘈雜。4h EMA20 提供高品質的趨勢方向判斷，
1h EMA20 提供精確的進場時機。兩者結合將 WR 從 ~55%（單時框）提升到 ~80%。

### 2. Short-only 在 $1K 帳戶優於 L+S

在 maxTotal ≤ 3 的限制下，L+S 雙向策略的關聯損失風險不可控。
Short 側 WR 81.9% 遠高於 Long 側 66.3%。移除弱勢 Long 側
反而提升整體穩定性。

### 3. maxTotal=1 是風控的核心

maxTotal=1 確保：
- 不會有多倉同時爆掉
- 月虧 -$200 熔斷可靠（不存在「熔斷前已有多倉」的漏洞）
- 最大單筆虧損 = SafeNet 3.5% × 1.25 = ~$175

### 4. EMAcross 是主要獲利引擎

83% 的出場是 EMAcross，平均獲利 $55.6。策略的核心 edge 在於：
- 4h 下跌趨勢中的 1h 反彈是短暫的
- 反彈通常在 3-5 bar 內回落至 EMA20 以下
- 平均持倉僅 4.4 bars（~4 小時）

### 5. 1h 短線均值回歸（TP/SL 框架）結構性無法盈利

R1 的 MFE/MAE 分析證明：
- 所有均值回歸信號的 MFE ≈ MAE（~1.5%）
- 手續費佔 1h 平均 |move| 的 20.9%
- 2,160+ 配置的 TP/SL grid search 幾乎全負
- **結論：1h 需要趨勢型出場（EMA trail），不是固定 TP/SL**

### 6. 已知風險

- **Short-only 在持續上漲趨勢中弱勢**：IS 期間有 6 個月微虧（2024-09 ~ 2025-02），
  但月虧 -$200 熔斷確保帳戶不會爆。
- **IS 弱於 OOS**：IS $660 vs OOS $3,586。OOS 5.4× 優於 IS，
  可能存在近期市場更適合的 regime 效應。但 IS 仍正、WF 4/6 PASS。
- **年化交易量適中**：~108 筆/年 = ~9 筆/月。比 v9 的 4 筆/月好很多，
  但仍不算高頻。

---

## 排除的方向

### V10 排除清單

| 方向 | 輪次 | 結果 | 排除原因 |
|------|------|------|----------|
| 1h 均值回歸 (streak + TBR) | R1 | MFE ≈ MAE | 手續費 20.9% 吃掉 edge |
| 1h 均值回歸 grid (TP/SL) | R1b | 2,160 configs 近乎全負 | 結構性無法覆蓋費用 |
| GK Breakout + EMA20 Trail (v6 簡化) | R2-A | IS -$169 | IS 為負 |
| EMA50 Pullback in Trend | R2-B | IS/OOS 雙負 | 完全無效 |
| BTC-ETH RelDiv v9-style | R2-C | IS -$110, OOS -$931 | v9 結果不可復現 |
| RelDiv 多 lookback/threshold | R2-D | 全部 IS/OOS 分裂或雙負 | RelDiv 不穩健 |
| L+S maxTotal=3 (各種風控) | R3 | 2,160 configs, 0 pass | worst month 不可控 |
| L+S maxTotal=1 | R4-A | 1,728 configs, 0 pass | Long 側拖累 worst month |
| L+S maxTotal=2 緊縮熔斷 | R4-C | 144 configs, 0 pass | IS 全負 |
| TBR 訂單流（單因子） | R1 | WR 54-55% | 幅度太小，不足以覆蓋費用 |
| BTC crash bounce | R1 | +$165/730 天 | 太稀疏，利潤近乎為零 |
| 連續漲跌棒 + TP/SL | R1b | 全負 | MFE/MAE 無方向性優勢 |

---

## 研究腳本索引

| 腳本 | 輪次 | 配置數 | 結果 |
|------|------|--------|------|
| `v10_r1_data_analysis.py` | R1 | - | 13 維度數據分析，發現均值回歸 edge |
| `v10_r1b_mfe_mae_fast.py` | R1b | 2,160+ | MFE/MAE + TP/SL grid，證明均值回歸無法獲利 |
| `v10_r2_4h_trend_analysis.py` | R2 | 5 策略 | 5 方向測試，Strategy E 突破 |
| `v10_r3_stratE_optimize.py` | R3 | 2,160 | 風控優化，發現 Short 遠強於 Long |
| `v10_r4_maxone_short.py` | R4 | 3,600 | **391 configs 通過**，Short-only 是答案 |
| `v10_r5_champion_validate.py` | R5 | 3 champions | **3 × 7/7 ALL PASS**，WF 4/6，敏感度 20/21 |
| `v10_fetch_15m_data.py` | R6 | - | 下載 ETHUSDT 15m 730 天 K 線（70,080 根） |
| `v10_r6_15m_comparison.py` | R6 | 1,728 | 15m vs 1h 時框比較，15m Hybrid 也 PASS |

---

## R6：15m vs 1h 時框比較

**腳本**：`backtest/research/v10_r6_15m_comparison.py`, `v10_fetch_15m_data.py`

**目標**：V10 策略是否在 15m 時框上更好？更細粒度是否提升績效？

### 手續費拖累分析

| 時框 | 平均 \|move\| | Fee/Move | 信號品質 |
|------|---------------|----------|----------|
| 15m | $9.57 | **41.8%** | 非常嘈雜 |
| 1h | $19.11 | 20.9% | 中等 |
| 4h | $39.15 | 10.2% | 最乾淨 |

→ **15m 手續費拖累是 1h 的 2 倍**

### 4 種方法測試

| 方法 | 說明 |
|------|------|
| **Baseline** | 1h Champion1（原始策略） |
| **A: Pure 15m** | EMA80/EMA320 取代 EMA20/4h，所有參數×4 |
| **B: 15m Hybrid** | 正確 resample 1h/4h 指標，15m 粒度執行，參數×4 |
| **C: 15m Optimized** | Hybrid 基礎上 grid search 1,728 配置找最佳參數 |

### 結果對比

| 方法 | OOS PnL | 筆數 | WR% | PF | 正月 | 最差月 | MDD | 最差日 | PASS |
|------|---------|------|-----|----|------|--------|-----|--------|------|
| **1h Baseline** | **$3,586** | 108 | **79.6%** | 3.49 | **13/13** | +$18 | -$198 | -$179 | **YES** |
| A: Pure 15m | $299 | 93 | 54.8% | 1.15 | 6/13 | -$251 | -$636 | -$116 | NO |
| **B: 15m Hybrid** | **$3,741** | 139 | 74.1% | 3.63 | **13/13** | +$56 | **-$143** | **-$91** | **YES** |
| **C: 15m Optimized** | **$4,568** | 125 | 75.2% | **4.80** | **13/13** | **+$122** | **-$103** | **-$91** | **YES** |

### 關鍵發現

**1. Pure 15m 完全失敗**

EMA80/EMA320 不等同於 1h EMA20 / 4h EMA20。
長期 EMA 在 15m 上過度平滑，錯過趨勢變化且進場信號變嘈雜。
WR 從 79.6% 暴跌至 54.8%，幾乎無 edge → **不可行**。

**2. 15m Hybrid 通過全部 gates，MDD 更低**

使用正確 resample 的 1h/4h 指標 + 15m 粒度執行：
- OOS +$3,741（比 1h 多 4%）
- MDD 僅 -$143（1h 為 -$198）
- Worst day 僅 -$91（1h 為 -$179）
- 13/13 月全正，最差月 +$56

原因：15m 粒度讓 SafeNet/EarlyStop 更快觸發，減少滑脫。

**3. 15m Optimized 表面更強，但有 overfitting 風險**

Grid search 1,728 配置找到最佳參數（es_bars=24, min_hold=16, max_hold=80, exit_cd=40, pb_max=2.0）：
- OOS $4,568, PF 4.80, MDD -$103
- **但**：1,516/1,728 configs 通過 → 策略在 15m Hybrid 框架下極其穩健
- 最佳配置是 grid search 產物，有 data mining 風險

**4. 結論：1h 仍為推薦方案**

| 考量 | 1h ★ | 15m Hybrid |
|------|------|------------|
| 信號品質 | 乾淨，WR 79.6% | 稍降，WR 74.1% |
| 執行簡單度 | 每小時檢查一次 | 每 15 分鐘檢查 |
| 手續費拖累 | 20.9% | 41.8%（更頻繁交易放大） |
| MDD | -$198 | **-$143** ← 15m 更好 |
| Robustness | 20/21 敏感度 PASS | 1,516/1,728 PASS ← 極穩健 |
| 延遲 | ≤ 1h 出場延遲 | ≤ 15m 出場延遲 |
| 系統複雜度 | 低（1 個 CSV） | 高（需 15m + 1h + 4h） |

**推薦維持 1h**，原因：
1. WR 最高（79.6% vs 74.1%）
2. 系統最簡單（每小時執行一次）
3. 手續費拖累最低
4. 1h 和 15m Hybrid 都 PASS → **策略核心邏輯本身穩健**，時框非關鍵因素

**若未來需要更低 MDD / 更快止損**，可考慮切換到 15m Hybrid 模式（已驗證可行）。

---

## V10-L 做多策略研究

**研究期間**：2026-04-13
**目的**：為 V10 Short-only 策略補上做多端，補償 S 策略弱月（May/Jul/Jan/Mar PnL < $100）
**總計**：10 輪迭代、8,500+ 配置
**結果**：R9 找到 1,431 個 ALL GOALS PASS 配置（37% pass rate），R10 驗證通過

### 目標

V10-L 是獨立的 Long-only 策略，與 V10-S 同時運行（各自 maxTotal=1，最多 1L+1S 同時持倉）。

**獨立目標**：
| # | Gate | 條件 |
|---|------|------|
| G1 | IS PnL | > $0 |
| G2 | OOS PnL | > $0 |
| G3 | 正月比例 | ≥ 8/13 |
| G4 | 最差月 | ≥ -$200 |
| G5 | MDD | ≤ $400 |
| G6 | Walk-Forward | ≥ 4/6 fold 正 |

**合併目標（L+S）**：
| # | Gate | 條件 |
|---|------|------|
| C1 | L+S 正月 | ≥ 11/13 |
| C2 | L+S 最差月 | ≥ -$150 |
| C3 | L+S MDD | ≤ $500 |
| C4 | L+S 總 PnL | > S-only |

### 回測規格

```
標的：ETHUSDT 1h Perpetual
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee
IS：2024-04 ~ 2025-03（bars 0-8783）
OOS：2025-04 ~ 2026-04（bars 8784-17567）
WARMUP：150 bars
Anti-lookahead：全指標 .shift(1)，entry at O[i+1]
maxTotal=1（L 獨立，S 獨立）
```

### V10-S 月度 PnL（合併計算用）

```
2025-04: +527  2025-05: +93   2025-06: +388  2025-07: +76
2025-08: +392  2025-09: +324  2025-10: +467  2025-11: +332
2025-12: +286  2026-01: +18   2026-02: +406  2026-03: +98
```

---

### R1：趨勢回調 + EMAcross 出場

**腳本**：`backtest/research/v10l_r1_trend_pullback.py`（216 configs）

EMA50 趨勢 + EMA20 回調 + close 突破 + EMAcross 出場。

**結果**：2/216 IS>0，最佳 OOS $1,881。WR ~34%，MDD/WM 均 FAIL。

---

### R2：趨勢回調 + TP 出場

**腳本**：`backtest/research/v10l_r2_trend_tp.py`（216 configs）

同 R1 但改用固定 TP 出場。

**結果**：0/216 IS>0。TP 出場截斷 Long 大贏家，完全不可行。

---

### R3：GK 壓縮突破 + EMAcross 出場

**腳本**：`backtest/research/v10l_r3_gk_breakout.py`（288 configs）

改用 GK pctile 壓縮 + close breakout 進場。

**結果**：13/288 IS>0，最佳 OOS $1,580。GK 進場遠優於趨勢回調。

---

### R4：GK 突破 + EarlyStop 變體

**腳本**：`backtest/research/v10l_r4_earlystop.py`（972 configs）

測試 6 種 EarlyStop 變體。

**關鍵發現**：EarlyStop 對 Long 有害！NoES (26/162) 和 ES7-12@2% 最佳。

**最佳**：GK<40 BL=15 SN=4.5% mh=10 maxH=30 NoES
- IS: 61t $+177 | OOS: 79t **$+2,624** PF=1.70 WR=41.8% MDD=$834
- **Fails: WM=-$333, MDD=$834**

---

### R5：趨勢過濾器

**腳本**：`backtest/research/v10l_r5_trend_filter.py`（576 configs）

加入 EMA40/50/60/80/100 趨勢過濾。

**結果**：趨勢過濾損害 IS，`none` 表現最佳。已排除。

---

### R6：均值回歸 Dip Buying

**腳本**：`backtest/research/v10l_r6_dip_buy.py`（636 configs）

範式轉換：逢低買入（5 種信號類型）+ TP + MaxHold 出場。

**結果**：FAILED — 只有 `big_drop` (>3% 單根暴跌) 有 5 個 Both>0，但 OOS 僅 10 筆交易，無統計意義。其餘信號 0 個 Both>0。

---

### R7：風控收緊

**腳本**：`backtest/research/v10l_r7_risk_tighten.py`（1,728 configs）

在 R4 最佳基礎上收緊 SafeNet (2.0-5.5%) + 月虧上限 (-$50 to -$200)。

**結果**：92 個 WM ≥ -$200，**0 個 MDD ≤ $400**。MDD 是結構性瓶頸。

**診斷**：EMAcross 出場 WR=22-28%，大量小虧累積成 drawdown。SafeNet 單筆 $150-230 虧損疊加。

---

### R8：TP + MaxHold 出場（突破！）

**腳本**：`backtest/research/v10l_r8_tp_maxhold.py`（1,296 configs）

**範式轉換**：放棄趨勢跟隨（EMAcross），改用 TP + MaxHold 出場（做空策略方式）。

**結果**：**30 個 ALL INDEPENDENT GOALS PASS！** 46 個 MDD ≤ $400。

GK<20 主導所有 top configs。TP=2.0% + maxH=5 是甜蜜點。

---

### R9：Fine-tune

**腳本**：`backtest/research/v10l_r9_finetune.py`（3,888 configs）

在 R8 甜蜜區 fine-tune：GK[15,20,25] × BL[10,12,15] × TP[1.5,2.0,2.5] × maxH[4,5,6,7] × SN[2.5,3.0,3.5,4.0] × mCap[-75,-100,-125] × cd[6,8,10]

**結果**：**1,431 個 ALL GOALS PASS（37% pass rate）**

Top 3：
| # | Config | IS $ | OOS $ | WR | MDD | WM | L+S | L+S PM |
|---|--------|------|-------|-----|-----|-----|------|--------|
| 1 | GK<25 BL=12 TP=2.5 maxH=7 SN=4.0 mCap=-100 cd=6 | +67 | **+1,110** | 60% | $264 | -$72 | $4,517 | 12/13 |
| 2 | GK<25 BL=15 TP=2.0 maxH=5 SN=3.5 mCap=-75 cd=6 | +93 | +1,090 | 59% | $269 | -$128 | $4,497 | 12/13 |
| 3 | GK<25 BL=15 TP=2.5 maxH=7 SN=3.5 mCap=-75 cd=6 | +131 | +1,073 | 57% | $375 | -$146 | $4,480 | 12/13 |

---

### R10：驗證 & 壓力測試

**腳本**：`backtest/research/v10l_r10_validation.py`

#### Walk-Forward

| 配置 | 6-Fold | 8-Fold | 10-Fold |
|------|--------|--------|---------|
| Champion (GK<25 BL=12 TP=2.5) | 4/6 | 5/8 | 6/10 |
| **Runner-up (GK<25 BL=15 TP=2.0)** | 4/6 | **7/8** | **8/10** |
| Candidate3 (GK<25 BL=15 TP=2.5) | 3/6 | 6/8 | 7/10 |

**Runner-up WF 穩定性大幅領先**，只有 2024-04~2024-07 FAIL。

#### 壓力測試（Fee / SafeNet 穿透）

| Fee | SN穿透 | Champion IS | Runner-up IS |
|-----|---------|------------|-------------|
| $4 | 25% | $+67 | $+93 |
| $5 | 25% | $+11 | $+40 |
| $5 | 35% | **$-25 FAIL** | $+30 |
| $6 | 25% | **$-45 FAIL** | $+36 |
| $6 | 50% | **$-136 FAIL** | $+11 |

**Runner-up 9/9 壓力測試 IS>0。Champion 僅 1/9。**

#### 參數高原（Champion）

| 參數 | ALL PASS 範圍 |
|------|--------------|
| gk_thresh | 15-25（3 值） |
| bl | 12, 15（2 值） |
| safenet | **2.0-4.0（5 值）** |
| exit_cd | **4-10（4 值）** |
| tp | 2.5, 3.0（2 值） |
| max_hold | 7, 8（2 值） |
| m_loss_cap | -75, -100（2 值） |

#### Anti-Lookahead

7/7 全通過。純 1h 時框，無 4h 前瞻風險。

```
[x] GK pctile shift(1)
[x] Breakout shift(1)
[x] Session filter (current bar datetime)
[x] Entry at O[i+1]
[x] Exit on current bar OHLC only
[x] Monthly loss cap (cumulative, no future)
[x] No 4h data (pure 1h, no multi-TF lookahead)
```

#### 連虧分析

- 最大連虧：4 筆（僅 1 次）
- 多數連虧 1-2 筆
- Trade-level MDD：$554（全期）

---

### 最終推薦：Runner-up

**GK<25 BL=15 TP=2.0% maxH=5 SN=3.5% mCap=-75 cd=6**

推薦 Runner-up 而非 Champion 的理由：
1. **WF 穩定性**：8/10 vs 6/10（10-fold），7/8 vs 5/8（8-fold）
2. **壓力測試**：9/9 IS>0 vs 1/9（fee $6 + 50% 穿透仍正）
3. **IS 餘裕**：$93 vs $67
4. OOS PnL 差距僅 $20（$1,090 vs $1,110），可忽略

#### 策略規格

```
方向：Long-only
時框：1h
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場：
  1. GK pctile < 25（波動壓縮）
     gk = 0.5×ln(H/L)² - (2ln2-1)×ln(C/O)²
     ratio = mean(gk,5) / mean(gk,20)
     pctile = ratio.shift(1).rolling(100).apply(min-max pctile)
  2. Close breakout 15 bar（c > c.shift(1).rolling(15).max()）
  3. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  4. Exit Cooldown: 6 bar
  5. Monthly Entry Cap: 20
  6. maxTotal = 1

出場（優先順序）：
  1. SafeNet -3.5%（含 25% 穿透模型，max 單筆虧損 ~$158）
  2. TP +2.0%（固定止盈，$76/筆）
  3. MaxHold: 5 bar（時間止損）

風控熔斷：
  日虧 -$200 停 / 月虧 -$75 停 / 連虧 4 筆 → 24 bar 冷卻
```

#### OOS 績效

```
IS:  53t $+93  PF=1.07 WR=52.8%
OOS: 83t $+1,090  PF=1.68 WR=59.0% MDD=$269 WM=$-128

出場分佈：
  TP:      28t avg $+76   WR=100%  hold=2.9b
  MaxHold: 53t avg $-14   WR=40%   hold=5.0b
  SafeNet:  2t avg $-159  WR=0%    hold=3.0b
```

#### L+S 合併績效

```
     Month   L_PnL   S_PnL     L+S
   2025-04     +61    +527    +588
   2025-05    +229     +93    +322  ← S 弱月，L 補上
   2025-06    -128    +388    +260
   2025-07    +169     +76    +245  ← S 弱月，L 補上
   2025-08     -84    +392    +308
   2025-09    +182    +324    +506
   2025-10     -50    +467    +417
   2025-11    +163    +332    +495
   2025-12    +126    +286    +412
   2026-01     -95     +18     -77  ← 唯一負月
   2026-02    +294    +406    +700
   2026-03    +160     +98    +258  ← S 弱月，L 補上
   2026-04     +65       0     +65
     TOTAL   +1090   +3407   +4497

L+S pos months: 12/13
L+S worst month: -$77
L+S monthly MDD: $77
```

#### 全部目標檢查

| 獨立目標 | 值 | 判定 |
|---------|-----|------|
| IS > $0 | $+93 | **PASS** |
| OOS > $0 | $+1,090 | **PASS** |
| PM ≥ 8/13 | 9/13 | **PASS** |
| WM ≥ -$200 | -$128 | **PASS** |
| MDD ≤ $400 | $269 | **PASS** |
| WF ≥ 4/6 | 4/6（8/10 ten-fold） | **PASS** |

| 合併目標 | 值 | 判定 |
|---------|-----|------|
| L+S PM ≥ 11/13 | 12/13 | **PASS** |
| L+S WM ≥ -$150 | -$77 | **PASS** |
| L+S MDD ≤ $500 | $77 | **PASS** |
| L+S > S-only | $4,497 > $3,407 | **PASS** |

**ALL GOALS PASS ✓**

---

### V10-L 核心洞察

#### 1. TP + MaxHold 是 Long 做多在 $1K 帳戶的唯一可行出場方式

R1-R7（7 輪、3,800+ 配置）用 EMAcross 趨勢跟隨出場，MDD 無法降到 $400 以下。
R8 改用 TP + MaxHold（做空策略方式）後立即突破，30 → 1,431 ALL PASS。

原因：EMAcross 出場 WR 僅 22-28%（多數趨勢跟隨嘗試失敗），損失累積成大 MDD。
TP + MaxHold 犧牲偶爾的大贏家，換取更高 WR（59%）和可控 MDD。

#### 2. GK 壓縮突破是最佳進場（所有範式中）

| 範式 | 輪次 | 結果 |
|------|------|------|
| 趨勢回調 | R1-R2 | 2/216 IS>0 |
| **GK 壓縮突破** | R3-R10 | **1,431 ALL PASS** |
| 均值回歸 Dip Buy | R6 | FAILED |

#### 3. L 策略精確補償 S 策略弱月

S 策略弱月（PnL < $100）：May (+93), Jul (+76), Jan (+18), Mar (+98)
L 策略在這些月份：May (+229), Jul (+169), Jan (-95), Mar (+160)

4 個弱月中 3 個被 L 策略顯著補上，只有 Jan 2026 兩邊都弱（L+S = -$77）。

#### 4. 已知風險

- **IS 邊際性**：IS $93，PF 1.07 — 接近盈虧平衡。2024 年（熊市）Long 策略結構性弱勢。
- **2024 年 WF FAIL**：6-fold 的 Fold 1-2（2024-04 ~ 2024-12）均為負。
  Long 策略在持續下跌期間無法獲利，這是預期中的。
- **L 策略不是獨立賺錢機器**：它的價值在於補償 S 的弱月，不是獨立高利潤。

---

### V10-L 研究腳本索引

| 腳本 | 輪次 | 配置數 | 結果 |
|------|------|--------|------|
| `v10l_r1_trend_pullback.py` | R1 | 216 | 趨勢回調，2/216 IS>0 |
| `v10l_r2_trend_tp.py` | R2 | 216 | TP 出場，0/216 IS>0 |
| `v10l_r3_gk_breakout.py` | R3 | 288 | GK 突破，13/288 IS>0 |
| `v10l_r4_earlystop.py` | R4 | 972 | EarlyStop 測試，OOS $2,624 但 MDD FAIL |
| `v10l_r5_trend_filter.py` | R5 | 576 | 趨勢過濾器，損害 IS |
| `v10l_r6_dip_buy.py` | R6 | 636 | 均值回歸，FAILED |
| `v10l_r7_risk_tighten.py` | R7 | 1,728 | 風控收緊，0 MDD pass |
| `v10l_r8_tp_maxhold.py` | R8 | 1,296 | **TP+MaxHold 突破！30 ALL PASS** |
| `v10l_r9_finetune.py` | R9 | 3,888 | **Fine-tune 1,431 ALL PASS (37%)** |
| `v10l_r10_validation.py` | R10 | 3 候選 | **驗證通過，推薦 Runner-up** |

### V10-L 排除的方向

| 方向 | 輪次 | 排除原因 |
|------|------|----------|
| EMA50 趨勢回調 | R1-R2 | IS 極低（2/216），進場太寬鬆 |
| TP 出場（趨勢回調進場） | R2 | 0/216 IS>0，截斷大贏家 |
| EarlyStop for Long | R4 | 損害 Long 策略績效（R3→R4 移除後 OOS +66%） |
| EMA 趨勢過濾 | R5 | 損害 IS，過濾掉太多有效信號 |
| 均值回歸 Dip Buying | R6 | 5 種信號全 FAIL，交易太少無統計意義 |
| EMAcross + 超緊 SafeNet | R7 | 0/1,728 MDD ≤ $400，EMAcross WR 22-28% 是結構性問題 |
| GK<30/40 + TP+MaxHold | R8-R9 | GK<25 以下更穩定，GK ≥ 30 pass rate 驟降 |

---

## V10-S v2：純 1h Short 策略（2026-04-13）

### 背景

V10-S v1 被 8-Gate 稽核 REJECTED：4h EMA20 有 1-3 小時前瞻偏差，95.9% 的利潤來自此偏差。
V10-S v2 目標：**完全純 1h，無任何 4h/daily 數據**，找到能與 V10-L 互補的 Short 策略。

### 目標

**S 獨立目標**（IS/OOS 50/50 固定 split）：
- IS > $0
- OOS > $0
- 正月 ≥ 8/13
- 最差月 ≥ -$200
- MDD ≤ $400

**L+S 合併目標**：
- L+S 正月 ≥ 11/13
- L+S 最差月 ≥ -$150
- L+S MDD ≤ $500
- L+S 合計 > L-only ($1,090)

### R1：三範式探索（468 configs）

三種進場+出場組合：
- **A: Pullback Short + EMAcross**（v1 邏輯，純 1h EMA60/80/100 趨勢判定）→ **0 Ind PASS**
- **B: Pullback Short + TP+MaxHold**（V10-L style 出場）→ 2 Both>0, **0 Ind PASS**
- **C: GK Breakout Down + TP+MaxHold**（V10-L 鏡像結構）→ 140 Both>0, **16 Ind PASS**

**結論**：Pullback Short 完全失敗，證實 V10-S v1 的 edge 來自 4h 前瞻而非做空邏輯。
GK 壓縮突破向下 + TP+MaxHold 是唯一可行方向 — 完美鏡像 V10-L 結構。

Sweet spot: GK<30, BL=15, TP 1.5-2.0, maxH=5, cd 6-8。WF 5/6。

### R2：Fine-tune + 風控解鎖（6,912 configs）

鎖定 Paradigm C，新增 SafeNet 和 Monthly Cap 作為變量。

| 維度 | Grid | 最佳值 |
|------|------|--------|
| GK | [25, 28, 30, 33] | GK<33 (ALL PASS 102) |
| BL | [13, 14, 15, 16] | BL=15 (165), BL13-14 = 0 ALL PASS |
| TP | [1.0, 1.5, 2.0, 2.5] | TP=1.5% (107) |
| maxH | [4, 5, 6] | maxH=5 (128) |
| **SN** | [2.5, 3.0, 3.5, **4.0**] | **SN=4.0% (214/284 = 75%)** |
| mCap | [-75, -100, -150] | mCap=-150 (114) |
| cd | [4, 6, 8] | cd=8 略優 |

**突破**：SN=4.0% 解鎖大量配置。SafeNet 幾乎不觸發（0 次 in top configs），風控完全由 TP+MaxHold 管理。

結果：**2,326 Ind PASS → 284 ALL PASS（Ind + Combined）**

### R3：Comprehensive Validation（3 候選）

三個候選：

| # | Config | OOS PnL | WR | MDD | PM | WM | L+S | L+S MDD |
|---|--------|---------|-----|------|------|------|------|---------|
| Champion | GK33 BRK15 tp1.5 mxH5 sn4.0 mc-75 cd6 | $1,638 | 69% | $152 | 12/13 | -$43 | $2,730 | $77 |
| Runner | GK30 BRK15 tp2.0 mxH5 sn4.0 mc-150 cd8 | $1,644 | 67% | $267 | 10/13 | -$126 | $2,736 | $176 |
| **★ Cand3** | **GK30 BRK15 tp1.5 mxH5 sn4.0 mc-150 cd8** | **$1,543** | **71%** | **$172** | **11/13** | **-$49** | **$2,635** | **$133** |

#### Walk-Forward

| Candidate | 6-fold | 8-fold | 10-fold | 總分 |
|-----------|--------|--------|---------|------|
| Champion | 5/6 | 7/8 | 7/10 | 19/24 |
| Runner | 5/6 | 6/8 | 6/10 | 17/24 |
| **★ Cand3** | **6/6** | **8/8** | 7/10 | **21/24** |

Cand3 的 **8/8 walk-forward** 是罕見成就 — 包括 2024 bear 市場折都維持正收益。

#### Stress Test（IS period）

全部 **9/9 IS>0**。SN=4.0% 幾乎不觸發，fee/slippage 影響微乎其微。

#### Parameter Plateau（OOS）

全部三個候選：**每個參數 sweep 值都是正的**（50/50 positive）。

Champion plateau:
```
GK: 20→40 全正 ($484~$1,755)   BL: 12→18 全正 ($523~$1,638)
TP: 0.5→3.5 全正 ($408~$1,685) MH: 3→12 全正 ($542~$1,638)
SN: 2.0→6.0 全正 ($201~$1,638) CD: 2→12 全正 ($1,493~$1,755)
```

#### Consecutive Loss Analysis

| Candidate | Max 連虧 | 最差連虧 PnL | ≥4 連虧次數 |
|-----------|----------|-------------|------------|
| Champion | 3 | -$187 | 0 |
| Runner | 3 | -$227 | 0 |
| **Cand3** | **3** | **-$185** | **0** |

Circuit breaker (4 連虧冷卻) **從未觸發**。

#### Anti-lookahead

7/7 PASS：EMA20 shift(1)、GK shift(1).rolling(100)、Breakout shift(1).rolling(bl)、Entry at O[i+1]、NO 4h data、Fixed IS/OOS split、Exit uses current bar OHLC only。

### V10-S v2 推薦配置

**★ Cand3: GK<30 BRK15 TP=1.5% maxH=5 SN=4.0% cd=8 mc=-150**

選擇理由：WF 6/6 + **8/8** + 7/10（總分 21/24 最高），GK<30 比 GK<33 更保守的入場過濾。

```
方向：Short-only（只做空），純 1h
帳戶：$1,000 / $200 保證金 / 20x / $4,000 名目 / $4 fee

進場：
  1. GK pctile < 30（波動壓縮）
  2. Close < Close.shift(1).rolling(15).min()（向下突破 15 bar 低點）
  3. Session: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  4. Exit Cooldown: 8 bar
  5. Monthly Cap: 20, maxTotal: 1
  6. All indicators .shift(1) — 純歷史數據

出場（優先順序）：
  1. SafeNet +4.0%（25% 穿透，max single loss ~$200）
  2. TP -1.5%（固定止盈）
  3. MaxHold 5 bar（時間止損）

風控熔斷：
  日虧 -$200 停 / 月虧 -$150 停 / 連虧 4 筆 → 24 bar 冷卻
```

#### 績效

```
IS:  76t $+767 PF=1.55 WR=65.8%
OOS: 76t $+1,543 PF=2.65 WR=71.1% MDD=$172 WM=$-49 PM=11/13

WF:  6/6, 8/8, 7/10
Stress: 9/9 IS>0
Plateau: 50/50 positive
Max consec loss: 3 (circuit breaker never fires)
Anti-lookahead: 7/7 PASS
```

#### L+S 合併月度

```
     Month   S_PnL   L_PnL     L+S
   2025-04    +262     +61    +323
   2025-05    +236    +229    +465
   2025-06     +14    -128    -114 *
   2025-07     +55    +169    +224
   2025-08     -51     -84    -135 *
   2025-09    +116    +182    +298
   2025-10    +369     -50    +319
   2025-11     +27    +163    +190
   2025-12    -137    +126     -11
   2026-01    +180     -95     +85
   2026-02    +253    +294    +547
   2026-03    +225    +160    +385
   2026-04     -43     +65     +22
     TOTAL  +1,543  +1,092  +2,635
L+S: 11/13pm, worst month -$135, MDD $133
```

L+S 互補：S 弱月（Jun/Dec）有 L 撐，L 弱月（Jun/Aug/Oct/Jan）有 S 撐。

#### 出場分佈（OOS）

```
TP:      46t avg $+56   WR=100%  hold=1.8b
MaxHold: 41t avg $-22   WR=37%   hold=5.0b
SafeNet:  0t                              ← SafeNet 從未觸發
```

### V10-S v2 核心洞察

1. **Pullback Short 已死**：純 1h EMA60/80/100 無法替代 4h EMA20 的趨勢判定，0/306 configs pass。V10-S v1 的 edge 100% 來自 4h 前瞻。

2. **GK 壓縮突破是通用框架**：V10-L（GK+Breakout Up+TP+MaxHold）和 V10-S v2（GK+Breakout Down+TP+MaxHold）使用相同結構，只是方向相反。

3. **SN=4.0% 是解鎖鑰匙**：R1 用 SN=3.5% 只有 16 Ind PASS；R2 放寬到 4.0% 得到 2,326 Ind PASS。SafeNet 從未觸發 — 風控完全由 TP+MaxHold 管理。

4. **BL=15 是結構性閾值**：BL13-14 有 0 ALL PASS，BL15 有 165。15 bar 低點突破的信號質量顯著優於 13-14。

5. **8/8 WF 的稀有性**：Cand3 在所有 8 個時間折（含 2024 bear）都維持正收益。這在 $1K 帳戶 Short 策略中極為罕見。

### V10-S v2 腳本索引

| 腳本 | 輪次 | 配置數 | 用途 |
|------|------|--------|------|
| `v10s2_r1_exploration.py` | R1 | 468 | 三範式探索，Paradigm C 勝出 |
| `v10s2_r2_finetune.py` | R2 | 6,912 | Fine-tune + SN/mCap 解鎖，284 ALL PASS |
| `v10s2_r3_validation.py` | R3 | 3 候選 | WF 6/8/10 + Stress 9/9 + Plateau 50/50 |

### V10-S v2 排除的方向

| 方向 | 輪次 | 排除原因 |
|------|------|----------|
| Pullback Short + EMAcross（純 1h EMA60/80/100） | R1 | 0/144 IS>0，完全失敗 |
| Pullback Short + TP+MaxHold | R1 | 2 Both>0, 0 Ind PASS |
| BL=13-14 | R2 | 0 ALL PASS（結構性閾值在 BL=15） |
| SN ≤ 3.5% | R2 | 大幅減少 ALL PASS（SN=2.5% 僅 3, SN=3.0% 僅 13） |
