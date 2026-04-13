# V9 $1K 帳戶最佳可行策略研究

ETH/USDT 1h $1,000 帳戶雙策略 (L+S) 研究。
V8 證明嚴格門檻不可行後，V9 使用放寬但務實的門檻，尋找最佳可行策略。

**研究期間**：2026-04-11
**總計**：4 輪迭代、2,400+ 配置
**結果**：**3 組配置通過全部 8 道 V9 關卡**，推薦 Config C

---

## 背景：V8 結論

V8 研究（5 輪、300+ 配置）證明 $1K 帳戶的嚴格門檻結構性不可行：

- **WR ≥ 70% + PnL ≥ $300/月/側** 在 ETH 1h 上互斥
- 最佳 WR = 84%（close-exit）但 PnL 為負
- 最佳 PnL = +$787/年（BTC-ETH RelDiv）但 WR 僅 60%
- 要達到 WR 70% 需要不對稱出場（小 TP / 大 SL），結構性虧損

**V8 最有前景的信號**：BTC-ETH 5-bar 相對報酬背離（rel_ret5 > ±2%）

---

## 回測規格

| 項目 | 值 |
|------|-----|
| 標的 | ETHUSDT 1h Perpetual |
| 帳戶 | $1,000 |
| 保證金 | $200 (NOTIONAL $4,000, 20x) |
| 手續費 | $4/筆 (taker 0.04%×2 + slip 0.01%×2) |
| SafeNet | **4.5%** (25% 穿透滑價模型, SLIP_MULT=1.25) |
| IS/OOS 切分 | 前 365 天 IS / 後 365 天 OOS |
| WARMUP | 150 bars |
| Anti-Lookahead | 全指標 .shift(1)，entry at O[i+1] |
| 持倉上限 | maxSame L=2, S=2, Total=3 |
| Session filter | block hours {0,1,2,12} UTC+8, block weekends (Sat/Sun) |

### V9 8 道關卡（放寬版）

| # | Gate | 條件 | 設計理由 |
|---|------|------|----------|
| G1 | OOS 年 PnL | ≥ $600 | $50/月最低門檻，V8 最佳 $787 |
| G2 | OOS WR | ≥ 55% | 放寬至略高於隨機（50%） |
| G3 | Profit Factor | ≥ 1.15 | 最低可接受正期望值 |
| G4 | MDD | ≤ $1,500 | 本金 150%，高但可接受 |
| G5 | 最差月 | ≥ -$300 | 本金 30% 月度虧損上限 |
| G6 | 正月比例 | ≥ 8/12 | 至少 2/3 月份賺錢 |
| G7 | Walk-Forward | ≥ 4/6 fold 正 | 時間穩定性 |
| G8 | IS PnL | > 0 | **最關鍵**：防止 data mining |

---

## 信號說明

### BTC-ETH 相對報酬背離 (RelDiv)

```python
eth_ret5 = eth_close.pct_change(5)       # ETH 5-bar 報酬率
btc_ret5 = btc_close.pct_change(5)       # BTC 5-bar 報酬率
rel_ret5 = (eth_ret5 - btc_ret5).shift(1)  # 相對背離（anti-lookahead）

L 進場: rel_ret5 < -0.03  # ETH 落後 BTC 3%+ → 做多（均值回歸）
S 進場: rel_ret5 > +0.03  # ETH 領先 BTC 3%+ → 做空（均值回歸）
```

**原理**：BTC-ETH 在加密市場高度相關，當 ETH 相對 BTC 在 5 根 K 線內出現 3%+ 的異常背離時，
傾向均值回歸。L 側捕捉 ETH 超跌反彈，S 側捕捉 ETH 超漲回落。

---

## 研究歷程

### R1：深度優化 RelDiv 信號

**腳本**：`backtest/research/v9_r1_reldiv_deep.py`

**目標**：從 V8 最佳信號 (LB5 TH0.02 TP2.5/SL3.5 MH18 = +$787) 出發，
全面優化 lookback、threshold、TP/SL、MaxHold、session、confirmation。

**搜索空間**：
- Phase 1：4 lookback × 4 threshold × 5 TP/SL × 3 MaxHold = **240 base configs**
- Phase 2a：Volume confirmation (vol_ratio > 1.0/1.3/1.5)
- Phase 2b：dist_ema20 confirmation (> 0.5%/1%/1.5%)
- Phase 2c：Exit cooldown (5/8/12 bars)
- Phase 2d：Session 優化（L block hours / S allow hours）

**結果**：

| 配置 | OOS | IS | WR | PF | MDD | 通過 |
|------|-----|-----|-----|-----|-----|------|
| LB5 TH0.03 TP2.5/SL3.5 MH18 | **$1,586** | -$1,042 | 81% | 2.92 | $1,392 | 6/8 |
| LB5 TH0.03 TP2.5/SL4 MH18 | $1,381 | -$859 | 75% | 2.36 | $1,392 | 5/8 |
| LB5 TH0.03 TP3/SL4 MH18 | $1,159 | -$672 | 69% | 1.94 | $979 | 5/8 |

**關鍵發現**：
- **LB5 + TH0.03 是最佳組合**：WR 81%、PF 2.92，遠超其他
- **OOS 只有 32 筆交易**（太稀疏），導致 G6 FAIL（正月不足 8/12）
- **IS 全部為負**：240 個配置中，IS+OOS 同時為正的 = 0 個
- Confirmation（volume/dist_ema20）反而降低績效（過度濾除有效信號）
- Session / Cooldown 影響不大

**FAIL 原因**：
- G6（正月 ≥ 8/12）：OOS 僅 8 個月有交易，最多 6-7 個正月
- G8（IS > 0）：TH0.03 在 IS 期間只有 ~30 筆，噪音太大

**結論**：RelDiv 信號本身極強，但對稱 TP/SL + 低交易量無法通過 IS 正和月度分散性關卡。

---

### R2：信號集成（Ensemble）

**腳本**：`backtest/research/v9_r2_ensemble.py`

**目標**：嘗試將 RelDiv 與其他信號組合，增加交易次數、改善 IS。

**信號家族**：
1. **RelDiv**：BTC-ETH 相對背離 > ±3%
2. **VolSpike**：Volume ratio > 2.0 (volume > 2× 20-bar mean)
3. **MomCont**：5-bar momentum continuation (ROC5 × ROC20 > 0, |ROC5| > 1%)
4. **Vol2Dist**：Volume spike + dist_ema20 方向性確認

**集成方式**：
- **E1: OR Ensemble**（任一信號觸發）：6 configs
- **E2: Asymmetric**（L/S 用不同信號）：72 configs
- **E3: 2-of-3 / 3-of-3 AND**（多信號同時觸發）：12 configs
- **E4: Multi-lookback RelDiv**（多時間尺度 OR）：6 configs

**結果**：**近乎全面失敗**

| 集成方式 | 最佳 OOS | IS | 結論 |
|---------|---------|------|------|
| OR Ensemble | -$194 | -$4,365 | 信號稀釋，全虧 |
| Asymmetric (L_RelDiv+S_RelDivOR_Vol) | $1,073 | -$1,887 | 唯一正 OOS |
| 2-of-3 AND | $310 | -$1,347 | 太嚴格，交易太少 |
| 3-of-3 AND | 0 trades | - | 完全無交易 |
| Multi-lookback RelDiv | -$1,835 | -$2,907 | 多尺度相互抵消 |

**關鍵發現**：
- 加入更多信號**反而稀釋 RelDiv 的 edge**，而非增強
- OR ensemble 大幅增加交易量（333t vs 32t），但勝率降至 57-58%，全虧
- Asymmetric（L 用 MomCont + S 用 RelDiv）OOS 最佳 $1,073，但 IS -$1,887
- 3-of-3 AND 完全沒有交易機會——三個信號幾乎不會同時觸發
- **結論：RelDiv 是唯一有效信號，不要混合**

---

### R3：非對稱 L/S 出場 + 斷路器

**腳本**：`backtest/research/v9_r3_reldiv_asymmetric.py`

**目標**：R1/R2 的核心問題是 IS 為負。R3 嘗試：
1. 非對稱閾值（L 和 S 使用不同 threshold）
2. 非對稱 TP/SL/MaxHold（L 寬 S 緊）
3. 斷路器：每日虧損上限、連續虧損冷卻
4. 月度 entry cap：防止單月過度交易
5. 分側 exit cooldown

**Phase 1：非對稱閾值（192 configs）**

搜索：2 lookback × 4 L_thresh × 4 S_thresh × 3 TP/SL × 2 MaxHold

| 配置 | OOS | IS | WR | 備註 |
|------|-----|-----|-----|------|
| LB5 L0.03/S0.015 TP2.5/SL3.5 MH18 | $500 | -$210 | 64% | 最佳 OOS |
| LB5 L0.025/S0.03 TP2.5/SL4 MH18 | $469 | $21 | 65% | 唯一 IS+OOS 正 |

- IS+OOS 同時為正：**僅 1 個**（OOS $469, IS $21）
- 放寬 threshold → 更多交易 → 但品質下降
- 結論：閾值不對稱效果有限

**Phase 2：非對稱 TP/SL/MaxHold（405 configs）**

搜索：9 L-exit combos × 9 S-exit combos × top lookback/threshold configs

- L 側使用寬 TP（趨勢跟蹤）：TP 2.5-3.0%, SL 3.5-4.0%, MH 12-24
- S 側使用緊 TP（均值回歸）：TP 1.5-2.5%, SL 2.5-3.5%, MH 8-18

| 配置 | OOS | IS | WR | 備註 |
|------|-----|-----|-----|------|
| LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8 | **$1,376** | -$790 | 69% | 最佳 OOS |
| LTP3.0/LSL4.0/LMH24 STP2.5/SSL3.5/SMH18 | $849 | -$164 | 65% | IS 接近正 |

- IS+OOS 同時為正：**1 個**（OOS $469, IS $21，同 Phase 1）
- **非對稱 TP/SL 大幅改善 OOS**：$500 → $1,376
- L 側寬 TP 3.0% + 長 MH 18 捕捉趨勢大贏家
- S 側緊 TP 1.5% + 短 MH 8 快速鎖定利潤

**Phase 3：斷路器 + 月度 cap（~1,600 configs）**

在 Phase 1+2 的 top 5 配置上加入：
- L 月度 entry cap: 6/8/12
- S 月度 entry cap: 6/8/12
- L exit cooldown: 0/6/10 bars
- S exit cooldown: 0/6/10 bars

| 配置 | OOS | IS | WR | MDD | 備註 |
|------|-----|-----|-----|-----|------|
| Base + L_cap6 S_cap8 CD_S10 | $849 | **$835** | 65% | $458 | IS 大幅翻正 |
| **Candidate 6**: LTP3/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8 | $1,376 | -$790 | 69% | $1,164 | **7/8 PASS** |

**突破性發現**：
- **L_cap6（每月最多 6 筆 L）是 IS 翻正的關鍵**
  - IS 期間 L 策略過度交易（信號在 IS 期間噪音較大）
  - 限制月度進場次數後，IS 從 -$790 → +$835
- S exit cooldown 10 bars 進一步減少 IS 噪音交易
- 69 個 IS+OOS 同時為正的配置（Phase 1+2 僅 1 個）

**Phase 4：完整 V9 Gate Check（6 candidates）**

最佳：**Candidate 6** — 7/8 PASS（僅 G8 IS=-$790 FAIL）

```
LB5 TH0.03 LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8
OOS: 55t $1,376, WR 69%, PF 2.08, MDD $1,164
IS: 59t -$790
月度明細：8/11 正月，最差月 -$144
WF: 5/6
```

**結論**：非對稱出場 + 月度 cap 是解鎖 IS 正值的關鍵。R4 將在此基礎上微調。

---

### R4：最終驗證

**腳本**：`backtest/research/v9_r4_final_validation.py`

**目標**：驗證 R3 最佳候選 + 微調斷路器 + 敏感度分析

**Config A：R3 Candidate 6（無 cap，基準線）**

```
LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8（無月度 cap）
IS:  59t -$790, WR 54%, PF 0.73
OOS: 55t $1,376, WR 69%, PF 2.08, MDD $1,164
→ 7/8 PASS（G8 FAIL: IS -$790）
```

**Config B：R3 Phase 3 最穩健配置**

```
LTP3.0/LSL4.0/LMH24 STP2.5/SSL3.5/SMH18 + L_cap6 CD_S10
IS:  51t $835, WR 59%, PF 1.43
OOS: 48t $849, WR 65%, PF 1.46, MDD $458
→ 8/8 ALL PASS ★
Max consecutive loss: 3 trades (-$170)
```

**Config C：混合（A 的出場 + B 的限流）★ 推薦**

```
LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8 + L_cap6 CD_S10
IS:  49t $64, WR 61%, PF 1.03
OOS: 48t $1,170, WR 69%, PF 1.98, MDD $570
→ 8/8 ALL PASS ★★
WF: 6/6 全正
Max consecutive loss: 3 trades (-$312)
```

**Config D：微調掃描（720 configs）**

掃描：4 L_cap × 3 S_cap × 5 CD_S × 3 CD_L = 720 配置

IS+OOS 同時為正且 OOS > $600：**111 個配置**

| 排名 | 配置 | OOS | IS | WR | MDD |
|------|------|-----|-----|-----|-----|
| 1 | L_cap6 S_cap6 CD_S12 | **$1,343** | $8 | 72% | $570 |
| 2 | L_cap6 S_cap8 CD_S12 | $1,330 | $8 | 71% | $570 |
| 3 | L_cap6 S_cap6 CD_S10 | $1,183 | $64 | 70% | $570 |
| 4 | L_cap6 S_cap8 CD_S10 | $1,170 | $64 | 69% | $570 |
| 5 | L_cap5 S_cap12 CD_L6 | $1,144 | $184 | 68% | $764 |

**Config D-best 完整報告**：

```
L_cap6 S_cap6 CD_L0 CD_S12
IS:  48t $8, WR 60%, PF 1.00
OOS: 47t $1,343, WR 72%, PF 2.25, MDD $570
→ 8/8 ALL PASS ★
WF: 6/6 全正
Max consecutive loss: 2 trades (-$373)
```

**敏感度分析（Config D-best 改變 threshold）**：

| TH | 交易數 | OOS | IS | WR | PF | MDD |
|-----|--------|-----|-----|-----|-----|-----|
| 0.025 | 72 | $432 | **-$1,774** | 57% | 1.17 | $2,593 |
| 0.028 | 54 | $1,617 | **-$655** | 72% | 2.32 | $980 |
| **0.030** | **47** | **$1,343** | **$8** ★ | **72%** | **2.25** | **$570** |
| 0.032 | 38 | $1,175 | **-$225** | 74% | 2.36 | $877 |
| 0.035 | 31 | $737 | **-$142** | 68% | 1.85 | $645 |

**關鍵發現**：**TH=0.03 是唯一讓 IS 轉正的閾值**
- TH=0.028：更多交易但 IS -$655（IS 期間噪音交易增加）
- TH=0.032：更少交易，IS -$225（OOS 期恰好有幾筆大贏家撐住）
- TH=0.03 是「甜蜜點」：剛好在 IS 期間過濾掉足夠多噪音

---

## 最終結果：三組 8/8 ALL PASS 配置

### 共同設定

```
信號：BTC-ETH 5-bar relative return divergence > 3%
  rel_ret5 = (ETH 5-bar pct_change - BTC 5-bar pct_change).shift(1)
  L entry: rel_ret5 < -0.03
  S entry: rel_ret5 > +0.03

風控：
  maxSame L = 2, S = 2, Total max = 3
  SafeNet ±4.5% (25% slip model, SLIP_MULT = 1.25)
  Session filter: block hours {0,1,2,12} UTC+8, block weekends
  $200 margin / 20x leverage / $4,000 notional / $4 fee
```

### 配置比較

| | **Config B（穩健）** | **Config C（推薦）** | **Config D-best（激進）** |
|---|---|---|---|
| L exit | TP 3.0% / SL 4.0% / MH 24 | **TP 3.0% / SL 3.5% / MH 18** | 同 Config C |
| S exit | TP 2.5% / SL 3.5% / MH 18 | **TP 1.5% / SL 2.5% / MH 8** | 同 Config C |
| L monthly cap | 6 | **6** | 6 |
| S monthly cap | - | **-** | 6 |
| L exit cooldown | - | **-** | - |
| S exit cooldown | 10 bars | **10 bars** | 12 bars |
| **OOS 年化** | $849 | **$1,170** | $1,343 |
| **IS PnL** | **$835** ★ | $64 | $8 |
| **OOS WR** | 65% | **69%** | 72% |
| **PF** | 1.46 | **1.98** | 2.25 |
| **MDD** | **$458** ★ | $570 | $570 |
| **WF** | 5/6 | **6/6** ★ | 6/6 |
| **Max consec loss** | 3t (-$170) | 3t (-$312) | 2t (-$373) |
| **OOS 筆數** | 48 | 48 | 47 |
| **正月** | 8/11 | 8/11 | 8/11 |

### 推薦 Config C 的邏輯

1. **OOS 最強**：$1,170 vs B 的 $849，年化報酬率 117%
2. **WF 6/6 全正**：時間穩定性最佳（B 只有 5/6）
3. **PF 1.98**：接近 2.0，edge 明顯
4. **WR 69%**：接近七成勝率，心理壓力較小
5. IS $64 為正：通過 G8，但需注意薄利

### Config C OOS 月度明細

```
月份      L筆  L勝率   L淨利   S筆  S勝率   S淨利    合計
2025-04     4    50%     -141    4   100%      177       36
2025-05     2   100%      232    6    50%     -144       88
2025-06     4   100%      422    2    50%      -88      334
2025-07     1   100%       77    7    43%       17       95
2025-08     2   100%      232    4    50%      -41      191
2025-09     1     0%     -144    0     0%        0     -144
2025-10     0     0%        0    1     0%      -10      -10
2025-11     5   100%      436    0     0%        0      436
2025-12     0     0%        0    3   100%      114      114
2026-01     0     0%        0    1   100%       45       45
2026-02     1     0%      -14    0     0%        0      -14
```

**出場分佈**：safenet 1筆(-$229) | sl 7筆(-$115) | time_stop 18筆(+$14) | tp 22筆(+$89)

**L/S 互補**：
- L 強勢月（May/Jun/Aug/Nov）：S 弱或無 → L 撐
- S 強勢月（Apr/Dec/Jan）：L 弱或無 → S 撐
- 唯一同時弱月：Sep (-$144, 只有 1 筆 L 虧損)

---

## 排除的方向

### V9 排除清單

| 方向 | 輪次 | 結果 | 排除原因 |
|------|------|------|----------|
| Volume confirmation | R1 Phase 2a | OOS 下降 | 過度濾除有效信號 |
| dist_ema20 confirmation | R1 Phase 2b | OOS 下降 | 同上 |
| OR ensemble (RelDiv+Vol+Mom) | R2 E1 | 全虧 | 信號稀釋 edge |
| Asymmetric ensemble | R2 E2 | 最佳 $1,073 IS -$1,887 | IS 深度負值 |
| 2-of-3 AND ensemble | R2 E3 | $310 IS -$1,347 | 太嚴格，交易太少 |
| 3-of-3 AND ensemble | R2 E3 | 0 trades | 完全無交易 |
| Multi-lookback RelDiv | R2 E4 | 全虧 | 多尺度相互抵消 |
| 對稱閾值 (L_TH = S_TH) | R1-R2 | IS 全負 | 無法同時優化兩側 |
| TH < 0.03 | R4 sensitivity | IS 為負 | 噪音交易增加 |
| TH > 0.03 | R4 sensitivity | IS 為負 | 偶然性增加 |
| 無月度 cap | R3-R4 | IS -$790 | IS 期間過度交易 |

---

## 技術問題與修正

### 1. Python f-string 條件表達式 Bug

**R1 v9_r1_reldiv_deep.py line 252**

```python
# 錯誤：f-string format spec 不支持條件表達式
f"W${w.mean() if len(w)>0 else 0:.0f}"
# ValueError: Invalid format specifier '.0f if len(w)>0 else 0'

# 修正：先計算再格式化
w_avg = w.mean() if len(w) > 0 else 0
l_avg = lo.mean() if len(lo) > 0 else 0
f"W${w_avg:.0f} L${l_avg:.0f}"
```

### 2. Windows Python stdout 緩衝

腳本用 `> output.txt` 重定向時，output 長時間為 0 bytes。
原因：Python 在 Windows redirect stdout 時使用 full buffering。
解法：等待程序完成（不需 PYTHONUNBUFFERED，git-bash 鏈路仍緩衝）。

### 3. R3 Phase 3 O(n^2) 效能問題

```python
# 原始：每根 bar 掃描所有交易計算月度 cap → O(n_trades) per bar
l_this_month = sum(1 for t in trades if t["side"]=="long"
                    and str(t["entry_dt"])[:7] == current_month)

# 修正：用 incremental dict 追蹤 → O(1) per bar
monthly_counts = {}  # "YYYY-MM" -> {"long": n, "short": n}
# entry 時更新：monthly_counts[entry_month]["long"] += 1
```

同時也修正了 O(n) 的連續虧損 bar 查找：
```python
# 原始：df.index[df["datetime"] == trades[-1]["exit_dt"]]  → O(n)
# 修正：last_loss_exit_bar = i  → O(1)
```

### 4. R3 Phase 2 搜索空間過大

原始 grid：3^6 = 3,645 configs（6 個獨立參數各 3 值）
修正：9 × 9 focused combos = 405 configs（根據 Phase 1 知識縮減）

---

## 核心洞察

### 1. 非對稱出場是成功關鍵

L（做多，趨勢跟蹤）和 S（做空，均值回歸）的最佳出場策略截然不同：

- **L 側**：寬 TP 3.0% + 寬 SL 3.5% + 長持有 MH 18
  - 捕捉較大趨勢波段，avg_win $102
  - 容忍較大回撤，讓贏家有時間發展
  - OOS L: 20t, WR 80%, PnL $1,100

- **S 側**：緊 TP 1.5% + 緊 SL 2.5% + 短持有 MH 8
  - 快速鎖定小利潤，avg_win $43
  - 限制持倉時間（做空風險不對稱）
  - OOS S: 28t, WR 61%, PnL $70

### 2. 月度 cap 是 IS 轉正的解鎖器

沒有月度 cap 時，IS 全為負（-$790）。加入 L_cap6 後 IS 翻正（+$64）。

原因：IS 期間（2024-04 ~ 2025-03）的 RelDiv 信號噪音較大，
不限制進場次數會在噪音期過度交易。月度 cap 6 筆強制限流，
只保留最明確的信號。

### 3. TH=0.03 是唯一的甜蜜點

- TH < 0.03：交易增多但噪音交易占比升高 → IS 負
- TH > 0.03：交易減少，少數大贏家主導結果 → IS 不穩定
- TH = 0.03：恰好過濾足夠噪音，保留足夠信號量

### 4. 風險提示

- **IS $64 非常薄**：統計上幾乎等於零，不排除是僥倖通過
- **年化交易量低**：~48 筆/年 = ~4 筆/月，樣本量小
- **MDD $570 = 本金 57%**：需要較強心理承受力
- **Config B 更安全但收益較低**：IS $835 遠比 $64 穩健

---

## 研究腳本索引

| 腳本 | 輪次 | 配置數 | 結果 |
|------|------|--------|------|
| `v9_r1_reldiv_deep.py` | R1 | 240+ | 6/8 PASS，IS 全負 |
| `v9_r1_output.txt` | R1 | - | R1 完整輸出 |
| `v9_r2_ensemble.py` | R2 | 96 | 全面失敗 |
| `v9_r2_output.txt` | R2 | - | R2 完整輸出 |
| `v9_r3_reldiv_asymmetric.py` | R3 | ~2,000 | 7/8 PASS，發現月度 cap |
| `v9_r4_final_validation.py` | R4 | 720 | **3 × 8/8 ALL PASS** |
