# V12 策略研究：全新 S 做空進場邏輯

目標：找到一組完全不同於 GK compression breakout 的 S 進場邏輯，取代或改善 V11-E S 策略。

**最終結論：8 輪、15+ 方向、數千組配置 — 無一能匹配 V11-E S。GK 壓縮突破是 ETH 1h 上明確的最優 S 進場信號。V11-E S 策略維持不變。**

---

## 研究背景

### V11-E S 基線

```
數據：ETHUSDT_1h_latest730d.csv（2024-04-14 ~ 2026-04-13）
IS: bar 150 ~ 8766（前半）  |  OOS: bar 8766 ~ 17531（後半）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

V11-E S 規格：
  進場：GK pctile < 30 + Close < 15-bar min（壓縮 + 突破下）
  出場：SafeNet +4% / TP -2.0% / MaxHold 7 bar
  風控：日虧 -$200 / 月虧 -$150 / 連虧 4→24 bar 冷卻 / cd=8 / cap=20

V11-E S 績效：
  IS:  62t $+709  WR 64.5%
  OOS: 76t $+1,328  WR 64.5%  PM 10/13  worst -$113
```

### 研究動機

V11-E L+S 合併 OOS $2,801，但 L 和 S 都使用 GK 壓縮突破信號。
如果能找到一個不同機制的 S 信號：
1. 降低兩策略信號相關性，提升互補性
2. 在 L 弱月（Jun、Sep、Oct、Jan）提供更好的 S 支撐
3. 可能發現新的 edge 來源

### 研究約束

- 相同帳戶設定（$1K / 20x / $4,000 notional / $4 fee）
- 相同風控框架（SafeNet 4% + TP + MaxHold + session filter + circuit breaker）
- Anti-lookahead：所有指標 `.shift(1)`，entry at `O[i+1]`
- IS/OOS 50/50 split

### 排除方向

- 壓縮期結構止損（ETH 突破噪音 > 壓縮區間，<12h 全掃）
- EMA Trail for S（V10/V11 研究已證實 TP+MaxHold 更適合）
- 4h 數據（V10-S v1 的 4h EMA20 前瞻偏差已證實無效）

### 候選方向

```
A：動量衰竭做空（Momentum Exhaustion Short）— 連續上漲 + 買方枯竭
B：成交量異常做空（Volume Spike Short）— 量價背離做空
C：BTC-ETH 背離做空（Cross-asset Divergence）— ETH 跑贏 BTC → 均值回歸
D：過度延伸做空（Overextension Short）— 價格遠離 EMA → 均值回歸
E：RSI 過買做空（RSI Overbought Short）— RSI > 閾值
```

---

## R1：月相統計驗證

### 假設

月相（新月/滿月）前後可能影響市場情緒，導致 ETH 有方向性偏差。

### 方法

- 計算新月/滿月後 7 天的 ETH 累積收益
- 使用 t-test 和 Mann-Whitney U test 檢驗統計顯著性
- 閾值：p < 0.05

### 結果

```
新月 7d return: p = 0.361 (t-test)
滿月 7d return: p = 0.775 (t-test)
Mann-Whitney U: 兩者均不顯著
```

### 結論

**NOT SIGNIFICANT — ABANDONED。** 月相對 ETH 收益無統計顯著影響。

### 腳本

`v12_r1_lunar.py`

---

## R2：方向 A — 動量衰竭做空（Momentum Exhaustion）

### 假設

連續多根上漲 K 線後動量耗盡，做空可以捕捉回調。
使用 streak（連續上漲根數）+ TBR percentile（買方壓力百分位）+ EMA deviation 作為進場條件。

### 方法

Grid search：
- streak_min: [2, 3, 4]
- tbr_min: [0, 60, 70, 80]
- ema_dev_min: [0, 1.0, 2.0, 3.0]
- tp_pct: [1.5, 2.0, 2.5, 3.0]
- mh: [5, 6, 7, 8]

### 結果

```
Total configs with IS>0: 30
ALL OOS negative — 最佳 OOS 也為負值
```

### 結論

**方向 A FAILED。** 動量衰竭信號在 OOS 完全無 edge。IS 正值為過擬合。

### 腳本

`v12_r2_momentum_exhaust.py`

---

## R3：方向 B/D/E — 成交量異常 + 過度延伸 + RSI 過買

### 方法

同一腳本測試三個方向：

**Direction D: Overextension Short（EMA 過度延伸）**
```
Grid: ema_span [20,50] × dev_thresh [2-5%] × tp [1.5-3.0] × mh [5-10]
IS>0 configs: 23
```

**Direction B: Volume Spike Short（成交量異常做空）**
```
Grid: vol_mult [1.5-3.0] × pchg [0-1.5] × tbr [0-0.6] × tp [1.5-3.0] × mh [5-8]
IS>0 configs: 162
```

**Direction E: RSI Overbought Short**
```
Grid: RSI [65-80] × tp [1.5-3.0] × mh [5-10]
IS>0 configs: 24
```

### 結果

```
Direction D (Overextension): 全部 OOS 負值，最佳 -$6 — FAILED
Direction E (RSI Overbought): 全部 OOS 負值，最佳 -$320 — FAILED
Direction B (Volume Spike):   最佳 OOS $+492 (vm3.0 pc1.0 tbr0 tp2.5 mh7)
                              33t WR 60.6% PM 10/13 worst -$274
                              vs V11-E S: -63% PnL, worst month 惡化 2.4x
```

Top 5 Volume Spike：
```
                              Config | IS_n      IS    WR | OOS_n     OOS    WR |    PM     WM
      vm3.0 pc1.0 tbr0 tp2.5 mh7    |   18    +206  55.6 |    33    +492  60.6 | 10/13   -274
      vm3.0 pc1.0 tbr0 tp2.5 mh5    |   19    +230  57.9 |    33    +464  51.5 |  9/13   -210
      vm3.0 pc1.0 tbr0 tp2.0 mh7    |   18     +55  55.6 |    33    +448  60.6 | 10/13   -274
      vm3.0 pc1.0 tbr0 tp2.5 mh8    |   18    +194  50.0 |    33    +447  60.6 | 10/13   -266
      vm3.0 pc1.0 tbr0 tp3.0 mh5    |   19    +319  57.9 |    33    +435  48.5 |  8/13   -210
```

### 結論

- **D (Overextension): FAILED** — ETH 過度延伸後常繼續走強，均值回歸做空無 edge
- **E (RSI Overbought): FAILED** — RSI 過買不是可靠的做空信號
- **B (Volume Spike): 有微弱 edge ($492)** — 但遠不及 V11-E S ($1,328)，且 worst month -$274

### 腳本

`v12_r3_overext_vol.py`

---

## R4：方向 C — BTC-ETH 背離做空

### 假設

ETH 短期大幅跑贏 BTC 後，傾向均值回歸。做空 ETH 可以捕捉這個回調。
V9 研究中 BTC-ETH RelDiv 是 $1K 帳戶最佳信號，但 V9 用的是 SL+TP 出場。
R4 將 RelDiv 信號搭配 V10-style TP+MaxHold 出場（全新組合）。

### 方法

需先下載 BTC 1h 730天數據（17,528 bars）。

**C1: Simple Relative Return Threshold**
```
rel_ret = ETH_pct_change(lb) - BTC_pct_change(lb)  (shift 1)
Grid: lb [3,5,7,10,15] × thresh [0.01-0.05] × tp [1.5-3.0] × mh [5-10]
IS>0 configs: 256
```

**C2: Z-score Adaptive Threshold**
```
rel_ret z-score = (rel_ret - rolling_mean) / rolling_std
Grid: lb [5,10] × z_thresh [1.0-2.5] × tp [1.5-3.0] × mh [5-10]
IS>0 configs: 22
```

**C3: RelRet + Volume Spike Combo**
```
Grid: lb [3,5,7] × thresh [0.01-0.02] × vol_min [1.5-2.0] × tp [1.5-3.0] × mh [5-8]
IS>0 configs: 201
```

### 結果

```
C1 最佳: lb7 th0.01 tp3.0 mh10 → OOS $+337, PM 5/13, worst -$235
         （lb5 th0.05 系列 OOS 1 筆，無統計意義）
C2 全部: OOS 負值 — FAILED
C3 最佳: lb5 th0.01 vm1.5 tp3.0 mh8 → OOS $+255
```

月度比較（C1 最佳 vs V11-E）：
```
     Month  L(V11E)   S(new)   L+Snew |  S(V11E)   L+Sv11 |   Delta
   2025-04     +117     -220     -103 |     +106     +223 |    -326
   2025-05     +329     -207     +122 |     +174     +503 |    -381
   2025-06      +19     -171     -152 |       +0      +19 |    -171
   2025-09      +62     +182     +244 |     +100     +162 |     +82
   2025-11     +139     +768     +907 |      +13     +152 |    +755
   2026-02     +355     +231     +586 |     +274     +629 |     -43
     TOTAL                      +1974 |             +2788 |    -814
```

### 結論

**方向 C FAILED。** 即使 BTC-ETH RelDiv 在 V9 有 edge，搭配 TP+MaxHold 出場後表現遠不及 GK 壓縮突破。月度分析顯示新 S 在多數月份表現不如 V11-E S。

### 腳本

`v12_r4_btc_eth_div.py`

---

## R5：方向 F/G/H/I — Bollinger Band + ATR + N-bar High + Volume 擴展

### 方法

**F: Bollinger Band Upper Touch → Short**
```
Grid: BB_win [20,30] × mult [1.5,2.0,2.5] × bb_thresh [0,0.5,1.0] × tp × mh
IS>0 configs: 101
```

**G: ATR-normalized Large Up Move → Short**
```
1-bar up/ATR: atr_win [14,20] × up_thresh [1.5-3.0] × tp × mh
Multi-bar chg/ATR: lb [3,5] × chg_thresh [2-5] × tp × mh
IS>0 configs: 62
```

**H: N-bar High + Bearish Reversal → Short**
```
N [10,15,20,30] × bearish candle / engulfing / volume
IS>0 configs: 0
```

**I: Volume Spike Expanded + EMA filter**
```
Grid: vol_min [2.0-3.5] × pchg [0-2.0] × ema_dev [0-2.0] × tp × mh
IS>0 configs: 317
```

### 結果

```
F (Bollinger Band): 最佳 $+314 (BB20x2.5>=0% tp1.5 mh10), PM 7/13, worst -$333
G (ATR up move):    最佳 $+275 (1bar/ATR14>=1.5 tp2.5 mh7), PM 7/13, worst -$282
H (N-bar high):     0 IS>0 configs — 完全 FAILED
I (Volume expand):  最佳 $+485 (vm3.0 pc0.5 ed0 tp2.5 mh7), PM 9/13, worst -$274
```

### 結論

- **F (BB): 弱** — $314, -76% vs V11-E S
- **G (ATR): 弱** — $275, -79% vs V11-E S
- **H (N-bar high): FAILED** — 反轉K線型態在 ETH 1h 完全無 edge
- **I (Volume expand)** — $485 確認 Volume Spike 是最好的替代信號，但仍遠不及 V11-E S

### 腳本

`v12_r5_bb_atr_high.py`

---

## R6：方向 J/K/L/M/N — 失敗突破 + 量價背離 + MACD + Donchian + SMA

### 方法

**J: Failed Long Breakout（GK 壓縮 + 突破上 N bar 後跌回）**
```
Grid: gk_thresh [25-40] × brk_n [10,15,20] × lookback [2-5] × tp × mh
IS>0 configs: 211
```

**K: Price/Volume Divergence（價格上漲 + 成交量下降 = 派發）**
```
Grid: lb [5,10] × sma50_dev [0-2%] × tp × mh
IS>0 configs: 26
```

**L: MACD Positive but Declining（MACD 柱狀圖減弱）**
```
Grid: sma50_dev [0-3%] × tp × mh
IS>0 configs: 52
```

**M: Donchian Upper Channel Touch → Short**
```
Grid: n [10,15,20,30] × tp × mh (+bearish combo)
IS>0 configs: 0
```

**N: Slow MA Overextension (SMA50/100) → Short**
```
Grid: span [50,100] × dev [2-10%] × tp × mh
IS>0 configs: 30
```

### 結果

```
J (Failed breakout): 最佳 $+462 (GK<40 brk20 fb4 tp1.5 mh7), PM 7/13
   注意：仍使用 GK 作為條件之一，非「完全不同」信號
K (PV Divergence):   最佳 $+825 (PV10 sd50>2.0 tp1.5 mh10), PM 9/13, WR 66.2%
   ★ 全研究最高 OOS PnL！但 IS 僅 $+2（極度不可靠）
L (MACD):            最佳 -$161 — FAILED
M (Donchian):        0 configs — FAILED
N (SMA overext):     全部 OOS 負 — FAILED
```

### K (PV Divergence) 分析

```
PV10 sd50>2.0 tp1.5 mh10:
  IS:  45t $+2 WR 62.2%  ← IS 幾乎為零，不可靠
  OOS: 65t $+825 WR 66.2% PM 9/13 worst -$239

PV10 sd50>0 tp2.0 mh5:（IS 更穩健的替代）
  IS:  175t $+454 WR 49.7%  ← IS 正值但 WR 僅 50%
  OOS: 179t $+807 WR 50.3% PM 8/13 worst -$193
  179 筆交易 50% WR = 本質上是隨機硬幣
```

### 結論

- **K (PV Divergence) 是數據上的最佳替代**，但 IS 可靠性極差
- J 仍依賴 GK，不算「完全不同」
- L/M/N 全部失敗

### 腳本

`v12_r6_novel.py`

---

## R7：複合信號做空（Composite Signal Short）

### 假設

單一信號太弱，但組合多個弱信號可能產生更強的進場條件。

### 方法

**Approach 1: AND 組合（兩信號同時滿足）**
- VM + EMA、VM + PV、EMA + RSI、PV + SMA50、BB + VM
- 每組合配 tp × mh 掃描

**Approach 2: Score-based（加法評分系統）**
- 8 項指標各貢獻 0-1 分，entry when score >= threshold
- 指標：VM≥2、VM≥3、EMA≥2、EMA≥4、RSI≥70、PV div、BB>upper、pchg≥2%
- Grid: score_thresh [2,3,4] × tp × mh

**Approach 3: Triple AND（三信號同時）**
- VM + EMA + PV / VM + EMA + RSI

### 結果

```
Approach 1 最佳: PV10+SMA50>=2.0 tp1.5 mh10 → $+825 (= R6 K 相同信號)
                 VM>=3.0+EMA>=1.0 tp2.5 mh5  → $+359
Approach 2 最佳: score>=2 tp3.0 mh7 → $+665, PM 5/13
                 score>=2 tp2.0 mh5 → $+654, PM 6/13
Approach 3 最佳: VM1.5+EMA1.0+PV tp3.0 mh8 → $+64 (過度篩選 = 太少交易)
```

### 結論

**複合信號無法超越單一最佳信號。** 組合弱信號只是創造了更稀少的觸發（更少交易），並未產生更強的 edge。Score-based 做法在 $665 但 PM 僅 5/13，不穩定。

### 腳本

`v12_r7_composite.py`

---

## R8：GK Expansion Reversal（GK 擴張反轉做空）

### 假設

與 V11-E S（低 GK = 壓縮突破）相反 — 高 GK（波動擴張）時做空，期望均值回歸。

### 方法

**GK Expansion Simple: GK pctile > threshold → Short**
```
Grid: gk_min [50-90] × tp × mh
IS>0 configs: 31
```

**GK Expansion + Price Above EMA20**
```
Grid: gk_min [50-80] × ema_dev [0-3%] × tp × mh
IS>0 configs: 32
```

**GK Expansion + Bearish Candle / Close in lower 30%**
```
Grid: gk_min [50-80] × bear_type × tp × mh
IS>0 configs: 50
```

**GK Expansion + Volume Spike**
```
Grid: gk_min [50-80] × vol_min [1.5-2.5] × tp × mh
IS>0 configs: 133
```

### 結果

```
GK Expansion Simple:   全部 OOS 負值 — FAILED
GK Expansion + EMA:    最佳 $+251 — 弱
GK Expansion + Bearish: 全部 OOS 負值 — FAILED
GK Expansion + Volume:  最佳 $+517 (GK>=70+VM>=2.5 tp2.5 mh5)
                         83t WR 51.8% PM 7/13 worst -$194
```

GK Expansion + Volume 月度比較：
```
     Month  L(V11E)   S(new)   L+Snew |  S(V11E)   L+Sv11 |   Delta
   2025-06      +19     +411     +430 |       +0      +19 |    +411 ★ 互補
   2025-10     -111     +269     +158 |     +296     +185 |     -27
   2026-01      -24     +163     +139 |     +108      +84 |     +55 ★ 互補
   2025-08     +115     -194      -79 |       +4     +119 |    -198 ✗ 惡化
   2025-12     +105     -152      -47 |      +94     +199 |    -246 ✗ 惡化
   2026-02     +355     -192     +163 |     +274     +629 |    -466 ✗ 惡化
     TOTAL                      +2154 |             +2788 |    -634
```

### 結論

GK Expansion + Volume 在少數月份有互補效果（Jun、Jan），但在更多月份造成虧損。
**總體 L+S 從 $2,788 惡化到 $2,154（-$634），不可取。**

### 腳本

`v12_r8_gk_expansion.py`

---

## 全研究匯總

### 所有方向結果一覽

| Round | 方向 | 信號類型 | Best OOS | vs V11-E S ($1,328) | 狀態 |
|-------|------|---------|----------|---------------------|------|
| R1 | 月相 | 天文 | N/A | — | NOT SIGNIFICANT |
| R2 | A: 動量衰竭 | Streak+TBR+EMA | ALL 負 | — | FAILED |
| R3 | D: EMA 過度延伸 | 均值回歸 | -$6 | — | FAILED |
| R3 | E: RSI 過買 | 動量指標 | -$320 | — | FAILED |
| R3 | B: 成交量異常 | 量價 | $+492 | -63% | 微弱 edge |
| R4 | C1: BTC-ETH RelRet | 跨資產 | $+337 | -75% | 弱 |
| R4 | C2: BTC-ETH Z-score | 跨資產 | ALL 負 | — | FAILED |
| R4 | C3: RelRet+Volume | 跨資產+量 | $+255 | -81% | FAILED |
| R5 | F: Bollinger Band | 通道指標 | $+314 | -76% | 弱 |
| R5 | G: ATR 大K線 | 波動率 | $+275 | -79% | 弱 |
| R5 | H: N-bar high+反轉 | 型態 | 0 configs | — | FAILED |
| R5 | I: Volume+EMA 擴展 | 量+趨勢 | $+485 | -63% | 微弱 edge |
| R6 | J: Failed breakout | 結構 (含GK) | $+462 | -65% | 弱 |
| R6 | K: 量價背離 (PV) | 量價 | $+825 | -38% | IS 不可靠 |
| R6 | L: MACD 背離 | 動量 | -$161 | — | FAILED |
| R6 | M: Donchian upper | 通道 | 0 configs | — | FAILED |
| R6 | N: SMA 過延伸 | 均值回歸 | ALL 負 | — | FAILED |
| R7 | Score 複合 | 多指標 | $+665 | -50% | PM 5/13 |
| R7 | AND 組合 | 雙信號 | $+825 (PV) | -38% | IS 不可靠 |
| R7 | Triple AND | 三信號 | $+64 | — | FAILED |
| R8 | GK Expansion | 波動率反向 | ALL 負 | — | FAILED |
| R8 | GK Expansion+EMA | 波動率+趨勢 | $+251 | -81% | 弱 |
| R8 | GK Expansion+Volume | 波動率+量 | $+517 | -61% | 互補不足 |

### 排名（OOS PnL 前 5）

```
Rank | 方向                    | OOS PnL | WR    | PM    | worst | IS PnL | 可靠性
  1  | K: PV Divergence        |   $+825 | 66.2% |  9/13 |  -239 |    $+2 | ✗ IS≈0
  2  | Score>=2 composite      |   $+665 | 52.8% |  5/13 |  -298 |  $+356 | △ PM 低
  3  | GK>=70+VM>=2.5          |   $+517 | 51.8% |  7/13 |  -194 |  $+482 | △ WR≈50%
  4  | B: VM3.0+pc1.0 tp2.5    |   $+492 | 60.6% | 10/13 |  -274 |  $+206 | △ worst 差
  5  | I: VM3.0+pc0.5 tp2.5    |   $+485 | 62.5% |  9/13 |  -274 |   $+68 | △ IS 低
  —  | V11-E S (baseline)      | $+1,328 | 64.5% | 10/13 |  -113 |  $+709 | ★ 穩定
```

### 核心結論

1. **GK 壓縮突破是 ETH 1h 上結構性最強的 S 進場信號。** 它捕捉的是波動率 regime 變化（壓縮→突破→趨勢延續），這個 edge 來源是其他技術指標（RSI、BB、MACD、MA deviation、Volume）無法複製的。

2. **均值回歸做空在 ETH 1h 上不可行。** Direction D（EMA overextension）、E（RSI overbought）、N（SMA overextension）、GK Expansion 全部失敗。ETH 的趨勢性太強，過度延伸後常繼續走強而非回調。

3. **成交量異常是唯一有微弱 edge 的替代信號**（$492-517），但只有 V11-E S 的 37-39%，且 worst month 惡化到 -$274（V11-E S: -$113）。

4. **複合信號未能超越單一信號。** 組合多個弱信號只減少了觸發次數，沒有增加 edge 強度。Score-based 和 AND combo 的表現都不如最佳單一信號。

5. **跨資產信號（BTC-ETH divergence）搭配 TP+MaxHold 出場效果不佳。** V9 的 RelDiv 信號在 SL+TP 框架下有效（$1,170/yr），但在 TP+MaxHold 框架下只有 $337。這暗示 edge 可能來自出場機制而非進場信號。

### 建議

**保持 V11-E S 策略不變。** 8 輪研究、15+ 方向、數千組配置的系統性搜索，確認了 GK 壓縮突破的優越性。未來若要改善 S 策略，應聚焦於：
- 出場機制調整（TP/MH 微調）
- GK 閾值/lookback 參數優化
- 風控參數調整

而非嘗試替換進場信號。

---

## 研究腳本索引

| 腳本 | 說明 |
|------|------|
| `v12_r1_lunar.py` | R1: 月相統計驗證 — NOT SIGNIFICANT |
| `v12_r2_momentum_exhaust.py` | R2: 動量衰竭做空 (Direction A) — FAILED |
| `v12_r3_overext_vol.py` | R3: 過度延伸 + 成交量異常 + RSI (D/B/E) |
| `v12_r4_btc_eth_div.py` | R4: BTC-ETH 背離做空 (Direction C) |
| `v12_r5_bb_atr_high.py` | R5: BB + ATR + N-bar high + Volume 擴展 (F/G/H/I) |
| `v12_r6_novel.py` | R6: 失敗突破 + 量價背離 + MACD + Donchian + SMA (J/K/L/M/N) |
| `v12_r7_composite.py` | R7: 複合信號做空 (AND/Score/Triple) |
| `v12_r8_gk_expansion.py` | R8: GK 擴張反轉做空 |
