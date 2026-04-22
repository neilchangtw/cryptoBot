# V21 研究：獨立 edge 與 V14 改良（2026/04/21）

> V21 目標：在 V19-V20 強結論「ETH alpha = breakout only, 非 breakout = random walk」之下，
> 嘗試找到**超越 V14 OOS $4,549** 的新策略，或至少找到獨立 edge 作為備案。
>
> 方法分兩條路徑：
> - **Path A**：尋找獨立於 V14 breakout 的新 edge（與 V14 交易重疊率 < 70%）
> - **Path B**：放棄獨立性要求，改做 V14.1（允許高重疊率，目標是改進 V14 本身的 OOS/MDD）

---

## 背景

V6-V20 共 27,500+ 配置研究的終極結論（V19-V20 強化）：
1. ETH 1h 上唯一的 alpha = 15-bar close breakout（V14 GK 壓縮突破鎖定此 alpha）
2. 非 breakout bars 是 random walk（MFE 對稱性，HMM 3-state 證實）
3. 所有免費技術指標/宏觀/情緒/HMM 無預測力（V17/V18/V19 近 600+ configs）
4. V14 是 ETH-specific（V20 9 幣種 locked-param 9/9 FAIL）
5. 任何依賴 cascade 或 data-mined filter 的策略都會被 10-Gate 稽核打回（V15/V16）

V21 的先驗機率：**低**。但使用者希望窮盡探索，故展開。

---

## R0: CoinGlass 付費數據評估

曾考慮以 CoinGlass 市場微結構數據（OI / LSR / Funding / Liquidation / CVD）突破 V19-V20 的結論。

| 方案 | 月費 | 1h 歷史 | 對等 V14 (730 天) |
|------|------|---------|-------------------|
| Hobbyist | $29 | 180 天 | ❌ |
| Startup | $79 | 360 天 | ❌ |
| **Standard** | **$299** | **720 天** | ✅ |
| Professional | $699 | 720 天 | ✅ |

**使用者決定不訂閱**。原因：
- 免費方案不存在，最低 $29
- V19-V20 強結論暗示 CoinGlass 數據也不太可能找到新 alpha
- 期望值不足以 cover 付費成本

V21 改以現有免費數據（OHLCV）繼續探索，接受低先驗。

---

## V21 硬性約束

```
帳戶：$1,000 / $200 margin / 20x / $4,000 notional
手續費：$4/trade（含滑價）
進場價：O[i+1]
指標：.shift(1) 強制
時框：1h
maxTotal：L=1, S=1, L+S ≤ 2
IS/OOS：前 50% / 後 50% 固定（IS = 2024-04-18 ~ 2025-04-19, OOS = 2025-04-19 ~ 2026-04-20）
```

**10-Gate 審查**（任一 FAIL = REJECTED）：
- G1 IS 先行 / G2 自然步長 / G3 IS/OOS 同向 / G4 鄰域穩定 / G5 Cascade 驗證
- G6 Swap test < 50% / G7 WF ≥ 4/6 / G8 時序翻轉仍有 edge / G9 Remove-best-month
- G10 新增參數 ≤ 2/輪
- V14 重疊率 < 70%（Path A 必過，Path B 不適用）

---

## Path A: 獨立 edge 探索（7 輪全 REJECTED）

### R1: Weekly Range Escape

假說：突破上週 H/L 後延續 N bar 的趨勢動能。事件錨定（非 rolling），時間尺度 168h。

**Spec**: Entry = 每週第一次 close 突破上週 H/L / Hold 24 bar / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 46 | 47 |
| WR | 37.0% | 46.8% |
| PnL | $1010 | $440 |
| MDD | $649 | $822 |
| 月正 | 7/13 | 9/13 |
| L PnL | $190 (32% WR) | $1050 (52% WR) |
| S PnL | $820 (42% WR) | -$611 (39% WR) |

**結論：REJECTED**
- **G3 FAIL**：OOS 衰退 56%，L/S 角色完全翻轉（IS 靠 S 賺，OOS 靠 L 賺）
- **G4 FAIL**：hold 鄰域 cliff（hold=12 $2225 / hold=24 $1010 / hold=48 $1151）
- 無穩定方向 edge

---

### R2: Daily Opening Range Breakout

假說：每 UTC 日前 4 小時的 H/L 形成事件錨定支撐阻力。

**Spec**: OR window 00:00-04:00 UTC / Entry = 04:00 後首次突破 OR H/L / Hold 12 / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 361 | 350 |
| WR | 42.1% | 45.7% |
| PnL | **-$2825** | $127 |
| MDD | $3084 | $2622 |

**結論：REJECTED at Stage 2** — IS 負 $2825
Grid 16 組配置（hold 6/12/24/36 × SL 2.5/3/3.5/4%）全部負值，最接近零 -$333。

---

### R3: Multi-bar Staircase (N=4)

假說：N 連續 higher-low 階梯 = 買方吸籌，突破頂部觸發動能。跟 Three Soldiers 不同（看 low 序列不看 body）。

**Spec**: 4 bar monotone rising lows + close > 4-bar high / Hold 12 / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 277 | 316 |
| WR | 45.5% | 44.6% |
| PnL | -$407 | -$898 |
| MDD | $1875 | $2560 |

**結論：REJECTED at Stage 2** — IS 負 $407

**N-sweep 發現**：N=3 -$1881 / N=4 -$407 / N=5 -$43 / **N=6 +$894** — 單調遞增，非隨機。觸發 R4。

---

### R4: Staircase N=6 LOCKED（R3 N-sweep 發現後）

Lock N=6，用 IS-only evidence 鎖定參數（合法 G1）。

**Spec**: N=6 / Hold 12 / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 93 | **117** |
| WR | 46.2% | 48.7% |
| PnL | $894 | **$2148** |
| MDD | $776 | $497 |
| 月正 | 8/13 | 7/13 |

**OOS 遠強於 IS**（$2148 vs $894）— 紅旗。

**10-Gate 結果**：
| Gate | 結果 | 備註 |
|------|------|------|
| G1-G3 | PASS | IS/OOS 雙正 |
| G4 | PARTIAL | N 鄰域 cliff（N=5 -$43, N=7 -$197）|
| G5 | N/A | 單層 |
| **G6** | **FAIL** | Swap backward 衰退 **58%** |
| G7 | PASS | WF 5/6 正 |
| **G8** | **FAIL** | 時序翻轉只剩 2 筆 -$51 |
| G9 | PASS | 去最佳月 $2126 |
| G10 | PASS | 未加新 knob |
| V14 重疊 | PASS | 26%（真獨立 edge）|

**結論：REJECTED** — G6 + G8 硬 FAIL。edge 時期相依於 OOS 期間特定市場結構。

---

### R5: Prior Day HL Breakout

假說：昨日完整 H/L 作為事件錨定參考（日尺度，介於 R1 週與 R2 當日 OR 之間）。

**Spec**: 昨日 H/L / Hold 12 / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 241 | 250 |
| WR | 44.0% | 44.0% |
| PnL | $272 | $1021 |

**結論：REJECTED at Stage 2** — IS $272 < $500 薄門檻
L/S 角色再次翻轉：IS L -$74/S +$346 → OOS L +$2100/S -$1079

---

### R6: Monthly Range Breakout

假說：上月 H/L 作為更大尺度事件錨定。

**Spec**: 上月 H/L / Hold 48 bar（2 天）/ SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 11 | 8 |
| WR | 54.5% | 50.0% |
| PF | 2.57 | 3.25 |
| PnL | **$1129** | **$1088** |
| MDD | $288 | $195 |
| 月正 | 6/10 | 4/8 |

**IS/OOS 對稱性罕見**（$1129 vs $1088，差距僅 3.6%）。

**10-Gate 結果**：
| Gate | 結果 |
|------|------|
| G1-G4 | PASS（grid 全正 $579-$1632）|
| G5 | N/A |
| **G6** | **PASS**（fwd 3.7%, bwd -3.8%，幾乎零衰退）|
| G7 | PASS（4/6 正）|
| **G8** | **FAIL**（反轉 -$1619）|
| G9 | PASS（去最佳月 $1357）|
| G10 | PASS |
| V14 重疊 | PASS（33%）|

**結論：REJECTED (9/10 Gate pass, only G8 FAIL)** — 最接近成功的候選

**解讀**：Monthly breakout edge 綁定 ETH 2024-2026 多頭方向。時序翻轉 = 模擬空頭，edge 消失。另外 **19 筆樣本極薄**，即使 G8 過也統計薄弱。

---

### R7: Swing Pivot Break (5-bar fractal)

假說：5-bar fractal swing high/low = 結構確認關鍵價位，突破觸發。

**Spec**: 5-bar fractal pivot / close > last pivot H / Hold 12 / SL 3.5%

| 指標 | IS | OOS |
|------|-----|------|
| 交易數 | 552 | 554 |
| PnL | $1465 | $1119 |
| MDD | $1092 | **$2368** |
| L PnL | **-$811** | **+$1105** |
| S PnL | **+$2277** | **+$13** |

**結論：REJECTED** — L/S 組成完全翻轉（經典 regime-dependent signature），MDD 翻倍，G4 hold 鄰域 cliff（hold=6 -$3847 / hold=12 +$1465 / hold=24 -$3694）。

---

## Path A 總結

| Round | 方向 | IS | OOS | FAIL Gates |
|-------|------|-----|-----|-----------|
| R1 | Weekly Range | +$1010 | +$440 | G3, G4 |
| R2 | Daily 4h OR | -$2825 | +$127 | Stage 2 |
| R3 | Staircase N=4 | -$407 | -$898 | Stage 2 |
| R4 | Staircase N=6 | +$894 | +$2148 | G6, G8 |
| R5 | Prior Day HL | +$272 | +$1021 | Stage 2 |
| R6 | Monthly Range | +$1129 | +$1088 | G8 (9/10 pass) |
| R7 | Swing Pivot | +$1465 | +$1119 | G3, G4 |

### 共通失敗模式
1. **L/S 角色在 IS/OOS 翻轉**（R1/R5/R7）— 無穩定方向 edge
2. **時序翻轉失效**（R4/R6）— edge 依賴 ETH 多頭方向
3. **參數鄰域 cliff**（R4 N, R7 hold）— 過擬合地貌
4. **OOS 遠強於 IS**（R4）— swap backward > 50%

### 核心發現

**Path A 事件錨定 breakout 策略在 ETH 1h 存在邊緣訊號，但 edge 高度 regime-dependent**，無法通過完整 10-Gate 審查。這呼應並強化 V19-V20 結論：「ETH alpha 存在但綁定特定市場方向，難以穩健分離」。

V21 排除清單新增（寫入 CLAUDE.md 禁區）：
- Weekly Range Breakout（hold=24）
- Daily Opening Range Breakout（UTC 00-04 window）
- Multi-bar Staircase（N=4 to N=8）+ breakout trigger
- Prior Day HL Breakout
- Monthly Range Breakout（樣本極薄 + 時序依賴）
- 5-bar Fractal Swing Pivot Break

---

## Path B: V14.1 改良（3 輪 全 REJECTED）

Path A 7/7 REJECT → 正式進 Path B（V14.1 改良，接受高重疊率）。

G1 改審：相對 V14 L+S IS $2,435 / OOS $4,542 改善（非獨立 edge）。
維持 S 完全不動（V14 研究已證 S 是 globally optimal），B1/B2/B3 皆只動 L 或全域規則。

### V14 Baseline（本輪重測，production slippage model）

| 指標 | L IS | L OOS | S IS | S OOS | L+S IS | L+S OOS |
|------|------|-------|------|-------|--------|---------|
| 交易數 | 72 | 85 | 71 | 94 | 143 | 179 |
| PnL | $947 | $2,064 | $1,488 | $2,478 | **$2,435** | **$4,542** |
| WR | 54% | 61% | 61% | 62% | — | — |
| MDD | $232 | $227 | $276 | $275 | — | — |

與 CLAUDE.md 公布值 $4,549 偏差 $7（< 0.2%），來自 rank percentile 實作差異，可忽略。

### B1: Signal Clustering Filter — 負 trade 後擴展 cooldown

**假說**：V14 的 echo 突破（第一筆虧損 → 短期內 re-entry）是可過濾的低品質交易。

**Spec**：`cd_after_loss` ∈ {8/12/15/20/24} × `cd_s_after_loss` ∈ {10/15/20/24}（V14 原 CD=6/8）

20 組結果中：
- **最佳 IS 保留** = cd_l=8,cd_s=10：與 V14 完全相同（V14 原 CD 已過濾所有 < 8 bar 的 L echo）
- **OOS 最佳** = cd_s=24：+$32~+$36，但伴隨 IS -$152~-$224（G1 FAIL）
- 增加 cd_l ≥ 20：IS 最多降 -$468

**結論：REJECTED** — V14 cooldown 已接近最佳，B1 要嘛重複 V14 要嘛犧牲 IS 換 OOS（data-mining）。

### B2: Path-dependent Exit — L bar 2/3 深 drawdown 早切

**假說**：V14 的 Conditional MH（bar 2 ≤ -1% → MH=5）不夠積極。深 drawdown 應直接早切。

**Spec**（10 組）：
- B2a-d: bar 2 ≤ -1.5% ~ -2.0% → MH=2 or 3
- B2e-g: bar 3 ≤ -1.5% ~ -2.5% → 立即出場
- B2h-i: b2+b3 組合

| Config | L IS | L OOS | vs V14 L OOS |
|--------|------|-------|--------------|
| V14 | $947 | $2,064 | 0 |
| B2a-d (bar2 stricter) | $947 | $2,064 | **+0**（never fired） |
| B2e bar3≤-2.0% cut | $943 | $1,878 | -$186 |
| B2f bar3≤-1.5% cut | $902 | $1,878 | -$186 |
| B2g bar3≤-2.5% cut | $943 | $1,990 | -$74 |

**結論：REJECTED** — B2a-d 在 V14 資料中從未觸發（bar 2 ≤ -1.5% 的 L entry 不存在於 non-SafeNet path）。B2e-i bar 3 cut 全部犧牲翻身機會。V14 SafeNet 3.5% 已是路徑相依出場的 globally optimal 位置。

### B3: Conditional Non-trading — L 連敗 → 跳過下 N 筆 L signal

**假說**：V14 全域連 4 虧 → 24 bar 暫停，但 L-specific 的連敗模式可以更早捕捉。

**Spec**（8 組）：consec_thresh ∈ {2,3,4} × skip_next ∈ {1,2,3}

| Config | L IS | L OOS | vs V14 L+S OOS |
|--------|------|-------|----------------|
| V14 | $947 | $2,064 | $4,542 |
| B3a 2-loss skip 1 | $848 | $2,042 | $4,520 |
| B3b 2-loss skip 2 | $636 | $1,834 | $4,312 |
| B3d 3-loss skip 1 | $394 | $2,100 | $4,579 |
| **B3e 3-loss skip 2** | $760 | $2,153 | **$4,631** |
| B3f 3-loss skip 3 | $322 | $1,988 | $4,466 |
| B3g 4-loss skip 1 | $932 | $2,010 | $4,488 |

**結論：REJECTED** —
- B3e 是唯一 OOS 改善 ($4,631 vs $4,542, +$89, +2.0%) 的配置
- 但 IS 顯著退化 $947 → $760（**-20%，G1 FAIL**）
- 參數鄰域 cliff（B3d/f/g 全降）
- 經典 data-mining signature：僅一個配置碰巧在 OOS 跳過剛好是幾筆輸單

---

## Path B 總結

| Candidate | IS | OOS | Fail Mode |
|-----------|-----|-----|-----------|
| B1 cooldown extend | -$0 ~ -$468 | -$240 ~ +$36 | G1 IS 退化 or 無效 |
| B2 path-dep exit | -$45 ~ +$0 | -$186 ~ +$0 | 從未觸發 or 殺翻身 |
| B3 L-only non-trading | -$625 ~ -$15 | -$230 ~ +$89 | G1 IS 退化 + 鄰域 cliff |

### 核心發現

**V14 在 $1K/20x/fee=$4 account 條件下已是 tightly-optimized local maximum**：
1. V14 cooldown（L=6, S=8）已過濾所有短期 echo → B1 無空間
2. V14 Conditional MH（bar 2 ≤ -1%）+ SafeNet 3.5% 已捕捉所有可早切路徑 → B2 要嘛無效要嘛殺翻身
3. 全域 4-consec-loss 熔斷已足，L-only 變體只會過擬合 IS → B3 G1 FAIL

### 排除清單新增

- Signal Clustering Filter（B1：extended cooldown after loss）
- Path-dependent Early Exit（B2：bar 2/3 drawdown cut）
- L-only Consec-loss Non-trading（B3：會過擬合 IS）

---

## V21 最終結論

**2026-04-21 結案**：V21 Path A 7 輪 + Path B 3 輪 = **10/10 REJECTED**。

V14 作為 ETH 1h 最佳生產策略持續鎖定。V21 的價值在於：
1. 正式證實 Path A 獨立 event-anchored breakout edge 全部 regime-dependent
2. 正式證實 V14 參數/邏輯已是局部最佳（改動多數降 IS）
3. 節省付費數據費用（CoinGlass $299 的期望值為負）

**超越 V14 的下一步**（僅供未來參考）：
- 需要**新的數據模態**（order book micro-structure / on-chain flow），而非 OHLCV 組合
- 需要**不同的獎勵結構**（跨月複利 / 風險平價），而非 per-trade max
- 目前 $1K account 規模下，V14 OOS $4,549（+27.5%）已是 realistic ceiling

---

## 相關檔案

- `backtest/research/v21_r1_weekly_range.py` — R1
- `backtest/research/v21_r2r3_candidates.py` — R2/R3
- `backtest/research/v21_r4r5_candidates.py` — R4/R5
- `backtest/research/v21_r4_full_audit.py` — R4 10-Gate
- `backtest/research/v21_r6r7_candidates.py` — R6/R7
- `backtest/research/v21_r6_full_audit.py` — R6 10-Gate
- `backtest/research/v21_b1b2b3_candidates.py` — B1/B2/B3 篩選
- `backtest/research/v21_r[1-7]_(is|oos|grid).csv` — 原始結果
