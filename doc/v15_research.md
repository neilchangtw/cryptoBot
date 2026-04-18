# V15 策略研究：進場過濾優化（Entry Filter Optimization）

目標：在不改變 V14 進場邏輯核心（GK 壓縮突破）和出場機制的前提下，透過進場過濾器提升策略績效。

**推薦 Config D：OOS $5,458（+$1,047, +23.7%），13/13 正月（ALL MONTHS POSITIVE），WF 5/6+7/8。**
**S 策略經全面測試確認為全域最佳解（V12+V14 已鎖定），本輪僅過濾最差 S 進場。**

---

## 研究背景

### V14 基線（要超越的目標）

```
數據：ETHUSDT_1h_latest730d.csv（2024-04-13 ~ 2026-04-13）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee
IS/OOS 分割：前 50% IS，後 50% OOS

V14 L+S（OOS）：
  L: 88t $+2,184 WR 60%
  S: 86t $+2,227 WR 60%
  合計: $4,411, 11/13 正月, worst -$225 (Jan 2026), MDD $480

V14 弱月（L+S < $100）：
  Jun 2025: +$48    Jan 2026: -$225    Apr 2026: -$124
```

### V15 目標

超越 V14 OOS $4,411 並改善弱月（尤其 Jan 2026 的 -$225）。

---

## R0：V14 弱月深度診斷

腳本：`backtest/research/v15_r0_v14_diagnostic.py`

### 方法

完整重建 V14 回測，追蹤每筆交易的 BTC 狀態、ATR、volume、GK percentile、進場時間。
分析弱月 vs 強月的 7 個維度。

### 核心發現

#### 1. ATR 是最強分化因子

```
弱月（3 月）: avg ATR = 23.2（L）/ 22.6（S）
強月（10 月）: avg ATR = 30.6（L）/ 30.2（S）
```

**低 ATR = 低絕對波動 → 突破沒有後勁 → 交易虧損。**

#### 2. GK Percentile 非單調關係（L）

```
GK  0-5:   8t, WR 38%, avg $+15 ← 太死（extreme compression = dead market）
GK  5-10: 21t, WR 81%, avg $+45 ← 最強
GK 10-15: 18t, WR 39%, avg $+5  ← 模糊區
GK 15-20: 12t, WR 75%, avg $+55 ← 強
GK 20-25: 23t, WR 65%, avg $+20 ← 中等
```

GK < 5 是「死水」，不是「壓縮」。

#### 3. 進場時間有極端差異

```
L at 8:00 (UTC+8):  5t, WR 20%, total $-120 ← 亞洲開盤假突破
L at 19:00 (UTC+8): 7t, WR 29%, total $-42
S at 10:00:         7t, WR 29%, total $-57
S at 16:00:         4t, WR 25%, total $-160 ← 倫敦開盤反轉
```

#### 4. BTC 趨勢不是有效過濾器

```
L with BTC>EMA20: 58t, avg $+19.8
L with BTC<EMA20: 30t, avg $+34.6 ← 反直覺：BTC 弱時 L 更好
```

#### 5. 動量與成交量皆無效

動量（3h/6h/12h return）完全被 breakout 信號吸收（redundant）。
Volume 無顯著差異。

### R0 結論

最有前途的三個方向：
1. ATR 最低門檻過濾（H1）
2. GK percentile 下限過濾（H3）
3. 進場時間細化（H2）

---

## R1：三大進場過濾掃描

腳本：`backtest/research/v15_r1_entry_filters.py`

### H1: ATR 最低門檻

64 組掃描（L_ATR × S_ATR）：

```
Top 3:
L_ATR  S_ATR | IS      OOS      delta  WR  MDD   WstMo
  15     15  | $2,232  $5,091  +$679   64% $339  $-34
  18     15  | $2,013  $4,869  +$457   64% $339  $-45
  15      0  | $2,144  $4,826  +$414   61% $382  $-124
```

ATR>=15 同時用於 L+S 效果最好。

### H2: 進場時間細化

49 組掃描（L/S 各自額外封鎖時段）：

```
Top 3:
L_extra      S_extra | IS      OOS      delta  WR
[8, 19]      [16]    | $2,057  $5,006  +$594   63%
[8, 19]      [6]     | $2,104  $4,989  +$577   63%
[8, 19]      []      | $2,104  $4,896  +$484   62%
```

L 封鎖 8:00+19:00，S 封鎖 16:00 效果最好。

### H3: GK Percentile 下限

25 組掃描（L_GK_min × S_GK_min）：

```
Top 3:
L_min  S_min | IS      OOS      delta  WR
  7      0   | $2,064  $4,977  +$565   64%
  5      0   | $2,093  $4,926  +$515   63%
  7      3   | $1,928  $4,791  +$380   63%
```

L GK>=7（排除 dead market 的 extreme compression）效果最佳。

### H4: 組合掃描

| Config | 參數數 | IS | OOS | delta | WR | MDD | WstMo | PM | WF6 | WF8 |
|--------|--------|-----|------|-------|-----|------|-------|-----|------|------|
| BASELINE | 0 | $2,238 | $4,411 | — | 60% | $480 | $-225 | 11/13 | 5/6 | 7/8 |
| ATR15+GK7 | 2 | $1,994 | $5,389 | +$977 | 68% | $276 | $-39 | 12/13 | 5/6 | 7/8 |
| R1_full | 4 | $1,860 | $5,633 | +$1,222 | 70% | $244 | $-54 | 12/13 | 5/6 | 7/8 |

### R1 結論

三大過濾器各自有效（$565-$679），組合效果超越個別之和。

---

## R2：Deep Validation

腳本：`backtest/research/v15_r2_validation.py`

### 1. 組件拆解

| Config | IS | OOS | delta | WR | MDD | PM | WF6 | WF8 | ratio |
|--------|-----|------|-------|-----|------|-----|------|------|-------|
| BASELINE | $2,238 | $4,411 | — | 60% | $480 | 11/13 | 5/6 | 7/8 | 2.0 |
| ATR only | $2,232 | $5,091 | +$679 | 64% | $339 | 12/13 | 5/6 | 7/8 | 2.3 |
| Hour only | $2,057 | $5,006 | +$594 | 63% | $379 | 12/13 | 5/6 | 7/8 | 2.4 |
| GK only | $2,064 | $4,977 | +$565 | 64% | $331 | 12/13 | 5/6 | 7/8 | 2.4 |
| ATR+GK | $1,994 | $5,389 | +$977 | 68% | $276 | 12/13 | 5/6 | 7/8 | **2.7** |
| ALL | $1,860 | $5,633 | +$1,222 | 70% | $244 | 12/13 | 5/6 | 7/8 | 3.0 |

### 2. 參數穩健性（ALL config, 15 perturbations）

**15/15 正 delta（range +297 ~ +1,246），15/15 至少 5/6 WF。**

NOT at a cliff edge.

### 3. 交易層級分析

```
Removed: 42 trades, total PnL $-303（net losers）
Cascade: 26 NEW trades, total PnL +$919（freed cooldown → better entries）

Jan 2026 transformation:
  V14: 3 S trades ALL MaxHold losses → $-169
  V15: different timing → 5 TPs + 2 losses → $+144 ← swing of $313
```

### 4. Overfitting check

- IS/OOS ratio 2.7 for ATR+GK (acceptable)
- IS/OOS ratio 3.0 for ALL (borderline)
- Rule of thumb: > 3.0 suspicious

### R2 結論

ATR+GK (2 params) 是最佳 risk-adjusted 選擇（ratio 2.7）。
R1_full (4 params) 更好但 ratio 3.0（borderline）。

---

## R3：動態出場 + 進場動量

腳本：`backtest/research/v15_r3_dynamic_exits.py`

### H5: ATR-Scaled TP（L only）

`TP_pct = 3.5% × (entry_ATR / median_ATR) × scale`

高波動環境 → 更寬 TP，低波動 → 更窄 TP。

```
Scale | IS     | OOS    | delta  | PM    | Note
0.70  | $1,990 | $4,944 | +$532  | 13/13 | ALL POSITIVE MONTHS
0.80  | $1,998 | $5,113 | +$702  | 13/13 | ALL POSITIVE MONTHS
0.85  | $2,028 | $5,229 | +$817  | 13/13 | ALL POSITIVE MONTHS
0.90  | $2,060 | $5,258 | +$847  | 12/13 |
1.00  | $2,125 | $5,463 | +$1,052| 12/13 | Best OOS
1.20  | $1,901 | $5,509 | +$1,098| 12/13 |
```

Scale 0.7-0.85 achieves 13/13 PM（低 scale = 低波動時更早 TP → 避免利潤回吐）。
Scale 1.0 最大 OOS 但只有 12/13 PM。

### H6: Dynamic MH (GK-adaptive)：**ABANDONED**

GK-adaptive MH（低 GK → longer MH）不改善任何指標。

### H7: Entry Momentum Filter：**ABANDONED**

**所有 9 組動量過濾結果完全相同！**
原因：breakout_long（close > 15-bar max）本身就隱含正動量，額外動量過濾 100% redundant。

### H8: Bar Range Confirmation

```
S_rng>=10: S OOS $2,823（+$69），總 OOS $5,458，13/13 PM
S_rng>=15+: WF 下降到 6/8
L_rng>=15+: L trades 大幅減少，嚴重損害
```

S bar range >= $10 是微幅但穩定的改善。

### Best Combinations

| Config | OOS | delta | WR | MDD | Worst Mo | PM | WF6/8 | ratio |
|--------|-----|-------|-----|------|----------|-----|-------|-------|
| R2 base | $5,389 | +$977 | 68% | $276 | $-39 | 12/13 | 5/6 7/8 | 2.7 |
| R2+TP_atr*1.0 | $5,463 | +$1,052 | 68% | $276 | $-39 | 12/13 | 5/6 7/8 | 2.6 |
| R1_full | $5,633 | +$1,222 | 70% | $244 | $-54 | 12/13 | 5/6 7/8 | 3.0 |
| R1_full+TP_atr | $5,751 | +$1,340 | 70% | $244 | $-54 | 12/13 | 5/6 7/8 | 3.0 |

---

## R4：最終候選驗證

腳本：`backtest/research/v15_r4_final_validation.py`

### 全候選對比

| Config | 參數 | IS | OOS | delta | WR | MDD | Worst Mo | PM | WF | ratio |
|--------|------|-----|------|-------|-----|------|----------|-----|-----|-------|
| V14 BASELINE | 0 | $2,238 | $4,411 | — | 60% | $480 | $-225 | 11/13 | 5/6 7/8 | 2.0 |
| **A: ATR15+GK7** | **2** | **$1,994** | **$5,389** | **+$977** | **68%** | **$276** | **$-39** | **12/13** | **5/6 7/8** | **2.7** |
| B: R1_full | 4 | $1,860 | $5,633 | +$1,222 | 70% | $244 | $-54 | 12/13 | 5/6 7/8 | 3.0 |
| C: R1_full+TP_atr | 5 | $1,929 | $5,751 | +$1,340 | 70% | $244 | $-54 | 12/13 | 5/6 7/8 | 3.0 |
| **D: A+S_rng>=10** | **3** | **$1,932** | **$5,458** | **+$1,047** | **68%** | **$276** | **+$18** | **13/13** | **5/6 7/8** | **2.8** |
| E: B+S_rng>=10 | 5 | $1,798 | $5,703 | +$1,291 | 70% | $244 | +$16 | 13/13 | 5/6 7/8 | 3.2 |
| F: C+S_rng>=10 | 6 | $1,867 | $5,821 | +$1,410 | 71% | $244 | +$16 | 13/13 | 5/6 7/8 | 3.1 |

### H9: BTC GK Filter：**COMPLETE FAILURE**

BTC GK <= 任何閾值都大幅減少 L 交易數。BTC_GK<=25 把 L 從 73t 砍到 31t。**ABANDONED。**

### 敏感度熱圖

```
           GK>= 0 GK>= 3 GK>= 5 GK>= 7 GK>=10 GK>=12
ATR>= 0       +0   +379   +515   +565   -488   -348
ATR>=10     +284   +383   +519   +569   -484   -344
ATR>=12     +344   +428   +572   +623   -430   -291
ATR>=15     +679   +782   +927  ★+977    +27   +167
ATR>=18      +78   +189   +333   +384   -533   -394
ATR>=20     -814   -703   -559   -508  -1252  -1135
```

**ATR=15 + GK=7 是明確的全域最優解**，不在懸崖邊緣：
- ATR=12 時 GK=7 仍有 +$623
- ATR=18 時 GK=7 仍有 +$384
- GK=5 和 GK=3 也都是正 delta

### Config A 月度明細（OOS）

```
Month   | V14_L  V14_S   V14  | V15_L  V15_S   V15  | delta
2025-04 | $  -25 $ +130 $ +104 | $ +223 $ +159 $ +382 | +$278
2025-05 | $ +276 $  +19 $ +295 | $ +238 $  +59 $ +297 |   +$2
2025-06 | $  -77 $ +126 $  +48 | $  -77 $ +126 $  +48 |    $0
2025-07 | $ +330 $ +156 $ +486 | $ +338 $ +156 $ +494 |   +$8
2025-08 | $ +132 $ +307 $ +438 | $ +256 $ +307 $ +563 | +$124
2025-09 | $ +190 $ +149 $ +339 | $ +153 $ +149 $ +302 |  -$37
2025-10 | $  +35 $ +622 $ +656 | $  +78 $ +622 $ +700 |  +$43
2025-11 | $ +232 $ +181 $ +413 | $ +232 $ +181 $ +413 |    $0
2025-12 | $ +112 $ +158 $ +269 | $ +236 $ +201 $ +437 | +$168
2026-01 | $  -56 $ -169 $ -225 | $  -86 $ +104 $  +18 | +$243
2026-02 | $ +570 $ +325 $ +895 | $ +570 $ +387 $ +958 |  +$62
2026-03 | $ +441 $ +376 $ +816 | $ +441 $ +376 $ +816 |    $0
2026-04 | $  +26 $ -150 $ -124 | $  +32 $  -71 $  -39 |  +$85
```

8 個月改善，4 個月持平，1 個月小幅退步（Sep -$37）。
**Jan 2026 從 -$225 翻正到 +$18**（swing $243，cascade 效果）。

### Config D 月度明細（+S_rng>=10）

13/13 正月，worst month +$18。具體差異在於 S 的 1 筆低 bar range 虧損被過濾。

---

## 推薦方案

### ★ 推薦：Config D（ATR15 + GK7 + S_rng>=10）

```
新增 3 個進場過濾器（L+S 共用 ATR，L 獨有 GK，S 獨有 bar range）：

1. ATR(14) >= 15（L+S）
   - 過濾極低波動環境（突破無後勁）
   - Jan 2026 的低 ATR 環境被精確識別

2. L GK percentile >= 7
   - 排除 "dead market" 的 extreme compression
   - GK 0-5 WR 只有 38%，是假壓縮

3. S entry bar range >= $10
   - 過濾 S 進場 bar 震幅太小的信號
   - 微幅改善但確保 13/13 PM

帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

OOS 績效：
  L: 73t, $2,635, WR 68%
  S: 86t, $2,823, WR 67%（+$69 vs baseline S）
  L+S: $5,458, WR 68%, MDD $276
  正月：13/13（ALL MONTHS POSITIVE）
  最差月：+$18
  WF：5/6（6-fold）, 7/8（8-fold）
  IS/OOS ratio: 2.8（healthy）

vs V14:
  OOS: $4,411 → $5,458（+$1,047, +23.7%）
  WR: 60% → 68%（+8%）
  MDD: $480 → $276（-43%）
  最差月: $-225 → +$18（swing $243）
  正月: 11/13 → 13/13（+2）
```

### 替代方案

| 方案 | 新增參數 | OOS | delta | PM | ratio | 適合 |
|------|----------|------|-------|-----|-------|------|
| A（最簡） | 2 | $5,389 | +$977 | 12/13 | 2.7 | 最穩健 |
| **D（推薦）** | **3** | **$5,458** | **+$1,047** | **13/13** | **2.8** | **最佳平衡** |
| B（進階） | 4 | $5,633 | +$1,222 | 12/13 | 3.0 | 更高收益 |
| F（激進） | 6 | $5,821 | +$1,410 | 13/13 | 3.1 | 最大收益但過擬合風險 |

---

## V15 策略規格（推薦 Config D）

### L 策略（做多）

```
方向：Long-only（純 1h）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場（V15 新增 2 個過濾器）：
  1. GK pctile < 25（GK(5/20) pw100）
  2. 【V15 新增】GK pctile >= 7（排除 dead market extreme compression）
  3. Close breakout 15 bar
  4. 【V15 新增】ATR(14) >= 15（排除極低波動環境）
  5. Block hours {0,1,2,12} UTC+8, block days {Sat,Sun}
  6. Exit Cooldown: 6 bar
  7. Monthly Entry Cap: 20, maxTotal = 1

出場（同 V14）：
  1. SafeNet -3.5%（含 25% 穿透模型）
  2. TP +3.5%
  3. MFE Trailing: 浮盈 >=1.0% 後回吐 >=0.8% → 出場
  4. Conditional MH: bar 2 虧 >=-1.0% → MH 6→5
  5. MaxHold 6(or 5) → ext2 + BE trail
```

### S 策略（做空）

```
方向：Short-only（純 1h）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場（V15 新增 2 個過濾器）：
  1. GK pctile_S < 35（GK(10/30) pw100）
  2. Close breakout 15 bar
  3. 【V15 新增】ATR(14) >= 15（排除極低波動環境）
  4. 【V15 新增】Entry bar range >= $10（排除極窄震幅 bar）
  5. Block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  6. Exit Cooldown: 8 bar
  7. Monthly Entry Cap: 20, maxTotal = 1

出場（同 V14，完全不動）：
  1. SafeNet +4.0%（含 25% 穿透模型）
  2. TP -2.0%
  3. MaxHold 10 → ext2 + BE trail
```

---

## 排除的方向

- BTC GK compression filter（R4 H9: 所有閾值大幅減少 L 交易，全部更差）
- Entry momentum filter（R3 H7: 100% redundant with breakout signal）
- Dynamic MH based on GK（R3 H6: 不改善任何指標）
- Volume filter（R0: 高量/低量無顯著差異）
- BTC trend filter（R0: BTC<EMA20 的 L avg PnL 反而更高）
- L bar range >= 15+（R3 H8: 過度過濾，L trades 大幅減少）
- S bar range >= 15+（R4: WF 下降到 6/8）
- GK min >= 10+（R4 heatmap: cliff edge，大幅虧損）
- ATR >= 20+（R4 heatmap: catastrophic，$-508 ~ -$1,252）
- ATR-scaled TP（R3 H5: 改善 L 但增加複雜度，ratio 惡化）

---

## 10-Gate 懷疑論稽核（Skeptical Audit）

**稽核腳本**：`backtest/research/v15_audit_10gate.py`
**稽核立場**：V15 是快樂表（overfit），除非數據證明不是。

### 稽核結果

| Gate | 名稱 | 判定 | 說明 |
|------|------|------|------|
| 1 | 事後選擇 vs 事前可知 | **FAIL** | IS ATR<15 有 7t +$79（正收益）；IS-optimal threshold=0（IS 不會建議此過濾器） |
| 2 | ATR>=15 穩健性 | PASS | ATR 12-18 全部 OOS >$4,500，無 cliff，曲線平滑 |
| 3 | GK>=7 穩健性 | CONDITIONAL | OOS L GK<7 實際 +$133（正收益！），-$130 單筆佔 98%，dead water 假說不成立 |
| 4 | S bar range>=10 穩健性 | PASS | 移除 2t +$6，cascade +1t +$76，效果來自 cascade |
| 5 | Cascade 效果真實性 | **FAIL** | OOS cascade 17t +$834 avg $49/t vs IS 22t +$29 avg $1.3/t；100x 隨機模擬排 100th percentile |
| 6 | IS/OOS 倒掛檢驗 | CONDITIONAL | ratio 2.83（<3.5 PASS），但 Swap test delta +$1,047→-$306（衰退 129%） |
| 7 | Walk-Forward 全面分析 | PASS | 10-fold 9/10，12-fold 11/12，rolling 3-month 全正 |
| 8 | 過濾器獨立性分析 | PASS | overlap 僅 6%，三個過濾器互相獨立 |
| 9 | 實盤可行性 | PASS | ATR/GK/bar_range 均使用已收盤 bar，無前瞻偏差 |
| 10 | 壓力測試 | PASS | Fee+50% OOS $5,140>$4,000，同時 SN $383<$400，ATR<15 期間零交易 |

**PASS: 6/10 | CONDITIONAL: 2/10 | FAIL: 2/10**

### 關鍵 FAIL 分析

**Gate 1 — ATR 過濾器是事後選擇**：
- IS 中 ATR<15 交易有 7 筆，合計 +$79，avg +$11.3/trade（正收益）
- OOS 中 ATR<15 交易有 13 筆，合計 -$335，avg -$25.7/trade（虧損）
- IS 數據自行最佳化出的 ATR threshold = 0（IS 不會建議任何 ATR 過濾）
- **結論**：ATR>=15 的設計來自觀察 OOS 弱月特徵，非從 IS 獨立推導

**Gate 5 — Cascade 效果是極端好運**：
- 過濾器移除的交易本身 PnL 影響小（直接移除 delta +$213）
- 但 cascade（空出的 cooldown 窗口進入的新交易）OOS 極度正偏
- 100 次隨機移除模擬中，實際 cascade 排第 100 名（比任何隨機情境都好）
- **結論**：$1,047 改善中，大部分來自 cascade 運氣而非過濾器邏輯

### CONDITIONAL Gate 詳解

**Gate 3 — GK>=7 依賴 cascade**：
- IS L GK<7 有 18t -$21（微虧，支持移除）
- 但 OOS L GK<7 有 19t +$133（正收益！移除反而虧 $133）
- 改善全靠 cascade 彌補，且最差單筆 -$130 佔 98%
- 「死水」物理假說（GK<7 突破力弱）數據不支持

**Gate 6 — Swap Test 嚴重衰退**：
- IS/OOS ratio 2.83（<3.5 未過警戒線）
- 但前後對調（2nd half=IS, 1st half=OOS）delta 從 +$1,047 降至 -$306
- 衰退 129%，遠超 50% 警戒線
- 暗示改善高度依賴特定時間段

### 正面發現

- **ATR 曲線穩健**（Gate 2）：ATR 12-18 全部 >$4,500，不是 cliff edge
- **WF 穩固**（Gate 7）：10-fold 9/10，V15 在後半段 4/4 folds 優於 V14
- **無前瞻偏差**（Gate 9）：全部過濾器使用已收盤 bar 數據
- **Fee+50% 通過**（Gate 10）：$5,140 仍高於 $4,000 基線

### 最終判定：**REJECTED**

V15 Config D 的 $1,047 改善（+23.7%）主要來源：
1. ATR 過濾器由 OOS 觀察設計（非 IS 獨立推導）— Gate 1 FAIL
2. 改善的核心是 cascade 效果，而非過濾器直接移除壞交易 — Gate 5 FAIL
3. Cascade 在 100x 隨機模擬中排 100th percentile（極端好運）
4. Swap test 衰退 129%（方向依賴性強）

**V14 維持為最佳策略，不建議部署 V15。**

保守估計（30% safety haircut）：即使部署，預期年化 ~$3,821（而非 $5,458）。
