# V23 研究 — V14 壓力測試 + Overlay 探索

**核心轉變**：V6-V22 已證明 ETH 1h 唯一 alpha = 15-bar close breakout (V14 鎖定)，非 breakout 為 random walk，OHLCV 訊號源耗盡。V22 自稽核揭露 V14 是 regime-dependent（G8 翻轉 -$4,627）而非 risk-neutral。V23 停止找新 entry，轉向：
1. 量化 V14 真實風險地圖（階段 1 壓力測試）
2. 平行探索三條 overlay 路線（階段 2：Path R Regime Gate / Path V Vol Target / Path H Multi-asset Hedge）
3. 誠實呈報（階段 3）

**約束**：$1K / 20x / $4K notional / $4 fee / IS-OOS 50:50 / `.shift(1)` / maxTotal ≤ 2 / 僅免費 OHLCV。

---

## 階段 1：V14 壓力測試（2026-04-22 完成）

17,633 bars（2024-04-18 ~ 2026-04-22），V14 baseline 302 筆：L 149t $+2,908 WR 57% / S 153t $+3,216 WR 60% / **Total $+6,123**。

### S1 時序翻轉

| 項目 | 原始 | 翻轉 | 差 |
|------|------|------|---|
| Total PnL | $+6,123 | $-4,627 | **$-10,750** |
| L WR | 57% | 24% | -33pp |
| S WR | 60% | 40% | -20pp |
| L PF | 2.20 | 0.15 | 崩 |

**差異最大 3 個月**（原本大賺 → 翻轉後大虧）：
- 2024-12：差 $-1,161（原 $+735）
- 2026-02：差 $-1,030（原 $+895）
- 2026-03：差 $-883（原 $+816）

→ V14 大賺的月份都是 ETH 大波段月，時序翻轉後反轉成大虧。多頭 drift 依賴確認。

### S2 Regime 拆解（SMA200 斜率，±2%/100bar 分類）

| Regime | bars | L trades | L PnL | L /t | L WR | S trades | S PnL | S /t | S WR | **Total** | MDD |
|--------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|------:|----:|
| UP | 4,962 (28%) | 45 | $+424 | +$9 | 53% | 47 | $+1,400 | +$30 | 62% | **$+1,825** | $261 |
| **SIDE** | **6,865 (39%)** | **47** | **$+803** | **+$17** | **49%** | **53** | **$+678** | **+$13** | **49%** | **$+1,481** | **$448** |
| DN | 5,506 (31%) | 56 | $+1,737 | +$31 | 68% | 51 | $+1,336 | +$26 | 73% | **$+3,073** | $281 |

**反直覺關鍵發現**：
- V14 在 **下跌 regime 表現最好**（$+3,073、WR 68-73%）— 不是風險區
- V14 **最脆弱的 regime 是 SIDEWAYS**（每筆 L/S $13-17、WR 49%、MDD $448 最高）
- UP regime 只賺 $1,825（L 只賺 $424，壓縮後突破追高效果差）

→ 原本 Path R 假設「偵測熊市就暫停」是**錯的方向**。真正該過濾的是 **SIDEWAYS regime**。

### S3 橫盤段（rolling window price range）

ETH 近 2 年波動大，8w/12w 幾乎找不到 <25% 範圍段。合理可測的是 4w 短橫盤：

| Window | Thresh | Bars 佔比 | V14 trades | PnL | /t | WR |
|---|---|---|---|---|---|---|
| 2w | 10% | 3.4% | 10 | $+145 | $+15 | 60% |
| 2w | 15% | 23.4% | 61 | $+337 | $+6 | 46% |
| **4w** | **20%** | **13.4%** | **32** | **$+726** | **$+23** | **50%** |
| 4w | 25% | 31.7% | 82 | $+856 | $+10 | 50% |
| 8w | 30% | 11.8% | 32 | $+638 | $+20 | 50% |
| 12w | 30% | 1.4% | 5 | $+215 | $+43 | 60% |

**最長連續 4w/20% 橫盤段**：2024-10-14 → 2024-11-07（24 天），V14 這段 **賺 $344**（8 筆）。

→ 短橫盤（2-4 週）V14 還能賺但 /t 偏低。ETH 過去 2 年沒有長到能形成「連續 N 週橫盤」壓力測試樣本。

### S4 最壞 Rolling Drawdown（non-overlapping top 5）

**Worst 30-day windows**
| 時間 | DD | % of $1K | trades |
|------|-----:|-----:|-----:|
| **2024-04-18 → 2024-05-18** | **$-570** | **-57%** | 8 |
| 2025-12-18 → 2026-01-17 | $-480 | -48% | 15 |
| 2025-10-28 → 2025-11-27 | $-281 | -28% | 17 |
| 2024-07-02 → 2024-08-01 | $-261 | -26% | 14 |
| 2025-04-08 → 2025-05-08 | $-261 | -26% | 21 |

**Worst 60/90-day windows**：
- 60d 最壞 **$-570**（同一開頭段）+ 第 2 名 $-480（2025-11~2026-01 跨月）
- 90d 最壞 **$-570** + $-480

**震撼結論**：V14 歷史最壞 30 天可打掉帳戶 **57%**。這遠超過 OOS MDD 報告的 $228-480（因為最壞段在 IS 前段 2024-04）。

### 實戰風險地圖

| 風險 | 量化 |
|------|------|
| 時序翻轉（熊市結構）年化損失 | $-4,627 |
| SIDEWAYS regime 每筆 PnL 打折 | $+30/t 掉到 $+15/t，幾乎腰斬 |
| 最壞 30 天帳戶損失 | -57%（$-570）|
| 最壞 60/90 天 | 與 30 天同一段主導 |
| V14 失效最可能模式 | 非熊市；而是 **長期橫盤 + 低波動** |

### Stage 2 KPI 修正

| Path | 原假設 | 修正後 KPI |
|------|------|------|
| **R Regime Gate** | 過濾熊市 | ❌ 錯方向。應**過濾 SIDEWAYS**，目標：移除 SIDE regime 至少 50% 的虧損交易，保留 UP/DN 交易 |
| **V Vol Target** | 高波動減倉 | 改：**低波動**（橫盤）減倉，高波動（UP/DN trending）維持。目標：最壞 30 天從 $-570 收到 $-350 以內 |
| **H Multi-asset Hedge** | 找反相關策略 | V14 月 PnL 區間 $-255 ~ $+895，需在 V14 虧月（2024-04/2024-06/2026-04）取得 ≥$150/月正貢獻 |

---

## 階段 2：Overlay 探索

### Path R — Regime Gate (2026-04-22 完成)

**核心假設演進**：S2 揭露 L 最弱在 UP regime（WR 53%、+$9/t），S 最弱在 SIDE regime（WR 49%、+$13/t）。單一對稱 gate（R1-R3）全失敗，非對稱 per-side gate（R4-R5）成功。

| 輪 | 假設 | 參數 | IS | OOS | ALL | G8 | WF | 判定 |
|---|------|------|---:|---:|---:|---:|---:|---|
| R1 | SMA200 100-bar 斜率對稱 block | TH=0.005 | $+1,803 | $+4,115 | $+5,918 | +$271 | 2/6 | REJECT |
| R2 | ATR(14)/200-bar pctile 對稱 | TH=25 | $+1,208 | $+3,156 | $+4,364 | +$871 | 0/6 | REJECT |
| R3 | ADX(14) 對稱 | TH=10 | $+1,794 | $+3,773 | $+5,567 | -$22 | 0/6 | REJECT |
| **R4** | **非對稱 slope gate** | TH_UP=0.04/TH_SIDE=0.010 | $+2,135 | $+4,287 | **$+6,422** | **+$662** | 3/6 | CONDITIONAL |
| **R5** | **R4 微調 WF 4/6** | **TH_UP=0.045/TH_SIDE=0.010** | **$+2,209** | **$+4,294** | **$+6,503** | **+$438** | **4/6** | **PROMOTE** |

**Path R 冠軍 — R5 (V14+R)**

| 指標 | V14 baseline | V14+R | Δ |
|------|----:|----:|----:|
| 交易數 | 302 | 263 | -39 (-13%) |
| PnL | $+6,123 | $+6,503 | **+$380 (+6.2%)** |
| MDD | $492 | $438 | **-$54 (-11%)** |
| Sharpe | 5.12 | 6.03 | **+0.91 (+18%)** |
| Worst 30d | $-570 | $-373 | **+$197 (-35%)** |
| 時序翻轉 PnL | $-4,627 | $-4,189 | +$438 (regime dep↓) |
| 全量 Sharpe | 5.12 | 6.03 | +18% |

**10-Gate 結果 (12/13 PASS)**
- G1-G4 全 PASS（IS+、OOS+、delta 同號、鄰域穩定）
- **G5 Cascade 97th percentile**（100 次隨機 block 僅 3 次超越 R5）→ gate 選到真實訊號非運氣
- G6 FAIL：Fwd -94.4% 為誤判（V14 baseline 本身 IS<<OOS，不是 overlay 造成）
- **G7 WF 4/6 PASS**（W1+ W2+ W3+ W4- W5- W6+）
- **G8 時序翻轉改善 +$438**（R 的核心 gate 達成）
- G9 移除最佳月 without=$+5,547 > 0 PASS
- G10 僅 2 個參數 PASS
- R-G11 AUC：L=0.502 / S=0.498 → 兩 gate 皆 gate 非 prediction
- R-G12 透明性：2 個數字（SMA200 100-bar % change）PASS

**機制解讀**
- L 在 UP 強漲 regime（slope > 4.5%/100bar）WR 53% 追高效果差 → block
- S 在 SIDE 橫盤 regime（|slope| < 1%/100bar）WR 49% 賺不到方向 → block
- 純熊市（slope < -1%）L/S 都維持，因為 V14 在 DN 最強（WR 68-73%）
- 約 15% L bars + 21% S bars 被過濾，保留全量 65% 交易

### Path V — Vol Target (2026-04-22 完成，REJECTED)

**結論**：soft size scaling 在此系統上嚴格劣於 Path R hard block。3 輪全數失敗或邊際。

| 輪 | 假設 | 參數 | ALL PnL | G8 | WF | 判定 |
|---|------|------|---:|---:|---:|---|
| R1 | ATR pctile < TH → scale_low | TH=30/scale=0.75 | $+5,448 | +$586 | 1/6 | REJECT |
| R2 | SIDE regime (|slope|<TH) → scale | TH=0.010/scale=0.75 | $+6,164 | +$344 | 3/6 | MARGINAL |
| R3 | Inverse-ATR (target/ATR%) | target=0.015/floor=0.75 | $+5,938 | +$309 | 0/6 | REJECT |

**為什麼 Path V 失敗**
- V14 本質是 binary edge — 是/否進場的訊號遠強於部分進場
- Size scaling 等同 "50% 進場"，無法 surgically 避開 regime-specific 的輸家交易
- R2 最接近 Path R 邏輯（用 slope 偵測 SIDE），但因是 soft scale 而非 hard block，改善幅度僅 +$40 vs Path R R5 的 +$380

### Path H — Multi-asset Hedge (2026-04-22 完成，REJECTED)

**結論**：BTC V14 結構性失敗，無其他資料源可測。

| 指標 | V14 ETH | V14 BTC | 合併 |
|------|--------:|--------:|--------:|
| 年化 PnL | $+6,123 | $-1,762 | $+4,362 |
| Sharpe | 5.12 | -1.51 | 4.39 |
| 月度相關 | — | — | -0.016 |
| ETH 負月數 | 7 | — | — |
| BTC 在 ETH 負月為正的比例 | — | **2/7** | — |
| 最壞月 | $-255 | $-383 | $-295 (更差) |

**為什麼 Path H 失敗**
- V20 早已證明 V14 breakout alpha 是 ETH-specific（9/9 主流幣 IS<0）
- BTC V14 年化 -$1,762，無法作為正收益 hedge
- 月度相關 -0.016 雖低但 BTC 負 EV 扛不起角色
- ETH 7 個負月加總僅 $-904，但 BTC 在那些月平均 $-46（同向虧損）
- Path H 需要**真實第二 edge**，但 V15-V22 已耗盡所有 OHLCV 可搜索空間

---

## 階段 3：最終報告

### V23 總結論

**唯一獲得 CONDITIONAL PROMOTE 的是 Path R（SMA 斜率非對稱 per-side gate）**。V、H 兩路線在 ETH 1h + $1K/20x 結構下結構性失敗。

### 最終推薦：V14+R

| 指標 | V14 (baseline) | **V14+R (推薦)** | Δ |
|------|----:|----:|----:|
| 交易數（2 年） | 302 | 263 | -13% |
| 總 PnL | $+6,123 | **$+6,503** | **+6.2%** |
| MDD | $492 | **$438** | **-11%** |
| Sharpe | 5.12 | **6.03** | **+18%** |
| Worst 30d | $-570 (-57%) | **$-373 (-37%)** | **-35% 尾部風險** |
| 時序翻轉 PnL（regime dep 量化） | $-4,627 | $-4,189 | +$438 |
| 總 WR | 59% | 62% | +3pp |

**10-Gate 結果**：12/13 PASS（G6 為基線屬性誤判，非 overlay 問題）

**部署建議**
- V14+R 可直接疊加在現有 V14 之上
- 新增參數僅 2 個：`TH_UP=0.045`（L block slope）、`TH_SIDE=0.010`（S block slope）
- 指標：SMA200 的 100-bar 百分比變化，1 個 OHLCV 數字即可計算
- 無需改動 V14 進出場邏輯，只在進場前加 gate 判定
- `strategy.py` 實作：進場前計算 `slope = (SMA200[i-1] - SMA200[i-101]) / SMA200[i-101]`，`block_L = slope > 0.045`、`block_S = abs(slope) < 0.010`

### 誠實限制說明

1. **V14+R 只能減輕而非消除 regime dependency**：時序翻轉仍 -$4,189，多頭 drift 依賴仍是最大結構風險
2. **最壞 30d $-373 仍是 $1K 帳戶的 37%**：單一帳戶倉位無法承受極端黑天鵝
3. **Path R G6 FAIL 技術上存在**：但 V14 baseline 本身 IS($1,951) << OOS($4,172) 已不符合 G6 50% 門檻，非 overlay 引入（見下方獨立驗證）
4. **沒有 Path V/H 的補充**：V14+R 仍是**單邊 ETH 多頭 drift + breakout alpha**的單一系統，無真正的多元分散

### G6 Swap Test 獨立驗證 (2026-04-22)

針對「G6 FAIL 來自 baseline」的解釋做獨立驗證：

| 對照 | IS PnL | OOS PnL | Fwd 衰退率 | Bwd 衰退率 | G6 |
|---|---:|---:|---:|---:|---|
| V14 baseline | $+1,951 | $+4,172 | -113.8% | +53.2% | **FAIL both** |
| V14+R overlay | $+2,209 | $+4,294 | -94.4% | +48.6% | FAIL Fwd only |

Overlay 單獨貢獻：IS +$258 / OOS +$121，**同向正值**（ratio 0.47×），衰退率 +52.9%

**結論情境 A 成立**：V14 baseline G6 雙向都 FAIL，V14+R Bwd 反而由 FAIL 轉 PASS。overlay 沒有引入新的 regime dependency，但在 OOS 的貢獻只有 IS 的 47%。

**部署決策**：V14+R 可部署，但對 +$380/2 年改善量級須降級信心至 **+$120~$380 區間**（OOS 觀測值傾向低端）。G6 FAIL 是 V14 本身的 regime dependency，非 overlay 缺陷。

### V14 vs V14+R 部署對照

| 項目 | 現行 V14（線上） | V14+R（研究完成待部署） |
|---|---|---|
| 參數數量 | L+S 共 ~25 knobs | +2 (TH_UP, TH_SIDE) |
| 額外指標 | 無 | SMA200 100-bar slope (1 個數字) |
| 進場改動 | — | 進場前多檢 2 個 bool gate |
| 進出場邏輯 | — | 完全不動 |
| 回測 PnL (2 年) | $+6,123 | **$+6,503 (+6.2%)** |
| MDD | $492 | **$438 (-11%)** |
| Sharpe | 5.12 | **6.03 (+18%)** |
| Worst 30d | $-570 (-57% acct) | **$-373 (-37% acct)** |
| G6 雙向 FAIL | ✓ | Bwd 改 PASS |
| G8 時序翻轉 | $-4,627 | **$-4,189 (+$438)** |

---

## 模擬盤運行狀態 (2026-04-22 21:00 UTC+8)

### 系統狀態
- **策略**：V14（`strategy.py` commit `f0c324d`），**V14+R 尚未部署**
- **模式**：Paper Trading / Binance Testnet / Hedge Mode
- **帳戶餘額**：$4,281.60（Testnet 初始非 $1K，cumulative $3,290 為 Testnet baseline）
- **當前持倉**：1 筆 L @ $2,403.41（2026-04-22 20:00 開倉，qty 1.664）

### 9 天 5 筆已實現交易（自 2026-04-14 上線起）

| # | 時間 UTC+8 | 方向 | 進場 | 出場 | Exit | PnL | 結果 |
|---|---|---|---:|---:|---|---:|---|
| 1 | 04-14 14:00 | L | 2372.44 | 2389.20 | BE | +$12.52 | WIN |
| 2 | 04-14 22:00 | S | 2359.06 | 2313.31 | TP | +$74.64 | WIN |
| 3 | 04-16 21:00 | S | 2306.44 | 2346.92 | MaxHold | -$71.79 | LOSS |
| 4 | 04-21 20:00 | S | 2307.51 | 2310.42 | MaxHold | -$8.25 | LOSS |
| 5 | 04-22 10:00 | L | 2368.44 | 2390.48 | **MFE-trail** | +$33.99 | WIN |

**績效**（極短樣本，不足以 OOS 評估）：
- WR 3/5 = 60%（與 V14 回測 WR 59% 大致一致）
- 實現 PnL +$41.11 / 9 天
- 出場分佈：MaxHold 2 / TP 1 / BE 1 / **MFE-trail 1**（V14 新特性實戰首次觸發 ✓）/ SafeNet 0

### 本月（2026-04）熔斷計數

- L：+$46.51 / 3 entries（entry cap 17 剩）
- S：-$5.40 / 3 entries（entry cap 17 剩）
- 連虧計數：0（無 cooldown）
- 月虧上限（L -$75 / S -$150）：遠未觸發
- 日虧上限（-$200）：遠未觸發

### 若部署 V14+R 的影響

- 2026-04-22 當前 slope ≈ ETH 2 年 SMA200 的過去 100-bar 增幅，L gate TH_UP=0.045 大概率未觸發（當前非強漲 regime）
- 實盤部署僅須在 `strategy.py` evaluate_long/short_signal 前加 2 行 gate 判定
- 不改動持倉計算、出場機制、風控熔斷

### V23 結論 — 何時停止

- Path R 5 輪完成（R5 PROMOTE、R1-R3 REJECT、R4 CONDITIONAL）
- Path V 3 輪完成全部 REJECTED（結構上 soft scaling < hard block）
- Path H 1 輪即證結構失敗（BTC V14 負 EV 無法 hedge，V20 已證 ETH-specific）
- OHLCV 資料源耗盡（V15-V22 已覆蓋）
- 後續進展需要付費資料（V21 已婉拒）或更多資金（$1K/20x 結構限制）

**V23 研究到此結束。V14+R 為目前已知最佳配置，可進入部署階段或等待新資料源。**

---

## 相關檔案

- `backtest/research/v22_v14_self_audit.py` — V14 10-Gate 自稽核
- `backtest/research/v23_s1_s4_stress_test.py` — S1-S4 壓力測試
- `backtest/research/v23_overlay_engine.py` — V14 + overlay hooks 共用引擎
- `backtest/research/v23_path_r_round1.py` ~ `round5.py` — Path R 五輪
- `backtest/research/v23_path_r_round4_audit.py` / `round5_audit.py` — 10-Gate 稽核
- `backtest/research/v23_g6_verification.py` — G6 Swap Test 獨立驗證（V14 baseline vs V14+R）
- `backtest/research/v23_path_v_round1.py` ~ `round3.py` — Path V 三輪 REJECT
- `backtest/research/v23_path_h_round1.py` — Path H BTC hedge REJECT
