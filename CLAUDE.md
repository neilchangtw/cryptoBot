# CryptoBot 專案說明

BTC 自動交易機器人，使用 Binance Futures API（支援 Testnet / 正式環境）。

---

## 資料夾結構

```
cryptoBot/
├── main.py                # 主程式入口（同時啟動 Runner + Monitor）
├── strategy_runner.py     # v4 策略信號引擎（5m RSI+BB 均值回歸，每 5 分鐘掃描）
├── cryptobot_monitor.py   # 持倉監控（TP1 部分平倉 + 保本 + 自適應移動止損）
├── binance_trade.py       # Binance Futures 下單模組（市價單、Algo SL、持倉查詢）
├── trade_journal.py       # 交易日誌模組（CSV 記錄完整交易生命週期）
├── telegram_notify.py     # Telegram 推播（共用模組）
├── requirements.txt       # Python 依賴
├── backtest/              # 回測系統
│   ├── data_fetcher.py    # 從 Binance 公開 API 抓 K線，自動快取
│   ├── strategy_engine.py # 技術指標計算（v1 + v2 擴充指標集）
│   ├── backtest.py        # 1h 回測引擎 + Walk-Forward 驗證
│   ├── research_v2.py     # 1h 策略研究（14 信號 × 4 SL × 5 出場）
│   ├── research_v3.py     # v3 多時間框架研究（1h 過濾 + 5m 進場）
│   ├── validate_v3.py     # v3 回測結果驗證（7 項檢查全通過）
│   └── results/           # 回測輸出（trades.csv, metrics.csv）
├── dashboard/             # 回測視覺化（Streamlit）
│   ├── app.py             # Dashboard 主程式
│   ├── charts.py          # 圖表繪製
│   ├── data_loader.py     # 資料讀取
│   └── metrics.py         # 指標計算
├── data/                  # K線快取（CSV，不進 git）
├── doc/                   # 研究文件
├── .env                   # API 金鑰（不進 git）
├── monitor_state.json     # 持倉監控狀態（自動產生，不進 git）
└── trade_journal.csv      # 交易日誌輸出（自動產生，不進 git）
```

---

## 運行方式

```bash
# 同時啟動 Runner + Monitor（推薦）
python main.py

# 或分開啟動：
# 終端 1：信號偵測 + 開倉
python strategy_runner.py

# 終端 2：持倉管理（TP1 + 自適應移動止損）
python cryptobot_monitor.py
```

`binance_trade.py`、`trade_journal.py` 是被 import 的模組，不需單獨啟動。

---

## 交易策略 v4（Strategy H：5m RSI+BB 均值回歸）

> v4 基於 btc-strategy-research 專案的回測研究，從 v3 的趨勢跟蹤改為均值回歸策略。
> 研究測試了 20+ 種進場信號、9 種止損、15 種出場、6 個時間框架，Strategy H 為最終選定。

### 做多策略

| 項目 | 條件 |
|------|------|
| **5m 進場** | RSI(14) < 30 **AND** Close < BB Lower(20,2)（雙重確認超賣） |
| **止損** | 結構止損：Swing Low(5) − 0.3 × ATR(14)（防掃針緩衝） |
| **TP1（10%）** | 進場價 + 1.0 × ATR → 平 10% + SL 移至進場價（保本） |
| **移動止損** | 自適應 ATR+RSI Trail（見下方公式） |
| **同方向限制** | 最多同時 3 單 |

### 做空策略

| 項目 | 條件 |
|------|------|
| **5m 進場** | RSI(14) > 70 **AND** Close > BB Upper(20,2)（雙重確認超買） |
| **止損** | 結構止損：Swing High(5) + 0.3 × ATR(14)（防掃針緩衝） |
| **TP1（10%）** | 進場價 − 1.0 × ATR → 平 10% + SL 移至進場價（保本） |
| **移動止損** | 自適應 ATR+RSI Trail（見下方公式） |
| **同方向限制** | 最多同時 3 單 |

### 自適應 ATR+RSI 移動止損（Phase 2）

```
base_mult = 1.0 + (atr_pctile / 100) × 1.5
  → 低波動 (pctile=0):  1.0x（收緊鎖利）
  → 中波動 (pctile=50): 1.75x
  → 高波動 (pctile=100): 2.5x（放鬆避震出）

RSI 加速：
  做多 & RSI > 65: mult × 0.6（收緊 40%，準備出場）
  做空 & RSI < 35: mult × 0.6（收緊 40%，準備出場）

做多 trail_sl = max(trail_high − ATR × mult, entry_price)
做空 trail_sl = min(trail_low  + ATR × mult, entry_price)
（最低為保本，不會虧損）
```

### 5m 指標計算

| 指標 | 公式 |
|------|------|
| RSI(14) | Wilder's smoothing: `ewm(alpha=1/14)` |
| BB(20,2) | Mid=SMA(20), Upper/Lower=Mid ± 2×Std(20) |
| ATR(14) | True Range rolling(14).mean() |
| ATR Percentile | ATR 在最近 100 根 K 線的百分位排名 |
| Swing High/Low(5) | 左右各 5 根確認的擺點，ffill 避免 look-ahead |

### 共同設定

| 參數 | 值 |
|------|----|
| 每單保證金 | 100 USDT |
| 槓桿 | 20x（名義倉位 2,000 USDT） |
| 手續費 | 0.04%（maker） |
| 掃描間隔 | 5 分鐘 |
| 交易品種 | BTCUSDT Perpetual |

---

## 持倉管理流程

```
開倉 → Phase 1（等待 TP1）→ Phase 2（自適應 Trail）→ 出場

Phase 1：
  - 監控 mark_price 是否達到 TP1 目標
  - 觸發時：平 10% 倉位 + SL 移至保本 + 切換 Phase 2

Phase 2：
  - 每 5 分鐘重算 adaptive trail_sl
  - 更新交易所 STOP_MARKET (Algo Order)
  - 當 trail_sl 被觸發 → 平倉剩餘 90%
```

---

## 回測結果 v4 Strategy H（Walk-Forward，2025/10 ~ 2026/03）

| 項目 | 數值 |
|------|------|
| 全期間 PnL | +$16,499 |
| 總交易筆數 | 2,185 |
| 勝率 | 84.8% |
| Profit Factor | 35.29 |
| 最大回撤 | -$54 (5.4%) |
| 日均交易 | ~12 筆 |

| 驗證 | 結果 |
|------|------|
| OOS（2 個月） | +$6,841 / WR 87.4% / PF 34.35 |
| OOS 保留率 | 142%（比 IS 更好） |
| Rolling WF（4 fold） | 4/4 全正收益 / 合計 +$11,863 |
| 月度 | 6/6 月全正收益 |

> 實盤預估回測績效打 3~5 折。

### vs v3 改進

| 項目 | v3 | v4 | 改善 |
|------|----|----|------|
| 進場 | EMA穿越+量能（趨勢跟蹤） | RSI+BB（均值回歸） | 勝率 +30% |
| 止損 | 固定 ATR 倍數 | 結構止損 Swing±0.3×ATR | PF +27x |
| 出場 | 靜態 ATR/EMA9 trail | 自適應 ATR+RSI trail | 適應市場波動 |
| 時間框架 | 1h 過濾 + 5m | 純 5m | 捕捉更多機會 |
| PnL | +$935 | +$16,499 | +1,664% |

---

## 交易日誌（trade_journal.csv）

每筆交易從進場到出場記錄完整生命週期，用於與回測數據對比分析。

### 記錄時機

| 事件 | 記錄者 | 寫入欄位 |
|------|--------|---------|
| 開倉 | strategy_runner | entry_time, side, entry_price, qty, rsi, atr, atr_pctile, bb, sl, tp1, swing |
| TP1 | monitor | tp1_hit, tp1_time, tp1_price |
| 出場 | monitor | exit_time, exit_price, exit_reason, pnl_pct, duration, rsi_exit |

### CSV 欄位

```
trade_id, entry_time, exit_time, side, entry_price, exit_price,
qty, margin, rsi_entry, atr_entry, atr_pctile_entry, bb_lower, bb_upper,
structural_sl, tp1_target, swing_level,
tp1_hit, tp1_time, tp1_price,
exit_reason, realized_pnl, pnl_pct, duration_min, rsi_exit, atr_pctile_exit
```

### 分析範例

```python
import pandas as pd
live = pd.read_csv("trade_journal.csv")
print(f"勝率: {(live['pnl_pct'] > 0).mean():.1%}")
print(f"平均持倉: {live['duration_min'].mean():.0f} min")
print(f"平均 PnL%: {live['pnl_pct'].mean():.2f}%")
```

---

## Binance API 注意事項

- **Algo Order API**：2025/12 起，Binance 將 STOP_MARKET / TAKE_PROFIT_MARKET 等條件單遷移至 Algo Order API
  - 下單：`POST /fapi/v1/algoOrder` + `algoType=CONDITIONAL` + `triggerPrice`
  - 查詢：`GET /fapi/v1/openAlgoOrders`
  - 取消：`DELETE /fapi/v1/algoOrder` + `algoId`
  - 舊端點 `/fapi/v1/order` 對這些 order type 回傳 `-4120`

---

## .env 格式

```ini
# Telegram
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>

# Binance Futures
BINANCE_API_KEY=<key>
BINANCE_API_SECRET=<secret>
BINANCE_TESTNET=true          # true=testnet, false=正式

# 交易參數
SYMBOL=BTCUSDT
MARGIN_PER_TRADE=100
LEVERAGE=20
MAX_SAME_DIRECTION=3
COOLDOWN_SECONDS=60
CHECK_INTERVAL=300
```

---

## 開發進度

- [x] Phase 1：回測 + Walk-Forward 驗證（v3 多時間框架策略）
- [x] Phase 2-1：binance_trade.py — Binance Futures 下單模組
- [x] Phase 2-2：strategy_runner.py — v3 → v4 策略信號引擎
- [x] Phase 2-3：cryptobot_monitor.py — 持倉監控 + 自適應移動止損
- [x] Phase 2-4：Binance Testnet 整合測試（開單 + SL + 平倉 + 日誌）
- [x] Phase 2-5：trade_journal.py — 交易日誌（回測對比用）
- [ ] Phase 3：串幣安正式下單

---

## Telegram 通知類型

| 通知 | 觸發時機 |
|------|---------|
| **信號通知** | 偵測到進場信號，顯示價格、RSI、SL、TP1、R:R |
| **開倉通知** | 下單成功，顯示數量、價格、名義金額、保證金 |
| **TP1 通知** | 觸發部分止盈，顯示盈虧%、平倉數量、保本 SL、切換 Phase 2 |
| **出場通知** | 持倉關閉，顯示出場原因（SL / Trail）、進出場價、盈虧% |
| **心跳摘要** | 每小時，顯示餘額、持倉、未實現盈虧、掃描次數 |
| **異常通知** | 程式錯誤時推送錯誤訊息 |

---

## 注意事項

- `.env` 含 API 金鑰，已加入 `.gitignore`
- `monitor_state.json` 儲存持倉監控狀態（phase、TP1 目標、trail 極值、trade_id），程式重啟後自動恢復
- `trade_journal.csv` 記錄所有交易進出場，可用 pandas 分析與回測對比
- 實盤預估回測績效打 3~5 折
- Binance Algo Order API 變更（2025/12）：止損單需用 `/fapi/v1/algoOrder` 端點
