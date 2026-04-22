# V24 Research — V14+R Risk Engineering

**研究日期**：2026-04-22
**基底**：V14+R（V23 R5 推薦：TH_UP=0.045 / TH_SIDE=0.010）
**目標**：在不動 V14+R alpha 的前提下，降低尾端風險、提升 Sharpe、或透過多標的分散
**結果**：**BEST VERSION = V14+R @ 可調槓桿；A/C 方向 REJECTED**

---

## 研究架構

三個並行方向：

| 方向 | 主題 | 變數 | 結果 |
|---|---|---|---|
| B | Leverage recalibration | Notional/Fee per-side | **完成：線性可調** |
| A | Vol overlay on V14+R | ATR/RV/HL + threshold | **REJECTED** |
| C | Multi-asset diversification | Coin × TF × Donchian | **REJECTED** |

---

## Direction B — Leverage Recalibration（完成）

### 研究設計

帳戶 $1,000，保證金視為 $200；notional = leverage × 保證金。
Fee 與 CB 門檻按 notional 比例縮放（保持 % 風險不變）。
基底 V14+R 鎖定 TH_UP=0.045 / TH_SIDE=0.010（V23 R5）。

測試 9 個配置：uniform（5x/7.5x/10x/15x/20x）+ mixed（L/S 不對稱）

### 關鍵發現

**1. Uniform 配置 = 線性可調風險儀**

| 配置 | PnL $ | IS $ | OOS $ | MDD $ | Worst30 $ | Worst30 % | Sharpe | WR% | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20x baseline | +6503 | +2209 | +4294 | 438 | -373 | **-37.3%** | 6.03 | 60.8 | 2.44 |
| 15x | +4877 | +1657 | +3220 | 329 | -279 | -27.9% | 6.03 | 60.8 | 2.44 |
| 10x | +3251 | +1104 | +2147 | 219 | -186 | **-18.6%** | 6.03 | 60.8 | 2.44 |
| 7.5x | +2438 | +828 | +1610 | 164 | -140 | -14.0% | 6.03 | 60.8 | 2.44 |
| 5x | +1626 | +552 | +1073 | 110 | -93 | **-9.3%** | 6.03 | 60.8 | 2.44 |

核心觀察：
- PnL / MDD / Worst30 全部按 notional 比例線性縮放
- Sharpe 保持 6.03 不變（risk/return ratio invariant）
- 槓桿純粹是風險偏好選擇，不影響 edge 品質

**2. Mixed L/S 配置反而劣化 Sharpe**

| 配置 | PnL $ | Sharpe | Worst30 |
|---|---:|---:|---:|
| L 20x / S 10x | +4755 | 5.58 | -300 |
| L 10x / S 20x | +4983 | 5.76 | -281 |
| L 15x / S 10x | +3995 | 5.82 | -243 |
| L 10x / S 15x | +4117 | 5.94 | -223 |

同總槓桿下，mixed 全部不如 uniform（因改變 CB scaling 導致 trade pattern 偏移）。

### Direction B 結論：三檔風險輪廓推薦

| 風險偏好 | 配置 | 2Y PnL | Worst30 % | 適用情境 |
|---|---|---:|---:|---|
| **保守** | 5x uniform | +$1,626 | -9.3% | 想驗證實盤 / 不願看到 10% 以上單月回撤 |
| **均衡** | 10x uniform | +$3,251 | -18.6% | 帳戶資金接受 ±20% 波動 |
| **積極** | 20x baseline | +$6,503 | -37.3% | V14+R 原配置，接受 ~40% 尾端 |

- Sharpe 6.03 在三檔之間無差異，選擇純風險偏好
- 「最佳」版本視資金屬性而定：**paper 測試建議 10x**（$3K/2Y = +325% 年化 on $1K）；實盤上線再視持倉習慣調整

腳本：`backtest/research/v24_direction_b_leverage.py`
引擎：`backtest/research/v24_engine.py`（擴充 v23_overlay_engine，per-side notional/fee/CB）

---

## Direction A — Vol Overlay on V14+R（REJECTED）

### 研究設計

在 V14+R block gate 上疊加波動 overlay（OR 合併 block bars），目標改善 Worst30 或 Sharpe，但 PnL 降幅 <5%。

允許變數：
- ATR(14) / ATR(20) / 200-bar rolling percentile
- Realized vol（20-bar log return std）/ pctile
- HL range 20-bar mean / pctile

禁區：GK（雙重計算）、波動預測方向、複製 V23 Path V 參數

新 Gates：
- **A-G11**：疊加後 overlay 貢獻的 OOS/IS ratio ≥ 0.47×（R gate 的參考比率）
- **A-G12**：vol block bars 與 R block bars 重疊率 <60%（保證新增 overlay 有獨立資訊）

### 結果

測試 23 個配置 × 基準 V14+R（$+6,503 / Sharpe 6.03 / Worst30 -$373）：

| 典型配置 | PnL $ | Sharpe | Worst30 $ | ΔPnL |
|---|---:|---:|---:|---:|
| ATR14_pct>85 both | +5488 | 5.22 | -373 | -1015 |
| ATR14_pct>90 Sonly | +6121 | 5.66 | -373 | -382 |
| ATR14_pct>85 Sonly | +6157 | 5.75 | -373 | -346 |
| RV20_pct>90 both | +5895 | 5.70 | -378 | -607 |
| ATR14_pct<10 both | +5834 | 5.60 | -316 | -669 |

KPI 門檻：Sharpe ≥ 6.63 **或** Worst30 ≥ -$298；PnL ≥ $6,177

**0/23 配置通過 KPI**。最佳候選 ATR14>85 S-only：PnL 降 5.3%（剛過門檻）、Sharpe 從 6.03 → 5.75（劣化 4.6%）、Worst30 **完全不變**。

### 根因分析

V14+R 的 R gate 已吸收了 vol 過濾能做的風險工程：
- R gate block 在 L-UP 極端 / S-SIDE chop 時段 — 這些也是高波動 regime
- 疊加 vol filter 只能：(a) 移除已被 R block 的 bar（無效）或 (b) 多刪獨立 trade（降 PnL）
- Worst30 不變代表：當最壞 30d 回撤形成時，vol 過濾並未 block 住那些 bar（因為那些通常是 R 已 block 範圍外的 surprise 損失）

**Direction A REJECTED**：V14+R 的 regime gate 與 vol gate 資訊正交性不足，vol overlay 無法額外改善尾端。

腳本：`backtest/research/v24_direction_a_vol_overlay.py`

---

## Direction C — Multi-asset Diversification（REJECTED）

### 研究設計

分配 $700 給 V14+R（notional 從 $4000 縮為 $2800），$300 給獨立策略。
策略骨架（V20 規則：不能照搬 V14 參數）：**Donchian N-bar breakout + SMA200 slope filter**，long-only。
標的：BTC/SOL/BNB/XRP/DOGE/ADA/AVAX/BCH/LINK/LTC/MATIC
時框：4h / 12h / 24h（1h 聚合）
參數：Donchian N ∈ {20, 30}，Slope threshold ∈ {0.02, 0.03, 0.05}

### Round 1：KPI 初篩

KPI：PnL ≥ $30 / |r_monthly with ETH| < 0.3 / Worst30 ≥ -$90

- 掃 166 配置，37/166 通過初篩
- Top 候選：BTC 24h / DOGE 4h / XRP 4h 等

### Round 2：IS/OOS 驗證

逐個候選做 50:50 IS/OOS split，檢查：
1. IS/OOS 同號
2. r_IS 與 r_OOS 皆 |r| < 0.3
3. Combined portfolio Sharpe 改善

| 候選 | N(IS/OOS) | IS $ | OOS $ | SameSign | r_IS | r_OOS | Verdict |
|---|---|---:|---:|---|---:|---:|---|
| BTC 24h DonN=20 Sl=0.05 | 0/8 | 0 | +70 | N | 0 | -0.20 | IS/OOS FAIL |
| BTC 4h DonN=30 Sl=0.02 | 22/10 | +69 | +29 | Y | **+0.48** | -0.30 | \|r\|≥0.3 |
| DOGE 4h DonN=30 Sl=0.02 | 34/18 | +113 | +50 | Y | **+0.33** | -0.03 | \|r\|≥0.3 |
| XRP 4h DonN=30 Sl=0.05 | 31/6 | +122 | +26 | Y | **+0.46** | +0.07 | \|r\|≥0.3 |
| SOL 4h DonN=30 Sl=0.02 | 18/26 | +10 | +59 | Y | **+0.37** | -0.17 | \|r\|≥0.3 |

**10/10 候選失敗**：
- BTC 24h：樣本太薄（N=8-12），前半段 IS 連 SMA200 都沒完整，data mining 風險
- 4h 候選：IS 期間 r >= 0.3，說明 ETH V14+R 多頭做多與其他 alt coin 趨勢策略在 crypto 多頭共漲期高度相關

### Direction C 結論

**根因**：加密幣種間在趨勢型策略（Donchian breakout + trend filter）下結構性相關。
- ETH V14+R 本質是 crypto bull-drift 的捕獲（V14 regime-dependent，見 V22 G8 FAIL）
- 其他主流幣的 breakout 策略在同一段 bull 期被同樣的宏觀 regime driven

要找到 low-correlation alpha，需要：
- (a) 不同方向策略（mean-reversion vs trend-follow）— 但 V12/V17/V18/V19 已證 ETH 無非 breakout alpha，其他幣不測過樣本更弱
- (b) 非 crypto 資產 — 超出本專案範圍（帳戶限 Binance Futures）
- (c) 不同時間尺度的獨立 driver — 嘗試 24h TF 但樣本數 N=8-12 過少

**Direction C REJECTED**：Binance Futures 加密幣籃內無法組出與 ETH V14+R 低相關 + IS/OOS 穩健 + 有效改善總 Sharpe 的獨立策略。

腳本：
- `backtest/research/v24_direction_c_diversify.py`
- `backtest/research/v24_direction_c_round2.py`

---

## 最終推薦：BEST VERSION

### 結論

**V24 無新 alpha 被發現。A/C 兩方向均 REJECTED；B 方向證實槓桿是線性風險儀。**

「最佳版本」 = **V14+R（TH_UP=0.045 / TH_SIDE=0.010）** + 使用者選擇的槓桿。
無任何 overlay / hedge / vol filter 能在不犧牲 edge 的前提下改善尾端。

### 三檔部署建議

| 階段 | 推薦配置 | 2Y 回測 PnL | Worst30 | 適用 |
|---|---|---:|---:|---|
| **Paper 驗證期** | V14+R @ **10x** | +$3,251 | -18.6% | 正在運行 paper（已是 20x），建議先拉到 10x 壓測執行一致性 |
| **實盤初期** | V14+R @ **10x** 或 **15x** | +$3,251 ~ +$4,877 | -18.6% ~ -27.9% | 接受中等波動、重點是策略與實盤對齊 |
| **穩定運行期** | V14+R @ **20x** | +$6,503 | -37.3% | 帳戶已證一致，原始 V14+R 配置最大化收益 |

### 實施步驟

1. **先不動當前 paper 配置（20x）**，繼續觀察 V14 vs V14+R 實盤對齊度
2. V23 已完成 V14+R promotion（12/13 gates）— 可隨時切換到 V14+R
3. 若切換到 V14+R，按使用者風險偏好選擇對應 leverage（修改 `v24_engine.py` 的 notional 參數即可，不需改 strategy.py 主邏輯；也可在 executor.py 開 notional override）

### 什麼情況下才該再動 alpha

本 V24 結束標記：**OHLCV alpha 已在 V14+R（12/13 gates）徹底閉合**，以下情境才值得重啟：

- 有新數據源（CoinGlass / 訂單簿 / L2 / funding / 鏈上）
- 新市場（非 Binance Futures、非加密）
- 新頻率（sub-1h tick/10s 數據）

---

## 研究腳本索引

| 腳本 | 用途 |
|---|---|
| `v24_engine.py` | 擴充引擎（per-side notional/fee/CB） |
| `v24_direction_b_leverage.py` | Direction B 槓桿權衡表 |
| `v24_direction_a_vol_overlay.py` | Direction A vol overlay 掃描 |
| `v24_direction_c_diversify.py` | Direction C 多標的掃描（Round 1） |
| `v24_direction_c_round2.py` | Direction C IS/OOS 驗證（Round 2） |

---

**V24 研究正式結案 — BEST VERSION 為 V14+R @ 可調槓桿，推薦 10x 作為 paper/實盤過渡期基準。**
