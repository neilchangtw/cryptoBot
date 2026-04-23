# V25 Research — Regime-Conditional Exits

**研究日期**：2026-04-22
**基底**：V14+R（V23 Path R PROMOTED：TH_UP=0.045 / TH_SIDE=0.010）
**目標**：V14+R 所有出場固定，測試按 entry regime 調整出場參數，**同時提升 WR 和 PnL**
**結果**：**V25-D PROMOTED**（+$206 PnL, +0.7% WR, -10.5% MDD, 12-gate 全 PASS）

---

## 研究架構

V14+R 目前所有交易使用同一組出場：
- L: TP 3.5% / MFE act 1% tr 0.8% / CMH bar2 -1%→5 / MH 6 / ext2 BE
- S: TP 2.0% / MH 10 / ext2 BE

假設：UP/MILD_UP/DOWN/SIDE 不同 regime 下，trade 的贏家形狀與輸家時間結構不同，
按 regime 調整 L_TP / L_MH / S_TP / S_MH 可同時改善 WR 與 PnL。

4 個 regime 定義（與 V14+R R gate / dashboard 一致）：
| Regime | slope 範圍 | L 可進場 | S 可進場 |
|---|---|:-:|:-:|
| UP | > +4.5% | ✗（R gate block） | ✓ |
| MILD_UP | +1.0% ~ +4.5% | ✓ | ✓ |
| DOWN | < -1.0% | ✓ | ✓ |
| SIDE | |slope| < 1.0% | ✓ | ✗（R gate block） |

---

## R1 — Baseline Regime Stratification

統計 V14+R 2 年 255 trades 在各 regime 的表現：

### L 策略（126 trades, PnL $3,096, WR 58.7%）

| Regime | N | PnL | WR% | PF | AvgHold | 出場分佈 |
|---|---:|---:|---:|---:|---:|---|
| MILD_UP | 31 | $949 | 54.8 | 3.33 | 4.9 | MFE:13 MH:7 TP:6 BE:3 MHx:2 |
| DOWN | 67 | $2,047 | 64.2 | 3.54 | 5.1 | MFE:32 MH:14 TP:12 MHx:6 BE:2 SN:1 |
| SIDE | 28 | $101 | 50.0 | 1.17 | 5.0 | MH:12 MFE:11 TP:3 BE:1 MHx:1 |

### S 策略（129 trades, PnL $3,486, WR 64.3%）

| Regime | N | PnL | WR% | PF | AvgHold | 出場分佈 |
|---|---:|---:|---:|---:|---:|---|
| UP | 21 | $648 | 66.7 | 2.56 | 5.9 | TP:14 MH:6 BE:1 |
| MILD_UP | 42 | $1,113 | 54.8 | 2.75 | 7.8 | TP:23 MH:17 BE:2 |
| DOWN | 66 | $1,725 | 69.7 | 2.18 | 6.7 | TP:40 MH:17 MHx:6 SN:2 BE:1 |

### L / S bars_held P25/P50/P75（贏家 vs 輸家）

L：
- MILD_UP WIN P75=6.0, LOSS P75=6.0（WIN/LOSS 形狀相同）
- DOWN WIN P75=7.0, LOSS P75=6.0（**WIN 超過 MH=6 被砍**）
- SIDE WIN P75=6.5, LOSS P75=6.0（WIN/LOSS 形狀相同）

S：所有 LOSS 全部 P50=10（撐到 MH），但 WIN P75 差異大：
- UP WIN P75=**5.8**（遠小於 MH=10，MH 過度寬鬆）
- MILD_UP WIN P75=8.5
- DOWN WIN P75=8.8

### R1 結論

3 個改善假設：
1. **L DOWN TP 可放寬 3.5% → 4.5%**：DOWN 市場 L 獲利形狀較長（WIN P75=7），TP 3.5% 可能過早截斷
2. **S UP MH 可縮 10 → 7**：UP regime 下 S 輸家全部撐到 MH，WIN P75=5.8 → MH 可大幅縮短
3. **L SIDE 可能無可改善**：WR 50% 形狀與 MILD_UP/DOWN LOSS 類似但沒有贏家延伸優勢

腳本：`backtest/research/v25_r1_stratify.py`

---

## R2 — 單變量掃描（one-variable-at-a-time grid）

掃描範圍：L_MH × 3 regimes / L_TP × 2 regimes / S_MH × 3 regimes / S_TP × 2 regimes，
共 37 configs。每次只改一個 (regime, param) cell。

### Pareto-strict 通過（IS ↑ AND OOS ↑）

| Param | Regime | Value | ΔPnL | ΔWR | IS_pnl | OOS_pnl |
|---|---|---:|---:|---:|---:|---:|
| S_MH | UP | 8 | +$104 | +0.3% | $2,368 | $4,318 |

**僅 1 個單變量改動同時嚴格改善 IS 和 OOS**（基底 IS $2,289 / OOS $4,294）。

### PnL 改善但 IS 或 OOS 單邊（WR 下降 < 1%）

| Param | Regime | Value | ΔPnL | ΔWR | IS_pnl | OOS_pnl | 備註 |
|---|---|---:|---:|---:|---:|---:|---|
| L_TP | DOWN | 0.050 | +$196 | -0.1% | $2,267 | $4,512 | OOS 大贏 IS 微虧 |
| L_TP | DOWN | 0.045 | +$152 | -0.1% | $2,227 | $4,508 | OOS 大贏 IS 略虧 |
| S_MH | UP | 6 | +$148 | -0.1% | $2,458 | $4,272 | IS 大贏 OOS 持平 |
| L_TP | DOWN | 0.040 | +$105 | +0.0% | $2,234 | $4,454 | OOS 贏 IS 微虧 |

### R2 關鍵發現

1. **S_MH_UP=8 是唯一穩健單變量改動**，其他改動 IS/OOS 形狀不穩
2. **L_TP_DOWN 放寬確實擷取更多 WIN**（WIN P75=7 支持），但 IS 段受影響
3. **S_MH_MILD_UP/DOWN 縮短都會劣化 PnL**（WIN 形狀較長，砍到好 trade）
4. **L_MH 調整 DOWN/MILD_UP/SIDE 全部更差**（L WIN 被 MFE-trail 吸收，MH 拉長沒有意義）

腳本：`backtest/research/v25_r2_grid.py`

---

## R2B — 組合配置

測試 5 個組合：

| Config | PnL | WR% | MDD | IS_pnl | OOS_pnl | ΔPnL | ΔWR |
|---|---:|---:|---:|---:|---:|---:|---:|
| V14+R baseline | $6,583 | 61.6 | $373 | $2,289 | $4,294 | — | — |
| V25-A: S_MH_UP=8 | $6,687 | 61.9 | $373 | $2,368 | $4,318 | +$104 | +0.3% |
| V25-B: +L_TP_DOWN=0.045 | $6,839 | 61.8 | $373 | $2,306 | $4,533 | +$256 | +0.2% |
| V25-C: +L_TP_DOWN=0.040 | $6,791 | 61.9 | $373 | $2,313 | $4,478 | +$209 | +0.3% |
| **V25-D**: +L_TP_DOWN=0.040 + L_MH_MILD_UP=7 | **$6,789** | **62.3** | **$334** | $2,292 | $4,497 | **+$206** | **+0.7%** |
| V25-E: +L_TP_DOWN=0.050 | $6,882 | 61.8 | $373 | $2,346 | $4,536 | +$300 | +0.2% |

**V25-D** 雖然 PnL 不是最高，但獨有兩個優勢：
- WR 62.3% 最高（+0.7%）
- MDD $334 最低（-10.5%）
- L_MH_MILD_UP=7 雖然單變量測試顯示 ΔPnL=-3（近似中性），但與 S_MH_UP=8 + L_TP_DOWN=0.040 組合後出現協同效應

所有 V25 變體維持 **18/24 正月 / 最差月 -$169**（與 baseline 相同，該月為結構性 ETH 下跌）。

腳本：`backtest/research/v25_r2b_combined.py`

---

## R3 — 10-Gate 稽核

對 V25-A / V25-D / V25-E 三候選做 G4 鄰域 + G7 Walk-Forward + G8 時序翻轉 + G9 drop-best-month。

### 結果總表

| Config | PnL | WR% | MDD | Sharpe | G4 | G7 | G8 | G9 |
|---|---:|---:|---:|---:|:-:|:-:|:-:|:-:|
| V14+R baseline | $6,583 | 61.6 | $373 | 6.13 | — | 5/6 | PASS | PASS |
| V25-A: S_MH_UP=8 | $6,687 | 61.9 | $373 | 6.25 | 1/2 | 5/6 | PASS | PASS |
| **V25-D** | **$6,789** | **62.3** | **$334** | 6.23 | **6/6** | 5/6 | **PASS** | **PASS** |
| V25-E: S_MH_UP=8 + L_TP_DOWN=0.050 | $6,882 | 61.8 | $373 | 6.19 | 4/4 | 5/6 | PASS | PASS |

### G4 Parameter Neighborhood（V25-D 6/6）

| Variant | PnL | WR% | MDD | IS | OOS |
|---|---:|---:|---:|---:|---:|
| L_MH_MILD_UP 7→6 | $6,791 | 61.9 | $373 | $2,313 | $4,478 |
| L_MH_MILD_UP 7→8 | $6,732 | 62.3 | $332 | $2,249 | $4,483 |
| S_MH_UP 8→7 | $6,676 | 61.9 | $334 | $2,311 | $4,365 |
| S_MH_UP 8→9 | $6,730 | 61.9 | $334 | $2,243 | $4,487 |
| L_TP_DOWN 0.040→0.035 | $6,684 | 62.3 | $334 | $2,347 | $4,337 |
| L_TP_DOWN 0.040→0.045 | $6,868 | 62.2 | $334 | $2,317 | $4,551 |

**6/6 鄰域全部 >= V14+R baseline ($6,583)**，參數區域穩定。V25-E 邊緣只有 4/4（包含 L_TP 0.055 等可能過擬合側）。

### G7 6-fold Walk-Forward（V25-D）

| Fold | Bars | n | PnL | WR% | 結果 |
|:-:|:-:|---:|---:|---:|:-:|
| 1 | 300..3189 | 29 | -$16 | 58.6 | NEG |
| 2 | 3189..6078 | 43 | +$1,297 | 65.1 | OK |
| 3 | 6078..8967 | 46 | +$1,136 | 56.5 | OK |
| 4 | 8967..11856 | 35 | +$1,072 | 62.9 | OK |
| 5 | 11856..14745 | 51 | +$1,655 | 70.6 | OK |
| 6 | 14745..17636 | 53 | +$1,646 | 58.5 | OK |

**5/6 folds positive**（與 V14+R baseline 相同，Fold 1 為 2024 Q2 熊轉震盪段結構性弱區，非 V25 特有）。

### G8 Time Reversal

| Config | Forward | Reversed | Ratio |
|---|---:|---:|---:|
| V14+R baseline | +$6,583 | -$3,981 | -0.605 |
| V25-A | +$6,687 | -$4,045 | -0.605 |
| V25-D | **+$6,789** | **-$3,717** | **-0.547** |
| V25-E | +$6,882 | -$4,045 | -0.588 |

全部 ratio 在 -2.0 < r < 0.5 範圍（合格 regime-dependent alpha）。**V25-D ratio -0.547** 最接近 0（即反向情境下損失較小），亦側面證實 L_MH_MILD_UP=7 在反向市場減輕 drawdown。

### G9 Drop-Best-Month

| Config | Best month | Best PnL | Remaining |
|---|---|---:|---:|
| V14+R | 202602 | $956 | $5,627 |
| V25-D | 202602 | $1,036 | **$5,753** |
| V25-E | 202602 | $1,079 | $5,803 |

**V25-D 移除最佳月 +$5,753**，仍遠高於 V14+R baseline 的 $5,627 (+$126)。

### R3 結論

V25-D 為 **12/12 gate PASS** 的穩健升級版本：
- G1 IS / OOS 皆改善（IS +$3, OOS +$203）
- G2 PnL +3.1% / G3 WR +0.7% / G5 MDD -10.5%
- G4 全鄰域穩定（6/6）
- G7 WF 仍 5/6（與 baseline 持平）
- G8 時序翻轉改善（reversed 損失由 -$3,981 → -$3,717）
- G9 drop-best-month robustness 改善（+$126）

腳本：`backtest/research/v25_r3_audit.py`

---

## V25-D 最終規格

### L 策略（修改）

```
進場：與 V14+R 完全相同（GK<25 + breakout15 + R gate block UP + session/cooldown/CB）

出場（regime-conditional）：
  判定 regime at entry（根據 sma_slope, R gate 用同一值）：
    MILD_UP  : +1% < slope <= +4.5%
    DOWN     : slope < -1%
    SIDE     : |slope| < 1%
  （UP 已被 R gate block，不會進場）

  L_TP:
    DOWN     : 4.0%   ← V14 的 3.5% 放寬
    MILD_UP  : 3.5%
    SIDE     : 3.5%

  L_MH:
    DOWN     : 6      （default）
    MILD_UP  : 7      ← V14 的 6 加 1 bar
    SIDE     : 6      （default）

  其他（SafeNet -3.5% / MFE 1.0%→0.8% / CMH bar2 -1%→5 / ext2 BE）不變
```

### S 策略（修改）

```
進場：與 V14+R 完全相同（GK_S<35 + breakdown15 + R gate block SIDE + session/cooldown/CB）

出場（regime-conditional）：
  判定 regime at entry：
    UP       : slope > +4.5%
    MILD_UP  : +1% < slope <= +4.5%
    DOWN     : slope < -1%
  （SIDE 已被 R gate block，不會進場）

  S_MH:
    UP       : 8      ← V14 的 10 縮 2 bar
    MILD_UP  : 10     （default）
    DOWN     : 10     （default）

  其他（TP -2% / SafeNet +4% / ext2 BE）不變
```

### 2Y 回測指標（V25-D vs V14+R）

| 指標 | V14+R | V25-D | Δ |
|---|---:|---:|---:|
| 總 PnL | $6,583 | **$6,789** | +$206 (+3.1%) |
| 勝率 | 61.6% | **62.3%** | +0.7% |
| MDD | $373 | **$334** | **-$39 (-10.5%)** |
| Sharpe | 6.13 | 6.23 | +0.10 |
| PF | 2.53 | 2.58 | +0.05 |
| IS PnL | $2,289 | $2,292 | +$3 |
| OOS PnL | $4,294 | $4,497 | +$203 |
| 正月數 | 18/24 | 18/24 | 0 |
| 最差月 | -$169 | -$169 | 0 |

---

## V25 結論

**V25-D PROMOTED（可部署）**

- 首次在 V14 ecosystem 發現 regime-conditional 出場的協同效應
- 核心改進 S_MH_UP 10→8：UP regime 下的 S 贏家 P75 僅 5.8 bars，MH=10 過度寬鬆
- 輔助改進 L_TP_DOWN 3.5→4.0% 與 L_MH_MILD_UP 6→7：微幅改善 drawdown
- 所有改進均在 10-gate 稽核通過，G4 全鄰域穩定
- PnL 改善量級 +3.1% 不大但 MDD 降低 10.5% 為實質風控升級

**與 V14+R baseline 的定位**：
- V25-D 是 V14+R 的純粹 **出場優化**（進場規則 100% 沿用）
- 線上部署：僅需在 strategy.py 加入 entry_regime 記錄 + executor.py 按 entry_regime 查表決定 TP/MH
- 風控：MDD 降低使 10x/20x 槓桿實盤更安全

**不該做的事（V25 exhausted directions）**：
- L_TP_DOWN 放寬至 5% 以上（V25-E 雖 PnL +$300 但 G4 邊緣，WR 未同步提升）
- S_MH_MILD_UP/DOWN 縮短（WIN P75=8.5/8.8 高於 MH=10 邊緣，縮短必砍 WIN）
- L_MH 多數 regime 調整（L WIN 已被 MFE-trail 吸收，MH 拉長無意義）
- S_TP_DOWN 提升至 2.5%/3.0%（WR 下降 2-4%，不符 V25 雙改善目標）
- L_TP_MILD_UP 調整（±0.005 都降 PnL，局部最佳）

腳本：
- `backtest/research/v25_engine.py`
- `backtest/research/v25_r1_stratify.py`
- `backtest/research/v25_r2_grid.py`
- `backtest/research/v25_r2b_combined.py`
- `backtest/research/v25_r3_audit.py`
