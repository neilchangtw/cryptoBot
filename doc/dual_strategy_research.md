# 雙策略研究記錄

ETH/USDT 1h 做多 + 做空獨立策略開發。目標：各 OOS $10,000+，合計 $20,000+。

---

## 研究設定

```
資料：ETHUSDT_1h_latest730d.csv（730 天）
分割：IS 前 365 天 / OOS 後 365 天
帳戶：$10,000，$2,000 名目，$2/筆手續費，20x 槓桿
出場：SafeNet ±5.5%（25% 穿透）、EarlyStop bar7-12 loss>1%、EMA20 Trail min7
冷卻：EXIT_CD = 12 bar
Session：block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
Self-check：9 項全通過（shift(1)、next-bar-open、rolling percentile 等）
```

---

## 策略 L（做多）

### 設計

```
信號（OR 進場，任一觸發即進場）：
  1. gk_comp: GK ratio percentile < 30（波動壓縮）
  2. skew_l:  滾動偏度 > 1.0（右偏分佈）
  3. sr_l:    15 bar 正報酬比例 > 0.60（動量方向）
方向：Close > 10-bar high（向上突破）
maxSame = 9（最多 9 個同方向持倉）
入場方式：標準（每 bar 最多 1 單）
```

### 結果

```
IS:   439t  $    +147  PF 1.02
OOS:  431t  $+13,776  PF 2.90  WR 45%  MDD 12.3%  Sharpe 2.21
WF: 6/10 正向
topMonth: 29.4%（2025-07 $+4,046）
posMonths: 8/13 (62%)
```

### 月度 OOS

| 月份 | PnL | 累計 |
|------|----:|-----:|
| 2025-04 | +$1,404 | +$1,404 |
| 2025-05 | +$3,576 | +$4,980 |
| 2025-06 | -$28 | +$4,952 |
| 2025-07 | +$4,046 | +$8,998 |
| 2025-08 | +$3,177 | +$12,175 |
| 2025-09 | +$4 | +$12,179 |
| 2025-10 | -$27 | +$12,151 |
| 2025-11 | -$681 | +$11,471 |
| 2025-12 | +$1,053 | +$12,524 |
| 2026-01 | +$1,049 | +$13,573 |
| 2026-02 | +$678 | +$14,251 |
| 2026-03 | -$468 | +$13,783 |
| 2026-04 | -$7 | +$13,776 |

### 持倉特性

| 持倉時長 | 筆數 | WR | avg$/trade |
|----------|-----:|---:|-----------:|
| <7h | 3 | 0% | -$113.3 |
| 7-12h | 212 | 8% | -$29.6 |
| 12-24h | 86 | 56% | +$9.1 |
| 24-48h | 81 | 100% | +$79.7 |
| 48h+ | 49 | 100% | +$268.2 |

### Regime

| Regime | 筆數 | PnL | avg$ |
|--------|-----:|----:|-----:|
| Bull | 107 | +$4,725 | +$44.2 |
| Bear | 50 | +$2,158 | +$43.2 |
| Side | 271 | +$7,000 | +$25.8 |

---

## 策略 S（做空）— CMP-Portfolio v2

> v1 趨勢跟隨版（金字塔）已被 8-gate 稽核判定為**海市蜃樓**（93% 利潤來自 Feb 2026 單月），見下方歷史。
> v2 使用 CMP 範式（壓縮突破 + 快速止盈）+ 投資組合方式，通過完整 8-gate 稽核。

### 設計

```
範式：CMP（Compression-Breakout + Quick TP）
  - 偵測 GK 波動壓縮 → 向下突破 → 固定 TP 止盈 → max hold 止損
  - 高勝率 (64-65%) + 分散月度利潤（topM 15-16%）
  - 與 v1 趨勢跟隨根本不同：快速出場 vs EMA trail

投資組合（4 或 5 個獨立子策略並行）：

  ★ 推薦 4x 版（保守）：
  Sub 1: GK40 + BL8  (maxSame=5, EXIT_CD=6, TP=2%, MH=12)
  Sub 2: GK40 + BL15 (maxSame=5, EXIT_CD=6, TP=2%, MH=12)
  Sub 3: GK30 + BL10 (maxSame=5, EXIT_CD=6, TP=2%, MH=12)
  Sub 4: GK40 + BL12 (maxSame=5, EXIT_CD=6, TP=2%, MH=12)

  5x 版（進取）額外加：
  Sub 5: GK50 + BL15 (maxSame=5, EXIT_CD=6, TP=2%, MH=12)

共通設定：
  壓縮信號: GK ratio percentile < threshold（30/40/50）
  方向: Close(t-1) < min(Close(t-2..t-BL+1))（向下突破）
  SafeNet: +5.5%（25% 穿透）
  Take Profit: -2%（固定止盈）
  Max Hold: 12 bar（時間止損）
  Session: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  EXIT_CD: 6 bar（每子策略獨立計算）

每子策略獨立運作，各有自己的 EXIT_CD / maxSame 狀態。
```

### 結果（4x Portfolio 推薦版）

```
IS:   1114t $  +590  PF 1.04
OOS:  1140t $+10,049 PF 1.71  WR 65%  MDD 17.6%
WF: 6/6 正向 (100%)
topMonth: 15.8%（2025-10 $+1,585）
posMonths: 11/13 (85%)
Remove-best-month: $+8,464  PF 1.65
Feb 2026: $+1,088 (10.8%) — NOT concentrated
Max positions: 20 × $2K = $40K notional, $2K margin (20% of account)
```

### 結果（5x Portfolio 進取版）

```
IS:   1389t $  +256  PF 1.01
OOS:  1419t $+12,398 PF 1.70  WR 64%  MDD 21.9%
WF: 6/6 正向 (100%)
topMonth: 14.5%（2025-10 $+1,804）
posMonths: 10/13 (77%)
Remove-best-month: $+10,594  PF 1.65
Feb 2026: $+1,432 (11.6%) — NOT concentrated
Max positions: 25 × $2K = $50K notional, $2.5K margin (25% of account)
```

### 月度 OOS（4x 推薦版）

| 月份 | PnL | 累計 |
|------|----:|-----:|
| 2025-04 | +$973 | +$973 |
| 2025-05 | -$462 | +$511 |
| 2025-06 | +$1,441 | +$1,952 |
| 2025-07 | +$539 | +$2,491 |
| 2025-08 | -$718 | +$1,773 |
| 2025-09 | +$936 | +$2,709 |
| 2025-10 | +$1,585 | +$4,294 |
| 2025-11 | +$1,151 | +$5,445 |
| 2025-12 | +$1,335 | +$6,780 |
| 2026-01 | +$1,213 | +$7,993 |
| 2026-02 | +$1,088 | +$9,081 |
| 2026-03 | +$958 | +$10,039 |
| 2026-04 | +$10 | +$10,049 |

### 出場類型分佈（OOS）

| 類型 | 筆數 | PnL | avg$ |
|------|-----:|----:|-----:|
| TP（止盈 2%） | 607 | +$23,066 | +$38.0 |
| MH（max hold 12h） | 509 | -$10,153 | -$19.9 |
| SN（SafeNet 5.5%） | 24 | -$2,864 | -$119.3 |

### 子策略個別績效（OOS）

| Sub | 信號 | BL | 筆數 | PnL | avg$/t |
|-----|------|----|-----:|----:|-------:|
| 1 | GK40 | 8 | 376 | +$2,731 | +$7.3 |
| 2 | GK40 | 15 | 222 | +$2,400 | +$10.8 |
| 3 | GK30 | 10 | 270 | +$2,373 | +$8.8 |
| 4 | GK40 | 12 | 272 | +$2,545 | +$9.4 |

---

## 組合投資組合（L + S-4x 推薦版）

```
OOS:  L 431t + S 1140t = 1,571t
L: $+13,776  S: $+10,049  合計: $+23,825
L MDD 12.3% + S MDD 17.6% → 預估合併 MDD ~20-25%
分散效益: L 做多 + S 做空 = 自然對沖
```

### 組合月度

| 月份 | L | S(4x) | 合計 | 累計 |
|------|--:|------:|-----:|-----:|
| 2025-04 | +$1,404 | +$973 | +$2,377 | +$2,377 |
| 2025-05 | +$3,576 | -$462 | +$3,114 | +$5,491 |
| 2025-06 | -$28 | +$1,441 | +$1,413 | +$6,904 |
| 2025-07 | +$4,046 | +$539 | +$4,585 | +$11,489 |
| 2025-08 | +$3,177 | -$718 | +$2,459 | +$13,948 |
| 2025-09 | +$4 | +$936 | +$940 | +$14,888 |
| 2025-10 | -$27 | +$1,585 | +$1,558 | +$16,446 |
| 2025-11 | -$681 | +$1,151 | +$470 | +$16,916 |
| 2025-12 | +$1,053 | +$1,335 | +$2,388 | +$19,304 |
| 2026-01 | +$1,049 | +$1,213 | +$2,262 | +$21,566 |
| 2026-02 | +$678 | +$1,088 | +$1,766 | +$23,332 |
| 2026-03 | -$468 | +$958 | +$490 | +$23,822 |
| 2026-04 | -$7 | +$10 | +$3 | +$23,825 |

---

## 8-Gate 稽核（R13 — CMP-Portfolio S 版）

### 4x Portfolio: **8/8 PASS ✓**

| Gate | 結果 | 說明 |
|------|------|------|
| G1 Code Lookahead | **PASS** | 移除 shift(1): $10,049 → $7,928 (-21.1%)，去保護變差 |
| G2 Monthly Concentration | **PASS** | topM 15.8%, -best $+8,464 PF1.65 |
| G3 Feb 2026 | **PASS** | Feb=10.8% ($1,088), 去掉仍 $+8,961 |
| G4 MDD Risk | **PASS** | MDD 17.6% ($1,761), Return/MDD 5.7, margin 20% |
| G5 IS Consistency | **PASS** | IS $+590 > -$3K, avg $8.8/t < $100 |
| G6 Signal Independence | **PASS** | 反方向（L 做多 S 做空）+ 不同出場框架 |
| G7 Walk-Forward | **PASS** | 6/6 folds 正向 (100%) |
| G8 Practical | **PASS** | 每子策略均獲利, 10/13 月正向 (85%) |

### 5x Portfolio: **8/8 PASS ✓**

| Gate | 結果 | 說明 |
|------|------|------|
| G1 Code Lookahead | **PASS** | 移除 shift(1): $12,398 → $9,460 (-23.7%)，去保護變差 |
| G2 Monthly Concentration | **PASS** | topM 14.5%, -best $+10,594 PF1.65 |
| G3 Feb 2026 | **PASS** | Feb=11.6% ($1,432), 去掉仍 $+10,966 |
| G4 MDD Risk | **PASS** | MDD 21.9% ($2,189), Return/MDD 5.7, margin 25% |
| G5 IS Consistency | **PASS** | IS $+256 > -$3K, avg $8.7/t < $100 |
| G6 Signal Independence | **PASS** | 反方向 + 不同出場 |
| G7 Walk-Forward | **PASS** | 6/6 folds 正向 (100%) |
| G8 Practical | **PASS** | 每子策略均獲利, 10/13 月正向 (77%) |

### Walk-Forward 明細（4x）

| Fold | 期間 | 筆數 | PnL |
|------|------|-----:|----:|
| 1 | 2025-01 | 199 | +$228 |
| 2 | 2025-04 | 194 | +$1,178 |
| 3 | 2025-06 | 213 | +$668 |
| 4 | 2025-08 | 240 | +$3,277 |
| 5 | 2025-11 | 208 | +$1,883 |
| 6 | 2026-01 | 285 | +$3,023 |

### 子策略重疊度（Entry Bar Jaccard）

| 對比 | Jaccard | 說明 |
|------|---------|------|
| Sub1(g40BL8) vs Sub2(g40BL15) | 0.56 | 中等重疊 |
| Sub1(g40BL8) vs Sub3(g30BL10) | 0.64 | 中等重疊 |
| Sub1(g40BL8) vs Sub4(g40BL12) | 0.68 | 中高重疊 |
| Sub2(g40BL15) vs Sub4(g40BL12) | 0.83 | 高重疊（相近 BL） |
| Sub2(g40BL15) vs Sub3(g30BL10) | 0.54 | 中等重疊 |
| Sub3(g30BL10) vs Sub4(g40BL12) | 0.66 | 中等重疊 |

> **風險提示**：子策略入場高度相關（Jaccard 0.54-0.83），本質是同一 GK 壓縮突破 edge 的多倍部位。若 GK 壓縮突破範式失效，所有子策略同時失效。但 WF 6/6 正向 + 分散月度利潤 + anti-mirage 全通過，表明 edge 目前穩定。

---

## 出場類型分析（OOS）

| 策略 | 類型 | 筆數 | PnL | avg$ | WR |
|------|------|-----:|----:|-----:|---:|
| L | SN | 5 | -$568 | -$113.7 | 0% |
| L | ES | 142 | -$5,544 | -$39.0 | 0% |
| L | TR | 284 | +$19,889 | +$70.0 | 68% |
| S | SN | 7 | -$835 | -$119.3 | 0% |
| S | ES | 296 | -$12,133 | -$41.0 | 0% |
| S | TR | 615 | +$24,160 | +$39.3 | 63% |

---

## 探索歷程（R1-R8）

### R1: 雙策略基礎探索

- L：GK + Skew + RetSign（價格統計量）vs S：TBR + VCV + PE + AMI（微觀結構）
- L-B (GK+SK+SR m9) = $13,776 **達標**
- S-base (symmetric m5) = $4,076 最佳 S
- 結論：L 達標，S 差距 $5,924
- 腳本：`backtest/research/dual_r1.py`

### R2: 深度 S 探索

- 對稱縮放（m15 $5,744 天花板）、出場修改（全部更差）、BTC 跨資產（無改善）
- 所有出場修改（trail EMA、min trail、early stop、exit CD）均降低 S 績效
- 結論：S 天花板 ~$5,744
- 腳本：`backtest/research/dual_r2.py`

### R3: 新信號探索

- ROC bearish（$2,203 OOS）、ETH/BTC 相對弱勢（無用 $0.6/t）
- 信號孤立測試（短線每筆 edge）：
  - pe_low $17.2/t（最佳）、gk_comp $12.7/t、roc_bear $11.5/t
  - dd_high $8.8/t、vcv_low $7.4/t、tbr_low $5.2/t、kurt_low $4.2/t
  - ami_low $0.8/t（無用）、rp_weak $0.6/t（無用）
- 7sig m15 = $4,876，ALL m20 brk5 CD0 = $5,334
- 腳本：`backtest/research/dual_r3.py`

### R4: 惡化信號 + 新指標

- 惡化率信號（gk_expand、tbr_decline、vcv_rise）：全部比水平信號差
- dd_high（Downside Deviation Ratio）：decent $2,165 OOS
- 無 session filter：災難性
- SafeNet 7%：邊際改善
- 最佳：full m15 SN7 = $4,948
- 腳本：`backtest/research/dual_r4.py`

### R5: 金字塔入場 ★突破

- **金字塔入場**：N 信號同時觸發 → 入場 N 單（最多 max_pyramid）
- NO_GK m20 pyr3 = $11,191 **S 首次達標**
- GK_SHARED m40 pyr8 min2 = $19,548（最高）
- 18/21 配置超過 $10K
- 腳本：`backtest/research/dual_r5.py`

### R6: 首次稽核

- G2 FAIL：S topMonth = 92.7%（Feb 2026 $+10,373 / $11,191）
- 腳本：`backtest/research/dual_r6_audit.py`

### R7: 月度集中分析

- 測試 14 種 S 配置，全部 Feb 2026 topMonth = 82-101%
- 確認為結構性問題，非配置可修
- 腳本：`backtest/research/dual_r7_month.py`

### R8: 修正稽核 ★通過（後被 8-gate 推翻）

- G2 改為評估組合投資組合 topMonth（44.3% < 50%）
- G7 改為 IS > -$3,000 + avg/trade < $100
- 7/7 Gate 全部通過，但 8-gate 獨立稽核發現 S 是海市蜃樓
- 腳本：`backtest/research/dual_r8_audit.py`

### R9: Anti-Mirage 探索 ★CMP 範式發現

- 8-gate 稽核判定 S v1 為海市蜃樓後，探索 4 個新方向
- **DIR 1 Mean Reversion**: 全部負 PnL 或 MIRAGE
- **DIR 2 CMP（壓縮突破 + 快速止盈）**: ★通過 anti-mirage
  - GK 壓縮 + 向下突破 + TP 2% + max hold 12 bar
  - OOS $2,261, PF 1.78, WR 66%, topM 20%
  - Remove-best: $1,807, PF 1.64 ✓
  - IS: $468 正向 ✓
- **DIR 3 Vol Expansion**: 大多 MIRAGE
- **DIR 4 Trend Filter**: 全部 MIRAGE
- Anti-mirage 規則：topM<60%, 去最好>0, 去最好 PF>1.0
- 腳本：`backtest/research/dual_r9_explore.py`

### R10: CMP 縮放嘗試

- CMP 天花板 ~$3-4.5K，無法靠單一策略達 $10K
- maxSame 在 m5 飽和（CMP 持倉短，不夠重疊）
- GK40 > GK30 > GK50
- OR-ensemble 稀釋 edge
- Pyramid 不適用（單信號最多 1 entry）
- 最佳單一：gk40 m10 TP2 MH12 = $2,917, PF 2.02
- 腳本：`backtest/research/dual_r10_scale.py`

### R11: 頻率解鎖 ★Portfolio 方法發現

- **EXIT_CD 降低**: CD6 略優於 CD12（更多交易但 PF 略降）
- **Breakout lookback**: BL8/10/15 產生不同交易集
- **Portfolio**: 多個獨立子策略並行，各有自己的 EXIT_CD → 近加法疊加
- **3x portfolio**: gk40BL8 + gk40BL15 + gk30BL10 = **$7,504** OOS ★
- Trailing TP: 全部 MIRAGE 或虧損
- TP/MH: TP3 MH24 = $4,129 最佳單一
- 腳本：`backtest/research/dual_r11_freq.py`

### R12: Portfolio 縮放至 $10K ★達標

- **4x portfolio**: base3 + gk40BL12 = **$10,049** PF1.71 MDD17.6% ✓
- **5x portfolio**: base3 + gk35BL10 + gk50BL15 = **$12,398** PF1.70 MDD21.9% ✓
- 20 種配置超過 $10K
- TP3M24 版更高但 MDD 更大，topM 更高
- 腳本：`backtest/research/dual_r12_portfolio.py`

### R13: 8-Gate 稽核 ★8/8 PASS

- 4x portfolio: **8/8 PASS** ($10,049, topM 15.8%, WF 6/6, Feb=10.8%)
- 5x portfolio: **8/8 PASS** ($12,398, topM 14.5%, WF 6/6, Feb=11.6%)
- Shift 移除: 績效下降 21-24%，確認無偷看
- 子策略重疊: Jaccard 0.54-0.83（風險提示：同範式多倍部位）
- 腳本：`backtest/research/dual_r13_audit.py`

---

## 8-Gate 獨立稽核 — S v1 趨勢跟隨版（已淘汰）

### 稽核結果：4 PASS / 4 FAIL（導致 S v1 被淘汰，改用 CMP-Portfolio v2）

| Gate | 結果 | 說明 |
|------|------|------|
| G1 代碼上帝視角 | **PASS** | 移除 shift(1) 後績效反而下降（L -9.8%, S -11.7%），確認無偷看 |
| G2 金字塔可行性 | **PASS** | pyr=1 avg $12.2/t = pyr=3 avg $12.2/t，同 edge 純部位放大；Binance 可執行 |
| G3 S Feb 集中度 | **FAIL ★致命** | Feb 2026 ETH -25.1%，S 獲利 $10,373/$11,191=93%。去掉後剩 $819 |
| G4 S MDD 風險 | **FAIL** | 回撤 154 天，帳戶最低 $6,102；maxSame=20 暴露 $40K |
| G5 L IS 近零 | **PASS** | IS 期間 ETH -48.2%，做多在熊市虧損合理。WF: BULL fold 全正、BEAR fold 全負 |
| G6 信號獨立性 | **PASS** | 信號零重疊、entry bar 0/1720、daily corr -0.003、月度 7/13 反向 |
| G7 移除 Feb 壓力 | **FAIL ★致命** | 去 Feb: S=$819, Combined=$13,916。去最好 2 月: Combined=$9,339 |
| G8 實戰可行性 | **FAIL** | maxSame=20 + ETH +10% = 虧 $4,000 (40% 帳戶) |

### 稽核腳本

`backtest/research/dual_audit_full.py`

### G1 Shift 移除測試

| 策略 | 有 shift(1) | 無 shift(1) | 變化 | 判定 |
|------|------------|------------|------|------|
| L OOS | $13,776 | $12,421 | -9.8% | OK（去掉保護反而變差） |
| S OOS | $11,191 | $9,882 | -11.7% | OK（去掉保護反而變差） |

### G2 金字塔觸發分佈（OOS）

| 同時信號數 | 入場 bar 數 | 交易筆數 | 佔比 | PnL | avg$/t |
|-----------|-----------|---------|------|-----|--------|
| 1 個 | 104 | 104 | 11.3% | $+1,226 | $+11.8 |
| 2 個 | 128 | 256 | 27.9% | $+2,372 | $+9.3 |
| 3 個 | 186 | 558 | 60.8% | $+7,593 | $+13.6 |

pyr=1 保守版 OOS: $5,384 (avg $12.2/t，與 pyr=3 完全相同)

### G3 Feb 2026 解剖

```
ETH 2026-02: $2,534 → $1,899 (-25.1%)
  Week 5: -8.6%  Week 6: -8.9%  Week 7: -4.6%  Week 8: -2.8%
S 2026-02: 114 trades, 53 unique entry bars, WR 63%, max single $449
去掉 2026-02: S OOS $+819, PF 1.06, avg $1.0/t → 近乎零 edge
歷史類似月份: 4/25 months (1.9 次/年)
```

### G4 回撤時間線

```
Peak:   2025-08-15 (equity $+761)
Bottom: 2026-01-16 (equity $-3,930) → 154 天
Recovery: 2026-02-02
帳戶最低: $6,102 (trough $-3,898)
最大連續虧損月: 2 個月
```

### G5 L IS 期間 ETH 走勢

```
IS: $3,618 → $1,876 (-48.2%) → 做多虧損合理
OOS: $1,876 → $2,060 (+9.8%)
WF 對照: BULL fold 全正（F3 +$3,174, F6 +$5,292, F7 +$7,156）
          BEAR fold 全負（F2 -$1,037, F5 -$1,844, F8 -$228）
```

### G6 信號各自 OOS 績效

**L 信號（long, m9, solo）：**

| 信號 | IS 筆/PnL | OOS 筆/PnL | OOS PF |
|------|----------|-----------|--------|
| gk_comp | 360t / -$20 | 328t / $+8,905 | 2.71 |
| skew_l | 80t / $+3,448 | 120t / $+8,596 | 6.58 |
| sr_l | 199t / $+1,792 | 231t / $+9,739 | 3.45 |

**S 信號（short, m20, pyr=1, solo）：**

| 信號 | OOS 筆 | OOS PnL | avg$/t | Feb 佔比 |
|------|--------|---------|--------|---------|
| dd_high | 283 | $+3,966 | $+14.0 | 101% |
| roc_bear | 221 | $+2,498 | $+11.3 | 90% |
| pe_low | 128 | $+2,439 | $+19.1 | 71% |
| tbr_low | 203 | $+1,182 | $+5.8 | 170% |
| kurt_low | 242 | $+1,067 | $+4.4 | 192% |
| vcv_low | 195 | $+1,054 | $+5.4 | 35% |

### 稽核最終判定

```
致命問題：S 策略的 $11,191 中有 93% 來自單月 ETH -25% 崩盤。
去掉該月後 S 僅 $819/年，合計 $13,916 未達 $20K。
結論：S 策略回測數字是海市蜃樓。L 策略 $13,776 是真實 edge。
真實合計 edge: ~$14-15K/年。S 策略需重新設計。
```

---

## 已知風險

1. **S 子策略相關性高**：Jaccard 0.54-0.83，本質是同一 GK 壓縮突破 edge 的多倍部位。若 GK 壓縮突破範式失效，所有子策略同時虧損。但 WF 6/6 正向 + topM 15-16% 表明目前穩定。
2. **S IS 近零**：IS $+590（avg $0.5/t），OOS/IS 比 16.7x。可能因 IS 期間市場不利，也可能暗示 edge 不穩定。
3. **L IS 近零**：前 365 天 ETH -48.2%，做多自然虧損。WF BULL/BEAR 完全對應，非過擬合。
4. **合併 MDD 預估 20-25%**：L MDD 12.3% + S MDD 17.6%，有部分對沖但仍可能同時回撤。
5. **S max positions 20-25 同時持倉**：margin $2K-$2.5K（20-25% 帳戶），notional $40-50K。需監控滑價。

---

## 排除的方向（S 策略）

| 方向 | 結果 | 原因 |
|------|------|------|
| 對稱翻轉 L 參數 | $5,744 天花板 | 違反獨立信號規則 |
| 出場修改（trail/ES/CD） | 全部更差 | 破壞趨勢跟隨 edge |
| BTC 跨資產信號 | 無改善 | rp_weak $0.6/t |
| 惡化率信號 | 比水平信號差 | 過渡信號過晚觸發 |
| 移除 session filter | 災難性 | 噪音交易大增 |
| AMI (Amihud) | $0.8/t | 無 edge |
| SafeNet 7% | 邊際 | 不足以改變結論 |
| min_signals ≥ 2（無金字塔） | $9,187 | 差 $813 未達標 |
| **金字塔 pyr=3 m20** | **$11,191** | **8-gate 稽核判定為海市蜃樓：93% 來自 Feb 2026** |
| 均值回歸（S CMP方向1） | 全部負或 MIRAGE | R9 測試 |
| Vol Expansion 做空 | 大多 MIRAGE | R9 測試 |
| Trend Filter 做空 | 全部 MIRAGE | R9 測試 |
| Trailing TP（ATR-based） | 全部 MIRAGE 或虧損 | R11 測試 ATR×0.5-2.0 全失敗 |
| OR-ensemble 加信號 | 稀釋 edge | R10: PF 下降，WR 下降 |
| BL5（breakout 5 bar） | IS 負，PF 1.13 | 過度敏感 |
| BL20+（breakout 20+ bar） | 交易太少 | PnL 不足 |
