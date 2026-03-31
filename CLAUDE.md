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

---

## 2026/03/31 Code Review 修復紀錄

### 修復 1（嚴重）：Max 3 持倉限制與回測不一致

**問題**：`strategy_runner.py` 用 `_entry_count` 計數器追蹤開倉數量，但只在「所有同方向持倉全部消失」時才重置。如果持有 3 多單、平了 1 單剩 2 單，計數器仍為 3，導致無法再開新多單。回測中是用「目前持倉數 < 3」判斷，邏輯不同。

**修復**：移除 `_entry_count` 計數器，改為每次用 `get_positions()` 查詢實際持倉數量，與回測行為完全一致。

**影響檔案**：`strategy_runner.py` — `check_signals()`, `execute_signal()`, `send_heartbeat()`

### 修復 2（嚴重）：TP1 後 SL 保本更新無重試

**問題**：`cryptobot_monitor.py` 在 TP1 觸發後呼叫 `update_stop_loss()` 將 SL 移到進場價（保本），但若 API 呼叫失敗（網路中斷等），SL 不會更新，剩餘 90% 倉位暴露在原始止損下，可能導致比預期更大的虧損。

**修復**：新增重試機制（最多 3 次，間隔 1 秒），失敗時發送 Telegram 警報提醒手動檢查。

**影響檔案**：`cryptobot_monitor.py` — `monitor_position()` Phase 1 段

### 修復 3（中等）：Phase 2 Trail SL 只升不降

**問題**：原本的 trail SL 每次都會呼叫 `update_stop_loss()`，即使新的 trail SL 比上次更差（多單更低、空單更高）。這會導致不必要的 API 呼叫，且理論上 trail SL 應該只往有利方向移動。

**修復**：在 state 中記錄 `last_trail_sl`，只在新的 trail SL 更優（多單更高、空單更低）時才更新。同時加入 try/except 防止 API 錯誤中斷整個監控流程。

**影響檔案**：`cryptobot_monitor.py` — `monitor_position()` Phase 2 段

### 修復 4（輕微）：bad import practice

**問題**：`strategy_runner.py` 在函數內部 `import time as _t`，不規範。

**修復**：移除，直接使用頂部已匯入的 `time` 模組。

### 修復 5（嚴重）：同方向多倉的 monitor state 互相覆蓋

**問題**：`cryptobot_monitor.py` 用 `{symbol}_{side}`（如 `BTCUSDT_long`）作為 state key。但 Max 3 允許同方向開 3 單，3 個多單共用同一個 key，TP1 target、trail_hi、entry_price 全被最後一單覆蓋，前面的單子失去追蹤。

**修復**：state key 改為 `{symbol}_{side}_{entry_price}`（如 `BTCUSDT_long_83500.00`），每個倉位獨立追蹤。同步修改了 stale state 偵測和 exit log 的 side 解析。

**影響檔案**：`cryptobot_monitor.py` — `monitor_all()`, `_log_position_exit()`

### 修復 6（中等）：Monitor 用未收盤 bar 的指標

**問題**：`get_5m_indicators()` 用 `iloc[-1]`（當前正在發展的 bar），而 `strategy_runner` 用 `iloc[-2]`（已收盤 bar）。兩者不一致，且 monitor 的 RSI/ATR 會在 5 分鐘內不斷變化。

**修復**：改為 `iloc[-2]`，與 runner 一致，確保用的是已確認的指標值。

**影響檔案**：`cryptobot_monitor.py` — `get_5m_indicators()`

### 修復 7（輕微）：mark_price 取得失敗無重試

**問題**：`place_order()` 取 mark_price 計算倉位大小時，API 失敗直接 return None，交易被跳過。

**修復**：加 3 次重試機制。

**影響檔案**：`binance_trade.py` — `place_order()`

### 優化 1：K 線快取（減少 API 呼叫）

**問題**：Runner 和 Monitor 每 5 分鐘各自抓一次 200 根 5m K 線，資料完全相同。

**優化**：在 `data_fetcher.py` 加入 60 秒記憶體快取，同一分鐘內第二次呼叫直接回傳快取。API 呼叫量減半。

### 優化 2：Telegram 通知加重試

**問題**：TG 發送失敗就靜默丟棄，重要的 SL 警報可能遺失。

**優化**：加入 3 次重試 + rate limit (429) 處理。

### 優化 3：trade_journal 加 thread lock

**問題**：Runner 和 Monitor 是兩個 thread，同時讀寫 CSV 會資料損壞。

**優化**：加 `threading.Lock()`，所有 CSV 讀寫都在鎖內完成。

### 優化 4：啟動時偵測孤兒持倉

**問題**：程式崩潰重啟後，已開的倉位不會被通知。

**優化**：`strategy_runner.main()` 啟動時查詢既有持倉，發送 TG 通知。Monitor 的 `load_state()` 已能自動恢復 Phase 追蹤。

### 優化 5：Binance session 自動重建

**問題**：長時間運行後 session 可能過期，API 呼叫失敗才重建。

**優化**：每 30 分鐘自動重建 session + 重新同步時間。

### 已知限制（待後續改善）

- **無每日最大虧損停機**：如果策略連續虧損，不會自動停止交易
- **PnL 記錄用 mark_price**：journal 的 exit_price 用最後一次檢查的 mark price，非實際成交價
- **手續費差異**：回測假設 0.04% maker，但 market order 實際是 taker 費率（可能 0.04%~0.05%）
- **同方向多倉共用 SL**：Binance `closePosition=true` 只允許同 symbol 同方向一個 SL，3 個多單共用最新那個 trail SL，可能不適合較早進場的倉位。需要改成每個倉位用固定數量的 SL 才能獨立管理
