# V32 L13/S16 完整 10-Gate 稽核

日期：2026-08-07

候選：L 突破回看 13 根、S 突破回看 16 根

## 稽核範圍

- 引擎：目前 `V14+R+V25-D` `v14_export_trades.py`
- 唯一策略變更：`L_BRK=13`、`S_BRK=16`
- 其他 GK、Regime gate、TP、MFE-trail、MaxHold、SafeNet、冷卻、熔斷全部維持線上版本
- 成交：`realistic=True`，額外滑價 0 bps
- 保證金：全程 200U 研究基準，另以目前 200→300→500U 排程複核過排序未改變
- 資料：ETHUSDT 1h，2024-07-30 14:00～2026-07-30 12:00，17,519 根
- IS/OOS：時間中點切分
- Walk-Forward：6 個連續時間區段

## 基準比較

| 項目 | L15/S15 基準 | L13/S16 候選 | 差異 |
|---|---:|---:|---:|
| IS PnL | +$3,283 | +$3,256 | -$27 |
| OOS PnL | +$4,637 | +$4,878 | +$240 |
| 全期間 PnL | +$7,920 | +$8,134 | +$214 |
| OOS 交易數 | 152 | 152 | 0 |
| OOS WR | 61.8% | 62.5% | +0.7pp |
| 全期間 MDD | $330 | $327 | -$3 |

## 10-Gate 結果

| Gate | 檢查 | 結果 | 主要證據 |
|---|---|---|---|
| G1 | IS 正收益 | **PASS** | IS +$3,256；L/S 兩側皆正 |
| G2 | OOS 正收益 | **PASS** | OOS +$4,878；L/S 兩側皆正 |
| G3 | IS/OOS 相對基準改善方向一致 | **FAIL** | IS -$27，但 OOS +$240；方向相反 |
| G4 | 3×3 參數鄰域 | **PASS** | 8/8 鄰近組合 OOS 正收益，且均達候選 OOS 的 70% 以上 |
| G5 | 隨機 breakout cascade/null test | **PASS** | 候選 +$8,134；100 次隨機同數量信號平均 -$652；候選排名第 100 百分位 |
| G6 | IS/OOS swap degradation | **PASS** | Forward +49.8%、Backward -33.2%，均低於 50% 門檻 |
| G7 | 6-fold Walk-Forward | **PASS** | 6/6 正收益；各 fold：+$1,282、+$996、+$1,002、+$2,412、+$1,692、+$749 |
| G8 | 時序翻轉 | **FAIL** | 原始 +$8,134；反轉 OHLCV 後 -$4,028 |
| G9 | 移除最佳月份 | **PASS** | 最佳月 2026-02 +$850；移除後仍 +$7,283 |
| G10 | 自由度/參數複雜度 | **CONDITIONAL** | 上線只增加 2 個參數，但候選由 441 組 L/S 組合掃描選出 |

## 結論

```text
7/10 PASS
1 CONDITIONAL
2 FAIL
VERDICT: NO PROMOTION
```

L13/S16 的交易品質、參數鄰域、隨機 null test、Walk-Forward 與最佳月剔除都通過；但不能 promotion 的原因有兩個：

1. 相對 L15/S15 的改善只出現在 OOS，IS 反而少 $27，沒有形成跨時段一致的增量證據。
2. 時序翻轉後為 -$4,028，確認候選仍依賴目前 ETH 的市場結構與 regime，並非 risk-neutral、跨時序穩健的 edge。

因此：

- **不部署 L13/S16。**
- L13 可以保留為研究候選，但需等待更多獨立新資料，或重新設計不依賴 OOS 選參數的驗證方式。
- 線上策略維持 L15/S15，不修改 `strategy.py` 或 `.env`。

稽核腳本：[v32_l13_s16_10gate_audit.py](../backtest/research/v32_l13_s16_10gate_audit.py)
