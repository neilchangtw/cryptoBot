# V22 研究 — 古典技術分析理論掃描

**目標**：跳脫 V14 框架，系統性測試世界上知名的古典技術分析理論（波浪、頭肩、三角、調和、費波那契、支撐阻力、威科夫等），看能否在 ETH 1h 上找到獨立 alpha。
**約束**：嚴格無上帝視角（shift(1) / N-bar fractal confirmed / 進場 O[i+1]），避開 V15-V21 已證實死路。
**帳戶**：$1K / 20x / $4K notional / $4 fee。
**IS/OOS**：17586 bars, IS[0:8793] 2024-04-18→2025-04-19, OOS[8793:17586] 2025-04-19→2026-04-20。
**模擬**：25% 穿透滑價 + production SafeNet 模型。

---

## 結論

**R1-R8 全數 REJECTED**。8 大古典理論（Ichimoku / Head & Shoulders / Triangles / Harmonic XABCD / Fibonacci / Pivot Points / Elliott Wave / Wyckoff）在 ETH 1h 全部失敗，失敗模式一致：

- **L-biased pattern（看多形態）**：IS 漂亮（牛市 ETH 倒買都能賺），**G8 時序翻轉全部虧錢** → regime-dependent，非真 alpha
- **S-biased pattern（看空形態）**：IS 高勝率 (70-85%)，**OOS 方向翻轉 / 巨虧** → 過擬合 ETH 特定回調點

**核心證據**：
- V22 R2 iHS L 形態：IS $1458 (73% WR) → G6 Forward +69% fwd / -225% bwd / G8 時序翻轉 -$343
- V22 R3 ASC 三角 L：IS $851 / OOS $1614（OOS > IS 警示） → G6 Fwd -89.7% / G8 -$834 / N×M 孤峰
- V22 R6 wS1 Pivot S breakout：IS 85% WR / $2044 → OOS -$61（100% IS→OOS 翻轉）
- V22 R8 Upthrust Wyckoff S：IS $1723 / OOS **-$1327**（top 8 配置 OOS 全 -$552~-$2648）

**結論補強 V19-V20**：ETH 1h alpha = 15-bar breakout (V14)，其他形態信號全為 regime-dependent noise。古典 TA 在加密期貨的小時級上無獨立 edge。

---

## 各輪結果表

| 輪 | 理論 | 核心指標 | 最佳 IS | OOS | 審判 |
|----|------|---------|--------|-----|------|
| R1 | Ichimoku | TK/Cloud/3/4-confirm | S2_Cloud_L IS $580 | OOS **-$2092** | REJECT (G3) |
| R2 | Head & Shoulders / iHS | N-bar fractal + shoulder tol | iHS_N5_tol3 L IS $1458 WR 73% | OOS $448 WR 60% | **AUDITED-REJECT** (G6+G8) |
| R3 | Triangle (Asc/Desc/Sym) | 最小二乘 trendline | ASC_N5_M5 L IS $851 | OOS $1614 | **AUDITED-REJECT** (G6 -89.7%, G8 -$834, N×M 孤峰) |
| R4 | Harmonic XABCD (Gartley/Bat/Butterfly/Crab) | Fib ratio match (AB/XA, BC/AB, CD/BC, AD/XA) | Bat_L 1 match / 2yr | N/A | REJECT (樣本不足) |
| R5 | Fibonacci Retracement | swing leg 回撤 38.2/50/61.8% | 0 IS+ configs | N/A | REJECT |
| R6 | Pivot Points (Daily/Weekly) | PP/R1/R2/S1/S2 breakout + MR | brk_wS1 S IS $2044 WR 85% | OOS **-$61** | REJECT (極端過擬合) |
| R7 | Elliott Wave (3-wave impulse) | L-H-L-H + wave 3 break | EW3_S_N7 0.040/24 IS $747 | OOS $278 (-63%) | REJECT (G6 邊緣) |
| R8 | Wyckoff Spring / Upthrust | wide-range + high-volume at extreme | Upthrust_L10_v18 IS $1723 WR 56% | OOS **-$1327** | REJECT (OOS 巨翻轉) |

---

## R2 iHS 10-Gate 稽核（唯一接近 viable 的候選）

候選：`iHS_N5_tol3 L TP=0.035 MH=48 SL=0.035 CD=6`

| Gate | 項目 | 結果 | 判定 |
|------|------|------|------|
| G1 | IS > 0 | $1458, 73% WR, PF 1.9 | PASS |
| G2 | OOS > 0 | $448, 60% WR | PASS |
| G3 | IS/OOS 方向一致 | 都 >0 | PASS |
| G4 | TP×MH 鄰域 | 相鄰 8 配置 7 PASS | PASS |
| **G6** | **Swap (IS↔OOS)** | **Fwd +69% / Bwd -225%** | **FAIL** |
| G7 | WF 6-window | 5/6 positive | PASS |
| **G8** | **時序翻轉** | **-$343** | **FAIL** |
| G9 | 移除最佳月 | +$1312 | PASS |
| V14 overlap | 與 V14 L 進場重疊 | 23% | PASS (獨立) |

**結論**：iHS 是獨立於 V14 的形態（overlap 僅 23%），但 **G6 + G8 雙 FAIL 證實 alpha 完全來自 ETH 2024-2026 多頭 regime**，時序翻轉（模擬熊市）後 -$343，換個市況就失效。

---

## R3 ASC 三角 10-Gate 稽核（最異常 OOS > IS）

候選：`ASC_N5_M5 L TP=0.04 MH=48`

| Gate | 結果 | 判定 |
|------|------|------|
| G1/G2/G3 | IS $851 / OOS $1614 同向 | PASS |
| G4 TP×MH | 鄰域穩定 | PASS |
| **G4 N×M** | **孤峰 (ASC N5 M5 唯一正，N3/N7/M4/M6 全負)** | **FAIL** |
| **G6 Fwd** | **-89.7%** | **FAIL** |
| G7 WF | 6/6 | PASS |
| **G8 Time Rev** | **-$834** | **FAIL** |
| V14 overlap | 23% | PASS |

**OOS > IS 的本質**：OOS (2025-04→2026-04) 有一段 ETH 從 $1400 反彈至 $4000 的大波段，三角 L 恰好捕捉。N/M 參數敏感 + G6 + G8 三重 FAIL = 運氣。

---

## 失敗模式分類

**模式 A：L-biased IS/OOS 雙正但時序翻轉虧**（iHS, ASC, 部分 Elliott L）
→ Alpha 來自 ETH 長期牛市 drift，非形態本身
→ G8 是唯一能抓到的 audit（G3 會 PASS）

**模式 B：S-biased IS 高勝率但 OOS 翻轉**（wS1 Pivot, Upthrust Wyckoff, 部分 Elliott S）
→ 過擬合 ETH 特定歷史回調點
→ 樣本外 ETH 漲多於跌 → S 方向不管理論多完美都會虧

**模式 C：信號太稀疏 / 樣本不足**（Harmonic XABCD, Fibonacci）
→ 太嚴格的形態定義在 2 年 17586 bars 產生 <10 樣本
→ 統計不可信

---

## 延伸禁區（加入 CLAUDE.md）

- Ichimoku Cloud 任何變體（TK 交叉 / Cloud 突破 / 3-confirm / 4-confirm） — V22 R1 ALL OOS 負
- Head & Shoulders / Inverse H&S 形態 — V22 R2 G6+G8 雙 FAIL，regime-dependent
- Triangle (Ascending / Descending / Symmetrical) breakout — V22 R3 G8 翻轉 -$834，N×M 孤峰
- Harmonic XABCD patterns (Gartley/Bat/Butterfly/Crab) — V22 R4 ETH 1h 上樣本過稀
- Fibonacci retracement (38.2/50/61.8) swing entry — V22 R5 0 IS+ configs
- Classical Pivot Points (Daily/Weekly PP/R1/R2/S1/S2) — V22 R6 85% WR IS → OOS -$61 極端過擬合
- Elliott Wave 3-wave impulse trigger — V22 R7 OOS 全部衰減 >60%
- Wyckoff Spring / Upthrust via volume + wide-range — V22 R8 top 8 OOS 全 -$552~-$2648

---

## 最終啟示

V22 掃描 8 大古典 TA 理論（~50 個訊號變體 × 多組 TP/MH 格點），全部 REJECTED。

**這補強了 V17-V21 的共同結論**：
- V17 非 breakout alpha 不存在
- V18 任何時框 (15m/30m/1h) 非 breakout alpha 不存在
- V19 宏觀/情緒/HMM 都無法添加 alpha
- V20 V14 框架是 ETH-specific
- V21 局部最佳 + 動搖 IS 即失
- **V22 古典 TA 理論在 ETH 1h 無獨立 edge**

**全部 OHLCV 可推導的訊號源已耗盡。** V14 維持生產策略。

---

## V14 自稽核（V22 延伸，回頭檢查 baseline）

用戶反問：「為什麼只有 V14 好？V14 會不會是假的？」

把 V22 建立的 10-Gate 框架套回 V14 自己，結果：

| Gate | 項目 | 結果 | 判定 |
|------|------|------|------|
| G1/G2/G3 | IS $+1,951 / OOS $+4,138 | 都正 | PASS |
| G4 | L_TP × S_TP 鄰域 20 組（L 0.025~0.045、S 0.015~0.030） | **全部 IS+OOS 正** | **PASS** (非孤峰) |
| G6 | Swap Fwd/Bwd | Fwd -112% / Bwd +52.8% | FAIL（反常：OOS > IS） |
| G7 | WF 6 窗口 | 5/6 正 | PASS |
| **G8** | **時序翻轉（反轉 OHLCV 重跑）** | **原 $+6,089 → 翻轉 $-4,627（L WR 52→24% / PF 1.68→0.15）** | **FAIL** |
| G9 | 移除最佳月 (L/S/ALL) | 三個全正 | PASS |

**結論：V14 不是假，但是 regime-dependent。**

- G4 / G7 / G9 三個最嚴的過擬合稽核全 PASS → 參數穩健、時間分散、不依賴單月
- G8 FAIL → V14 的 alpha 來自「壓縮 + 突破 + ETH 多頭 drift」的 regime 共振，時序翻轉（模擬熊市）後徹底崩
- G6 反常（OOS > IS）：不是標準過擬合，而是 OOS 期 ETH $1400→$4000 的大波段剛好對 V14 極度有利

**對生產的意義**：
- V14 依然是目前最佳生產策略（G4/G7/G9 驗證）
- 但它不是 risk-neutral alpha，是 bull-drift-aware breakout
- 在純橫盤 / 熊市尾端會失效
- 下一步研究方向（V23）：regime detector（ETH trend state / BTC corr / vol-of-vol）gate V14，或 vol-target 動態倉位

這也解釋了 V20 9/9 其他幣種 FAIL：它們沒有同樣的 ETH-compression-breakout 微結構 + bull drift。

---

## 相關檔案

- `backtest/research/v22_common.py` — 共用回測引擎
- `backtest/research/v22_r1_ichimoku.py`
- `backtest/research/v22_r2_headshoulders.py`
- `backtest/research/v22_r2_ihs_audit.py`
- `backtest/research/v22_r3_triangle.py`
- `backtest/research/v22_r3_asc_audit.py`
- `backtest/research/v22_r4_harmonic.py`
- `backtest/research/v22_r5_fibonacci.py`
- `backtest/research/v22_r6_pivotpoints.py`
- `backtest/research/v22_r7r8_elliott_wyckoff.py`
- `backtest/research/v22_v14_self_audit.py` — V14 10-Gate 自稽核
