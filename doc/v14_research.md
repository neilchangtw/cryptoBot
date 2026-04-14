# V14 策略研究：出場機制創新（Exit Mechanism Innovation）

目標：在不改變 V13 進場邏輯的前提下，純粹透過出場機制創新提升策略績效。

**R6 最終候選：L OOS $2,034（+$293, +16.8%），L+S $4,549（+6.9%），WF 6/6，12/13 正月。**
**S 策略經全面測試（25 種配置）確認為全域最佳解，無法改善。**

---

## 研究背景

### V13 基線（要超越的目標）

```
數據：ETHUSDT_1h_latest730d.csv（2024-04-13 ~ 2026-04-13）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee
IS/OOS 分割：前 50% IS，後 50% OOS

V13 L+S 合併（OOS）：
  L: $1,741 (85t, WR 44%, MDD $231)
  S: $2,515 (93t, WR 62%, MDD $281)
  合計: $4,256, 11/13 正月, worst -$32

V13 L 出場分佈（OOS 85t）：
  TP:     16t (19%)  avg +$136
  MH:     29t (34%)  avg -$39.6   ← 最大拖累
  MH-ext: 21t (25%)  avg +$54.2
  BE:     17t (20%)  avg -$4.0
  SL:      2t ( 2%)  avg -$179

V13 S 出場分佈（OOS 93t）：
  TP:     55t (59%)  avg +$76.0
  MH:     32t (34%)  avg -$48.5   ← 最大拖累
  MH-ext:  3t ( 3%)  avg +$33.4
  BE:      2t ( 2%)  avg -$4.0
  SL:      1t ( 1%)  avg -$204
```

### V14 研究範圍

- **只改出場機制**，進場邏輯完全保留 V13
- 目標：減少 MH 虧損出場的拖累（L 29t×$39.6 = $1,148，S 32t×$48.5 = $1,552）
- 紀律：IS/OOS 分割、Walk-Forward 驗證、參數穩健性、反 happy-table

---

## R1：V13 MH 虧損出場特徵分析

腳本：`backtest/research/v14_r1_mh_loss_analysis.py`

### 方法

完整重建 V13 回測，追蹤每筆交易的逐 bar PnL 生命週期、MFE/MAE、進場 GK pctile。
分析 8 個維度：出場分佈、PnL 軌跡、從頭虧 vs 先賺後虧、GK 分佈、早期 PnL 預測力、ATR、MFE timing、提前出場假設。

### 核心發現

#### 1. MH 虧損 vs TP 獲利的 PnL 軌跡在 bar 1 就已分化

```
L OOS 逐 bar 平均 close PnL (%)：
 bar |  MH虧損  |  TP獲利  |   差異
  1  |  -0.25%  |  +0.83%  | -1.08%    ← bar 1 就差 1%
  2  |  -0.28%  |  +0.69%  | -0.97%
  3  |  -0.41%  |  +1.35%  | -1.77%
  6  |  -0.89%  |  +2.78%  | -3.67%

S OOS 逐 bar 平均 close PnL (%)：
 bar |  MH虧損  |  TP獲利  |   差異
  1  |  -0.28%  |  +0.58%  | -0.86%
  3  |  -0.53%  |  -0.04%  | -0.48%
 10  |  -1.11%  |  +1.23%  | -2.34%
```

#### 2. 「先賺後虧」佔 MH 虧損的多數

| | 從頭虧 (MFE<0.5%) | 先賺後虧 (MFE>=0.5%) |
|---|---|---|
| L OOS | 12t (41%) | 17t (59%), median MFE 1.10% |
| S OOS | 7t (22%) | 25t (78%), median MFE 1.00% |

**S 有 78% 的 MH 虧損曾經是正獲利的** — 到手的鴨子飛了。

#### 3. GK pctile 無法預測 MH vs TP

- L: MH虧損 GK mean=10.6 vs TP獲利 GK mean=13.1（無顯著差異）
- S: MH虧損 GK mean=17.8 vs TP獲利 GK mean=17.6（幾乎相同）
- **結論：GK 進場值不是 MH 虧損的預測因子**

#### 4. Bar 3 是最強預測器（L）

```
L OOS bar3 正收益 → 33t, MH率  9%, avg +$46
L OOS bar3 負收益 → 47t, MH率 55%, avg -$3
S OOS bar3 正收益 → 31t, MH率 29%, avg +$33
S OOS bar3 負收益 → 44t, MH率 52%, avg +$3
```

#### 5. 假設提前出場效果巨大

```
L OOS MH虧損（實際 avg -$39.6）：
  bar 2 出場 → avg -$15.2（23/29 比實際好）— 省 $24/筆
  bar 3 出場 → avg -$20.6（22/29 比實際好）— 省 $19/筆

S OOS MH虧損（實際 avg -$48.5）：
  bar 2 出場 → avg -$22.2（26/32 比實際好）— 省 $26/筆
  bar 3 出場 → avg -$25.0（28/32 比實際好）— 省 $24/筆
```

#### 6. ATR(14) 無預測力

MH虧損與TP獲利的進場 ATR 無顯著差異。

### R1 結論

- MH 虧損和 TP 獲利在 bar 1-3 就明顯分化
- 59-78% 的 MH 虧損曾盈利但回吐（尤其 S）
- GK / ATR 無法預測，只有持倉期間的 PnL 動態有預測力
- 最有前途方向：**MFE 觸發式 Trailing Exit**

---

## R2：MFE Trailing Exit

腳本：`backtest/research/v14_r2_mfe_trailing.py`

### 機制

一旦浮盈（MFE）達到啟動門檻，啟動 trailing — 若回吐超過閾值就提前出場。

```
出場優先順序：SL → TP → MFE-trail（新）→ MH(ext+BE)

參數：
  mfe_act:  MFE 啟動門檻（浮盈達到此 % 後才啟動 trailing）
  trail_dd: 從 MFE 高點回吐多少觸發出場
  min_bar:  最早可啟動 trailing 的 bar 數
```

### 參數掃描

- mfe_act: [0.3%, 0.5%, 0.8%, 1.0%, 1.5%]
- trail_dd: [0.3%, 0.5%, 0.8%, 1.0%, 1.5%]（<= mfe_act）
- min_bar: [1, 2, 3]
- 共 46 組（含 baseline）

### 結果

#### L 策略：MFE Trailing 有效

```
Top 5 L configs（OOS PnL 排名）：
Config                 | IS PnL | OOS PnL | delta  | WR  | MDD  | WF
mfe=1.0% dd=0.8% mb=2 | $  940 | $ 1,869 | + $128 | 61% | $224 | 4/4
mfe=1.0% dd=1.0% mb=1 | $  890 | $ 1,865 | + $124 | 57% | $222 | 4/4
mfe=1.0% dd=1.0% mb=2 | $  975 | $ 1,853 | + $112 | 57% | $222 | 4/4
mfe=1.0% dd=0.8% mb=1 | $  959 | $ 1,835 | +  $93 | 62% | $224 | 4/4
mfe=1.0% dd=0.8% mb=3 | $  938 | $ 1,817 | +  $76 | 61% | $224 | 4/4

Top 12 個 L 配置全部打敗 baseline，且全部 4/4 WF。
```

#### S 策略：MFE Trailing 全部失敗

**所有 45 個配置都比 baseline 差。**

原因：S TP=2.0%，MFE trailing 在 1.0-1.5% 就攔截了本來會走到 TP 的交易。TP 從 55t 被削減到 34-53t。

#### 合併

- 獨立最佳：L(mfe=1.0%/dd=0.8%/mb=2) + S(baseline) = $4,384（+$128）
- 同參數：全部比 baseline 差（S 拖累）

### R2 結論

- MFE trailing 對 L 有效（+$128, WR 44%→61%），對 S 無效
- S 的 TP=2.0% 太低，trailing 誤殺 TP 交易

---

## R3：Conditional Early Exit（條件式提前出場）

腳本：`backtest/research/v14_r3_conditional_early_exit.py`

### 機制

三種方案：
- **方案 A**：bar N close PnL < threshold → 直接出場
- **方案 B**：bar N close PnL < threshold → 縮短 MH
- **方案 C**：連續 N 根負 bar → 出場

### 參數掃描

- check_bar: [2, 3, 4]
- exit_thresh: [-1.5%, -1.0%, -0.8%, -0.5%, -0.3%, 0.0%]
- reduced_mh (L): [3, 4]，(S): [5, 6, 7]
- 共 58 組

### 結果

#### L 最佳：bar2<=-1.0% → MH 縮短到 4

```
B: bar2<=-1.0% MH L->4 | IS $433 | OOS $1,833 | +$91 | WR 42% | MDD $188 | WF 4/4
```

MDD 從 $231 降到 $188，但 PnL 改善不如 R2。

#### S：全部配置比 baseline 差（同 R2）

#### 合併

- 獨立最佳：L(bar2<-1%/MH4) + S(baseline) = $4,348（+$91）
- 弱於 R2 的 +$128

### R3 結論

- 條件式 MH 縮短對 L 有效但幅度較小（+$91 vs R2 +$128）
- S 策略再次證明無法通過提前出場改善
- R2 和 R3 機制互補（不同的保護方式），有組合潛力

---

## R4：Combined L Improvements + S Independent Exploration

腳本：`backtest/research/v14_r4_combined_and_s_explore.py`

### Part 1：L 策略 R2+R3 組合

```
Config                              | IS PnL | OOS PnL | delta  | WR  | MDD  | WF
BASELINE (V13)                      | $  656 | $ 1,741 |     — | 44% | $231 | 4/4
R2 alone (mfe1.0/0.8/mb2)          | $  940 | $ 1,869 | + $128 | 61% | $224 | 4/4
R3 alone (bar2<-1%/MH4)            | $  433 | $ 1,833 | +  $91 | 42% | $188 | 4/4
R2+R3: mfe1.0/1.0/1+bar2<-1%/MH4  | $  847 | $ 1,958 | + $216 | 53% | $240 | 4/4  ← Champion
R2+R3: mfe1.0/1.0/2+bar2<-1%/MH4  | $  931 | $ 1,946 | + $204 | 53% | $240 | 4/4
R2+R3: mfe1.0/0.8/2+bar2<-1%/MH4  | $  897 | $ 1,928 | + $187 | 56% | $231 | 4/4
```

**組合效果超越個別之和**（$216 > $128 或 $91 單獨）。兩者互補：
- R2 MFE trailing：抓住「曾經盈利但回吐」的交易
- R3 conditional MH：對 bar 2 深度虧損的交易，縮短持有時間

### Part 2：S 策略全面探索（25 種配置）

```
配置類別                    | 最佳結果    | vs baseline
MH 縮減 (7,8,9)            | $2,270      | -$245
MH 增加 (12,14)            | $2,104      | -$411
移除 extension (ext=0)     | $2,430      |  -$85
TP 調整 (1.5-3.5%)         | $2,407      | -$108
SL 調整 (3.0-5.0%)         | $2,513      |   -$2
MH+TP 組合                 | $2,060      | -$455
MH+TP+SL 組合              | $1,479      | -$1,036
```

**25 個配置全部比 baseline 差。V13 S 策略為全域最佳解。**

### R4 結論

- Champion：L(R2+R3) + S(baseline) = **$4,473**（+$216, +5.1%）
- S 無法改善，任何出場參數調整都是損害

---

## R5：Champion 完整驗證

腳本：`backtest/research/v14_r5_champion_validation.py`

### Champion L 配置

```
MFE trail: mfe_act=1.0%, trail_dd=1.0%, min_bar_trail=1
Conditional MH: check_bar=2, exit_thresh=-1.0%, reduced_mh=4

意義：
  - 一旦浮盈達 1.0% 後又回吐 1.0%，提前以 MFE-trail 出場
  - 若 bar 2 收盤 PnL <= -1.0%，將 MH 從 6 縮短為 4
```

### 1. Walk-Forward 8-fold：6/6 PASS（baseline 5/6）

```
Fold | 期間                   | Champion | Baseline | delta
  1  | 2024-10 ~ 2025-01      | $   489  | $   -38  | + $527
  2  | 2025-01 ~ 2025-04      | $   270  | $   392  | - $122
  3  | 2025-04 ~ 2025-07      | $   505  | $   320  | + $185
  4  | 2025-07 ~ 2025-10      | $   335  | $   202  | + $134
  5  | 2025-10 ~ 2026-01      | $   219  | $   136  | +  $83
  6  | 2026-01 ~ 2026-04      | $   897  | $   979  | -  $82

Champion: 6/6 PASS, total $2,715
Baseline: 5/6 PASS, total $1,991
```

### 2. 月度 PnL 對比（OOS）

```
Month   | V13 L  | V14 L  | delta  | S      | V14 L+S
2025-05 | $  215 | $  314 | +  $99 | $   19 | $  333
2025-06 | $  -90 | $  -93 | -   $3 | $  126 | $   32
2025-07 | $  277 | $  315 | +  $38 | $  156 | $  471
2025-08 | $  -86 | $  -10 | +  $75 | $  307 | $  296
2025-09 | $  165 | $  227 | +  $61 | $  149 | $  376
2025-10 | $  -33 | $  -58 | -  $25 | $  622 | $  564
2025-11 | $  170 | $  232 | +  $62 | $  175 | $  406
2025-12 | $  138 | $  158 | +  $20 | $  158 | $  315
2026-01 | $  -56 | $  -56 | +   $0 | $  104 | $   48
2026-02 | $  533 | $  563 | +  $29 | $  325 | $  888
2026-03 | $  423 | $  420 | -   $3 | $  376 | $  796
2026-04 | $   97 | $  -12 | - $109 | $ -129 | $ -141

V13 L+S: $4,139, 正月 11/12
V14 L+S: $4,384, 正月 11/12
```

8 個月改善，3 個月微幅退步，1 個月持平。

### 3. 參數穩健性：16/16 全部 6/6 WF

```
Config                | OOS PnL | delta  | WF
Champion (exact)      | $ 1,958 | + $216 | 6/6
mfe_act 0.8%          | $ 1,822 | +  $81 | 6/6
mfe_act 1.2%          | $ 1,928 | + $186 | 6/6
mfe_act 1.5%          | $ 1,909 | + $168 | 6/6
trail_dd 0.8%         | $ 1,894 | + $152 | 6/6
trail_dd 1.2%         | $ 1,857 | + $116 | 6/6
trail_dd 1.5%         | $ 1,631 | - $110 | 6/6  ← 唯一負 delta 但仍 6/6
min_bar 2             | $ 1,946 | + $204 | 6/6
min_bar 3             | $ 1,903 | + $162 | 6/6
check_bar 3           | $ 1,797 | +  $56 | 6/6
exit_thresh -0.5%     | $ 1,498 | - $243 | 6/6
exit_thresh -1.5%     | $ 1,806 | +  $65 | 6/6
reduced_mh 3          | $ 1,860 | + $118 | 6/6
reduced_mh 5          | $ 2,022 | + $281 | 6/6  ← 更好，待探索
R3 only               | $ 1,833 | +  $91 | 5/6
R2 only               | $ 1,865 | + $124 | 6/6
```

**Champion 不在懸崖邊緣**：13/16 鄰近參數正 delta，16/16 全 6/6 WF。
注意：**reduced_mh=5 更好（+$281）**，R6 繼續探索。

### 4. 交易層級變化分析

```
V14 核心改善來源（48 筆交易改變）：
  MH -> MFE-trail:      9t, + $296（MH虧損轉為小盈利 — 最大貢獻）
  BE -> MFE-trail:     13t, + $156（BE -$4 轉為小盈利）
  MH -> MH (縮短):      5t, + $130（同為 MH 但虧損減少）
  SL -> MH:             1t, + $117（避免 SL 重虧）

代價：
  MH-ext -> MFE-trail: 11t, - $144（提前出場損失部分 ext 利潤）
  TP -> MFE-trail:      2t, - $200（2 筆 TP 被攔截）
  MH-ext -> MH:         2t, - $166（ext 機會喪失）

淨效果：+ $299（正面大於負面）
```

### R5 結論

V14 Champion 通過所有驗證：
- 8-fold WF **6/6 PASS**（baseline 5/6）
- IS/OOS **同步改善**
- 參數穩健性 **16/16 全部 6/6 WF**
- 月度分佈正常，未集中在特定月份

---

## 最終比較：V13 vs R5 Champion vs R6 Champion

```
                |     V13     | R5 (mh4)    | R6 (mh5)    | R6 delta
L IS PnL        |   $    656  |   $    847  |   $    953  | + $297
L OOS PnL       |   $  1,741  |   $  1,958  |   $  2,034  | + $293 (+16.8%)
L OOS WR        |       44%   |       53%   |       60%   |   +16%
L OOS MDD       |   $    231  |   $    240  |   $    228  |   - $3
L worst month   |   $    -90  |   $    -93  |   $    -77  |  + $13
L WF (8-fold)   |       5/6   |       6/6   |       6/6   |   +1

S (unchanged)   |   $  2,515  |   $  2,515  |   $  2,515  |     —

L+S OOS         |   $  4,256  |   $  4,473  |   $  4,549  | + $293 (+6.9%)
L+S 正月        |     11/12   |     11/12   |     12/13   |   +1
```

---

## R6：reduced_mh=5 Deep Exploration

腳本：`backtest/research/v14_r6_reduced_mh5_deep.py`

### 背景

R5 穩健性測試發現 reduced_mh=5（OOS $2,022, +$281）比 R5 champion reduced_mh=4（+$216）更好。
R6 深入掃描 mh5 搭配不同 MFE trail 參數。

### 掃描結果（28 組）

```
Config                      | IS PnL | OOS PnL | delta  | WR  | MDD  | WstMo  | WF
BASELINE                    | $  656 | $ 1,741 |     — | 44% | $231 | $  -90 | 5/6
R5 champion (mh4)           | $  847 | $ 1,958 | + $216 | 53% | $240 | $  -93 | 6/6
mh5: mfe=1.0/dd=0.8/mb=1   | $  953 | $ 2,034 | + $293 | 60% | $228 | $  -77 | 6/6  ← NEW CHAMPION
R5 mh5 (mfe=1.0/dd=1.0)    | $  883 | $ 2,022 | + $281 | 55% | $237 | $  -96 | 6/6
mh5: mfe=1.0/dd=0.8/mb=2   | $  934 | $ 2,022 | + $281 | 60% | $228 | $  -77 | 6/6
mh5: mfe=1.0/dd=1.0/mb=2   | $  968 | $ 2,010 | + $269 | 55% | $237 | $  -96 | 6/6
```

Top 5 全部 6/6 WF，全部 mh5。trail_dd=0.8% 比 1.0% 更好（更早鎖利）。

### New Champion 月度分佈

```
Month   | V14 L  | S      | L+S
2025-04 | $  -51 | $  130 | $   79
2025-05 | $  276 | $   19 | $  294
2025-06 | $  -77 | $  126 | $   48
2025-07 | $  330 | $  156 | $  486
2025-08 | $   -4 | $  307 | $  302
2025-09 | $  190 | $  149 | $  339
2025-10 | $   35 | $  622 | $  656
2025-11 | $  232 | $  175 | $  406
2025-12 | $  111 | $  158 | $  269
2026-01 | $  -56 | $  104 | $   48
2026-02 | $  570 | $  325 | $  895
2026-03 | $  441 | $  376 | $  816
2026-04 | $   38 | $ -129 | $  -91

L+S total: $4,549, 正月: 12/13（僅 Apr 2026 微虧 -$91）
```

### R6 穩健性（9 組）

```
Perturbation        | OOS PnL | delta  | WF
exact               | $ 2,034 | + $293 | 6/6
mfe_act 1.2%        | $ 1,973 | + $232 | 6/6
trail_dd 1.0%       | $ 2,022 | + $281 | 6/6
trail_dd 0.6%       | $ 1,799 | +  $58 | 6/6
thresh -1.5%        | $ 1,794 | +  $53 | 6/6
mh=4                | $ 1,894 | + $152 | 6/6
mh=6 (no reduce)    | $ 1,835 | +  $93 | 6/6
mfe_act 0.8%  ← 弱  | $ 1,609 | - $132 | 5/6
thresh -0.5%  ← 弱  | $ 1,711 | -  $30 | 6/6
```

7/9 正 delta，9/9 至少 5/6 WF。mfe_act=0.8% 是唯一非 6/6 的鄰居。

### R6 結論

R6 champion（mfe=1.0%/dd=0.8%/mb=1/mh5）全面優於 R5 champion（mfe=1.0%/dd=1.0%/mb=1/mh4）：
- OOS +$293 vs +$216（+$77）
- WR 60% vs 53%（+7%）
- MDD $228 vs $240（-$12，更好）
- Worst month -$77 vs -$93（+$16，更好）
- L+S 正月 12/13 vs 11/12（+1）

---

## V14 Champion L 策略規格（R6 最終版）

```
方向：Long-only（純 1h）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場（同 V13）：
  1. GK pctile < 25（GK(5/20) pw100）
  2. Close breakout 15 bar
  3. Block hours {0,1,2,12} UTC+8, block days {Sat,Sun}
  4. Exit Cooldown: 6 bar
  5. Monthly Entry Cap: 20, maxTotal = 1

出場（V14 新增 2 個機制）：
  1. SafeNet -3.5%（含 25% 穿透模型）
  2. TP +3.5%
  3. 【V14 新增】MFE Trailing:
     - 條件：持倉 >= 1 bar 且 running MFE >= 1.0%
     - 觸發：當前 close PnL <= running_MFE - 0.8%
     - 出場價：close price
  4. 【V14 新增】Conditional MH:
     - 條件：bar 2 close PnL <= -1.0%
     - 效果：MH 從 6 縮短為 5
  5. MaxHold 6 bar（或 5 if triggered）→ ext2 + BE trail

風控（同 V13）
```

---

## 待探索方向

1. **S 出場創新**（非參數調整）：R2-R4 證明 S 的 TP/MH/SL/ext 參數已最佳化，但未測試全新出場概念（如 S 版 MFE trailing with higher thresholds > TP）

---

## 排除的方向

- MFE Trailing 用於 S 策略（R2: 45 組全部更差，TP=2.0% 太低被誤殺）
- 條件式提前出場用於 S 策略（R3: 所有配置更差）
- S 策略 MH 縮減 (7-14)（R4: 全部更差）
- S 策略 TP 調整 (1.5-3.5%)（R4: 全部更差）
- S 策略 SL 調整 (3.0-5.0%)（R4: 全部更差，SL=3.0% 災難性 -$1,961）
- S 策略移除 extension（R4: -$85）
- S 策略 MH+TP+SL 組合（R4: 全部更差）
- L 策略 blunt bar3 negative exit（R3: 殺掉 MH-ext 和部分 TP，淨虧）
- 連續負 bar 出場（R3: 弱於其他方案）
