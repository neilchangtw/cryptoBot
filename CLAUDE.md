# CryptoBot 專案說明

BTC 自動交易機器人，使用 Binance Futures API（支援 Testnet / 正式環境）。

---

## 資料夾結構

```
cryptoBot/
├── main.py                # 主程式入口（同時啟動 Runner + Monitor）
├── strategy_runner.py     # v5 策略信號引擎（5m RSI+BB + 3 過濾，每 5 分鐘掃描）
├── cryptobot_monitor.py   # 持倉監控（TP1 全平 + 8h 時間止損）
├── binance_trade.py       # Binance Futures 下單模組（市價單、Algo SL、持倉查詢）
├── trade_journal.py       # 交易日誌模組（SQLite 記錄完整交易生命週期）
├── telegram_notify.py     # Telegram 推播（共用模組 + v5 格式 helper）
├── requirements.txt       # Python 依賴
├── backtest/              # 回測系統
│   ├── data_fetcher.py    # 從 Binance 公開 API 抓 K線，自動快取 + 60s 記憶體快取
│   ├── strategy_engine.py # 技術指標計算
│   ├── backtest.py        # 回測引擎 + Walk-Forward 驗證
│   └── results/           # 回測輸出
├── data/                  # K線快取（CSV，不進 git）
├── .env                   # API 金鑰（不進 git）
├── monitor_state.json     # 持倉監控狀態（自動產生，不進 git）
└── trade_journal.db       # 交易日誌 SQLite（自動產生，不進 git）
```

---

## 運行方式

```bash
# 同時啟動 Runner + Monitor（推薦）
python main.py

# 或分開啟動：
# 終端 1：信號偵測 + 開倉
python strategy_runner.py

# 終端 2：持倉管理（TP1 全平 + 時間止損）
python cryptobot_monitor.py
```

`binance_trade.py`、`trade_journal.py` 是被 import 的模組，不需單獨啟動。

---

## 系統架構

### 執行緒模型

```
main.py
├── Thread "Runner" → strategy_runner.main()   # 信號偵測 + 開倉
└── Thread "Monitor" → cryptobot_monitor.main() # 持倉監控 + 出場
```

- 兩個 daemon thread，主執行緒 `t1.join()` 等待，Ctrl+C 中斷
- Runner 和 Monitor **不直接通訊**，透過以下共享資源協作：
  - `trade_journal.db`（SQLite WAL mode）：Runner 寫入進場，Monitor 讀取匹配 + 寫入出場
  - `Binance API`（持倉查詢）：Monitor 偵測倉位變化
  - `monitor_state.json`：Monitor 自己的狀態持久化

### Testnet / 正式環境切換

由 `.env` 的 `BINANCE_TESTNET` 控制（預設 `true`）：

| 模式 | API Base URL | K 線 URL |
|------|-------------|---------|
| Testnet | `https://testnet.binancefuture.com` | `https://testnet.binancefuture.com/fapi/v1/klines` |
| Production | `https://fapi.binance.com` | `https://fapi.binance.com/fapi/v1/klines` |

啟動時 console 會顯示 `MODE: TESTNET` 或 `MODE: PRODUCTION`。

### Session 管理（binance_trade.py）

- Binance API session 每 **30 分鐘自動重建**（`_SESSION_MAX_AGE = 1800`）
- 每次 API 呼叫前檢查 session 年齡（`_ensure_session()`）
- 重建時同步 Binance 伺服器時間（`sync_time()`），修正本地時鐘偏移
- API 錯誤時也會觸發 session 重建（如 `get_symbol_info()`、`get_available_balance()` 等）

### 下單精度處理（binance_trade.py）

- **價格精度**：`round_to_tick(price, tick_size)` — 四捨五入到最近的 tick_size（如 BTCUSDT tick=0.10）
- **數量精度**：`round_to_lot(qty, qty_step, min_qty)` — 四捨五入到最近的 step_size，確保 ≥ min_qty
- Symbol info 從 `exchangeInfo` API 取得，快取在 `_symbol_info_cache`
- Fallback 值（API 失敗時）：tick_size=0.10, qty_step=0.001, min_qty=0.001

### 冷卻機制（binance_trade.py）

- **只對開倉單生效**（`reduce_only=False`）
- Key：`(strategy_id, symbol)`，如 `("v5", "BTCUSDT")`
- 同一 key 在 `COOLDOWN_SECONDS`（預設 60s）內不允許重複開倉
- TP1/TimeStop 平倉單（`reduce_only=True`）不受冷卻限制

### 錯誤處理與重試

| 場景 | 重試策略 | 位置 |
|------|---------|------|
| Mark Price 取得 | 3 次，間隔 1s | binance_trade.py |
| Telegram 發送 | 3 次，429 rate limit 等 2s | telegram_notify.py |
| API Session 錯誤 | 重建 session 後重試 | binance_trade.py 各函式 |
| Runner/Monitor 主迴圈異常 | catch all → TG 警報 → 繼續下一輪 | strategy_runner.py, cryptobot_monitor.py |

### 心跳與摘要

- **Runner**：每 3600 秒（1 小時）印出餘額、持倉數、掃描/信號次數
- **Monitor**：每 3600 秒發送 TG 摘要（餘額、PnL、持倉數、TimeStop 倒數）

---

## 交易策略 v5（5m RSI+BB 均值回歸 + 3 進場過濾）

> v5 基於 btc-strategy-research 的回測優化（Phase 8），從 v4 改進：
> - 新增 3 個進場過濾器（ATR 百分位、EMA21 偏離、1h RSI 轉向）
> - SL 從結構止損改為安全網 ±3%（限價出場，0 滑價）
> - TP1 從 10% 部分平倉改為 100% 全平（Phase 2 Trail 被證明是負價值）
> - 新增 8h 時間止損（認錯出場）
> - 最大同向持倉從 3 改為 2

### 做多策略

| 項目 | 條件 |
|------|------|
| **5m 進場** | RSI(14) < 30 **AND** Close < BB Lower(20,2) |
| **過濾 1** | ATR 百分位 ≤ 75（排除高波動） |
| **過濾 2** | \|price_vs_ema21\| < 2%（不偏離均線太遠） |
| **過濾 3** | 1h RSI(curr) >= 1h RSI(prev)（下跌趨勢已停止） |
| **安全網 SL** | entry × 0.97（-3%，STOP_MARKET，只防極端） |
| **TP1（100%）** | 進場價 + 1.5 × ATR → 全平 100% |
| **時間止損** | 8h（96 根 5m bar）未到 TP1 → 全平認錯 |
| **同方向限制** | 最多同時 2 單 |

### 做空策略（鏡像）

| 項目 | 條件 |
|------|------|
| **5m 進場** | RSI(14) > 70 **AND** Close > BB Upper(20,2) |
| **過濾 1** | ATR 百分位 ≤ 75 |
| **過濾 2** | \|price_vs_ema21\| < 2% |
| **過濾 3** | 1h RSI(curr) <= 1h RSI(prev)（上漲趨勢已停止） |
| **安全網 SL** | entry × 1.03（+3%） |
| **TP1（100%）** | 進場價 − 1.5 × ATR → 全平 100% |
| **時間止損** | 同上 |
| **同方向限制** | 最多同時 2 單 |

### 技術指標

| 指標 | 公式 | 用途 |
|------|------|------|
| RSI(14) | Wilder's smoothing: `ewm(alpha=1/14)` | 進場條件 |
| BB(20,2) | Mid=SMA(20), Upper/Lower=Mid ± 2×Std(20) | 進場條件 |
| ATR(14) | True Range rolling(14).mean() | TP1 距離計算 |
| ATR Percentile | ATR 在最近 100 根 K 線的百分位排名 | 進場過濾 |
| EMA21 | `close.ewm(span=21).mean()` | 進場過濾 |
| 1h RSI(14) | 同 RSI 公式，用 1h K 線 | 進場過濾（趨勢方向） |

### 資料來源與指標計算流程

所有技術指標**不是直接從 API 取得**，而是：
1. 從 Binance API 取得原始 K 線數據（OHLCV）
2. 在本地用 pandas 計算所有指標

| 資料來源 | API 呼叫 | 本地計算的指標 |
|---------|---------|--------------|
| **5m K 線** | `fetch_latest_klines(symbol, "5m", limit=120)` | RSI(14)、BB(20,2)、ATR(14)、ATR 百分位、EMA21、price vs EMA21 |
| **1h K 線** | `fetch_latest_klines(symbol, "1h", limit=30)` | 1h RSI(14) curr & prev |
| **Mark Price** | `get_mark_price()` | —（monitor 用來判斷 TP1 / 時間止損） |

**執行順序優化**：
- 每次掃描先 call 5m API → 本地算 6 個指標 → 檢查條件 1~4
- **只有 1~4 都通過**才 call 1h API → 算 1h RSI → 檢查條件 5（省不必要的 API 呼叫）
- K 線有 **60 秒記憶體快取**（`data_fetcher.py`），同一分鐘內重複請求不會重新 call API
- 所有指標使用 `iloc[-2]`（最後一根已收盤 bar），不使用未收盤的當前 bar

### 共同設定

| 參數 | 值 |
|------|----|
| 每單保證金 | 100 USDT |
| 槓桿 | 20x（名義倉位 2,000 USDT） |
| 手續費 | 0.04%（maker） |
| 掃描間隔 | 5 分鐘 |
| 交易品種 | BTCUSDT Perpetual |
| 安全網 SL | ±3% |
| TP1 距離 | 1.5 × ATR |
| 時間止損 | 8h（96 bars） |

---

## 持倉管理流程

```
開倉 → 等待 TP1 或 時間止損 → 出場

TP1（mark_price 達 entry ± 1.5×ATR）：
  → 全平 100% 倉位（市價單，reduce_only=True）
  → 設 closing_initiated = True
  → 記錄出場 exit_reason="tp1"

時間止損（持倉超過 8h / 480 min）：
  → 全平 100% 倉位（市價單，reduce_only=True）
  → 設 closing_initiated = True
  → 記錄出場 exit_reason="time_stop"

安全網 SL（交易所 STOP_MARKET ±3%）：
  → 被動觸發（極端行情才會到）
  → Monitor 偵測倉位消失 + closing_initiated=False → 判定為安全網
  → 記錄出場 exit_reason="safenet"

無 Phase 2：回測證明 Phase 2 Trail 是負價值
  （利潤回吐 > 額外收益，100% 全平在所有距離都更優）
```

### 持倉計數機制

- **不使用 Binance API 計數**：Binance 會合併同方向倉位為一筆，`get_positions()` 最多回傳 1 long + 1 short
- **使用 Journal 計數**：`get_open_trades(side="long/short")` 查詢 `exit_time IS NULL` 的交易筆數
- Runner 用此計數來限制 `MAX_SAME_DIRECTION`（預設 2）

### 孤兒持倉偵測

- Runner 啟動時呼叫 `get_positions()` 檢查是否有既有持倉
- 若有，發送 TG 通知「Monitor 會自動接管管理」
- Monitor 下一輪掃描時自動偵測並建立 state

### monitor_state.json 結構

每個持倉的 state key 格式：`{symbol}_{side}_{entry_price:.2f}`

```json
{
  "BTCUSDT_long_68690.78": {
    "tp1_target": 68840.78,
    "initial_atr": 100.0,
    "entry_price": 68690.78,
    "trade_id": "20260402_001200_long",
    "closing_initiated": false,
    "entry_time": "2026-04-02 00:12:00",
    "qty": 0.0291,
    "last_mark": 68700.00
  }
}
```

| 欄位 | 用途 |
|------|------|
| `tp1_target` | TP1 目標價（entry ± 1.5×ATR） |
| `initial_atr` | 進場時的 ATR 值 |
| `entry_price` | 進場價格 |
| `trade_id` | 對應 journal 的 trade_id |
| `closing_initiated` | 是否已由 Monitor 主動平倉（防重複記錄） |
| `entry_time` | 進場時間（用於計算時間止損） |
| `qty` | 該筆交易數量（用於 reduce_only 平倉） |
| `last_mark` | 最後一次檢查的 mark price |

State 初始化時從 journal 讀取 trade_id、entry_time、qty（`find_trade_id_by_position`）。

### Trade ID 格式與匹配

- **格式**：`{YYYYMMDD}_{HHMMSS}_{side}`，如 `20260402_001200_long`
- **匹配邏輯**（`find_trade_id_by_position`）：
  1. 查詢同 side 且 `exit_time IS NULL` 的交易
  2. 若只有 1 筆：直接使用
  3. 若多筆：以 entry_price 最接近的為準
  4. Fallback：使用最後一筆（最新）

---

## 回測結果 v5（嚴格版，含滑價+限價出場）

```
V5 最終版（1.5x ATR / 100% 全平 / 8h TimeStop / Max 2）：
  全樣本：PnL +$243，PF 1.48，SafeNet 2 次
  OOS：PnL +$249，WR 90.1%，PF 2.03，DD -$113，SafeNet 2 次
  OOS/Full = 102%（非過擬合）
  月報酬：~$83/月（8.3%/月 on $1,000）
```

> 實盤預估回測績效打 3~5 折。

### 版本歷程

| | v4（前版） | v5（現行） |
|--|-----------|-----------|
| 進場過濾 | 無 | ATR≤75 + EMA21<2% + 1h RSI 轉向 |
| SL | 結構止損 Swing±0.3×ATR | 安全網 ±3% |
| TP1 距離 | 1.0x ATR | 1.5x ATR |
| TP1 比例 | 10% | 100%（全平） |
| Phase 2 | 自適應 ATR+RSI Trail | 無（已移除） |
| 時間止損 | 無 | 8h |
| 最大同向 | 3 | 2 |

---

## 交易日誌（trade_journal.db — SQLite）

每筆交易從進場到出場記錄完整生命週期，用於與回測數據對比分析。

### SQLite 並行安全

- **WAL 模式**（Write-Ahead Logging）：允許 Runner 和 Monitor 同時讀寫
- **Connection timeout**：10 秒（`sqlite3.connect(timeout=10)`）
- **Busy timeout**：5 秒（`PRAGMA busy_timeout=5000`）
- 每次操作建立新 connection（不用 connection pool），避免跨執行緒鎖死
- **Schema 遷移**：`_migrate_columns()` 用 `PRAGMA table_info` 檢查 + `ALTER TABLE ADD COLUMN`，向下相容 v4 資料

### 記錄時機

| 事件 | 記錄者 | 寫入欄位 |
|------|--------|---------|
| 開倉 | strategy_runner | entry_time, side, entry_price, qty, rsi, atr, atr_pctile, bb, safenet_sl, tp1, ema21_dev, rsi_1h, time_stop_deadline |
| 出場 | monitor | exit_time, exit_price, exit_reason, pnl_pct, duration_min, bars_held, rsi_exit |

### 欄位說明

```
# 基本
trade_id, entry_time, exit_time, side, entry_price, exit_price, qty, margin

# 進場指標
rsi_entry, atr_entry, atr_pctile_entry, bb_lower, bb_upper

# v4 相容（舊資料保留）
structural_sl, swing_level

# v5 新增
safenet_sl, tp1_target, ema21_deviation, rsi_1h_entry, rsi_1h_prev, time_stop_deadline

# 出場
exit_reason (tp1 / time_stop / safenet), realized_pnl, pnl_pct,
duration_min, bars_held, rsi_exit, atr_pctile_exit
```

### 分析範例

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("trade_journal.db")
live = pd.read_sql("SELECT * FROM trades WHERE exit_time IS NOT NULL", conn)
print(f"勝率: {(live['pnl_pct'] > 0).mean():.1%}")
print(f"平均持倉: {live['duration_min'].mean():.0f} min")
print(f"出場分布: {live['exit_reason'].value_counts().to_dict()}")
```

---

## Binance API 注意事項

### 使用的 API 端點

| 端點 | 模組 | 用途 |
|------|------|------|
| `GET /fapi/v1/exchangeInfo` | binance_trade | 取 tick_size / step_size / min_qty |
| `GET /fapi/v1/account` | binance_trade | 查詢 USDT 可用餘額 |
| `GET /fapi/v1/positionRisk` | binance_trade | 取得持倉（side, size, entry, mark price） |
| `POST /fapi/v1/changeInitialLeverage` | binance_trade | 設定槓桿倍數 |
| `POST /fapi/v1/order` | binance_trade | 市價單（BUY/SELL） |
| `DELETE /fapi/v1/allOpenOrders` | binance_trade | 取消所有掛單 |
| `POST /fapi/v1/algoOrder` | binance_trade | 條件單（STOP_MARKET 安全網 SL） |
| `GET /fapi/v1/openAlgoOrders` | binance_trade | 查詢 Algo 掛單 |
| `DELETE /fapi/v1/algoOrder` | binance_trade | 取消 Algo 單（by algoId） |
| `GET /fapi/v1/markPrice` | binance_trade | 取得即時 mark price |
| `GET /fapi/v1/time` | binance_trade | 伺服器時間同步 |
| `GET /fapi/v1/klines` | data_fetcher | Futures K 線數據 |

### Algo Order API

- 2025/12 起，Binance 將 STOP_MARKET / TAKE_PROFIT_MARKET 遷移至 Algo Order API
- 參數：`algoType=CONDITIONAL` + `triggerPrice` + `side` + `symbol`
- 舊端點 `/fapi/v1/order` 對這些 order type 回傳 `-4120`

### closePosition 行為

- 安全網 SL 用 `closePosition=true`，會關閉該方向**所有**倉位
- Max 2 時兩個同向倉位共用一個安全網 SL
- TP1/TimeStop 用 `qty=trade_qty, reduce_only=True`，只平該筆特定數量

---

## .env 格式

```ini
# Telegram
TELEGRAM_BOT_TOKEN=<token>          # 必填
TELEGRAM_CHAT_ID=<chat_id>          # 必填

# Binance Futures
BINANCE_API_KEY=<key>               # 必填
BINANCE_API_SECRET=<secret>         # 必填
BINANCE_TESTNET=true                # true=testnet（預設）, false=正式

# 交易參數
SYMBOL=BTCUSDT                      # 預設 BTCUSDT
MARGIN_PER_TRADE=100                # 預設 100 USDT
LEVERAGE=20                         # 預設 20x
MAX_SAME_DIRECTION=2                # 預設 2（程式碼 fallback）
COOLDOWN_SECONDS=60                 # 預設 60 秒
CHECK_INTERVAL=300                  # 預設 300 秒（5 分鐘）
```

所有交易參數都有程式碼內建預設值，`.env` 未設定時使用預設。

---

## 開發進度

- [x] Phase 1：回測 + Walk-Forward 驗證（v3 多時間框架策略）
- [x] Phase 2-1：binance_trade.py — Binance Futures 下單模組
- [x] Phase 2-2：strategy_runner.py — v3 → v4 策略信號引擎
- [x] Phase 2-3：cryptobot_monitor.py — 持倉監控 + 自適應移動止損
- [x] Phase 2-4：Binance Testnet 整合測試（開單 + SL + 平倉 + 日誌）
- [x] Phase 2-5：trade_journal.py — 交易日誌（回測對比用）
- [x] Phase 2-6：v4 → v5 升級（3 過濾器 + 安全網 SL + TP1 全平 + 8h 時間止損）
- [x] Phase 2-7：v5 Testnet 下單驗證（開單 + 安全網 SL + 全平 + 清理，全部通過）
- [ ] Phase 3：Testnet 24h 完整驗證 v5
- [ ] Phase 4：串幣安正式下單

---

## Testnet 驗證紀錄

### v5 Testnet 測試（2026-04-02）

**測試前狀態**：
- 起始餘額：**4,915.67 USDT**
- Journal / State：已清空
- 預計啟動時間：12:20

**下單驗證結果（2026-04-02 00:12）**：
| 操作 | 結果 |
|------|------|
| 市價開多 | BUY 0.0291 @ 68690.78 |
| 安全網 SL 掛單 | algoId=39853935, trigger=66563.70 (-3%) |
| SL 取消舊單 | 成功 |
| TP1 全平 (reduce_only) | SELL 0.0291 成功 |
| 持倉歸零確認 | 0 positions |
| Algo Orders 清理 | 0 remaining |

---

## Telegram 通知類型

| 通知 | 觸發時機 |
|------|---------|
| **進場信號** | 偵測到信號：價格、RSI、安全網SL、TP1、ATR pctile、EMA21偏離、1h RSI、時間止損到期 |
| **開倉通知** | 下單成功：數量、價格、名義金額、保證金 |
| **TP1 全平** | 達到 TP1 目標：進出場價、盈虧%、持倉時間 |
| **時間止損** | 8h 未達 TP1：進出場價、盈虧%、認錯出場 |
| **安全網觸發** | 交易所 SL 被觸發：進場價、近似出場價、盈虧%、警告 |
| **心跳摘要** | 每小時：餘額、持倉數(x/2)、最近 TimeStop 倒數、掃描/信號次數 |
| **異常通知** | 程式錯誤時推送錯誤訊息 |

---

## 注意事項

- `.env` 含 API 金鑰，已加入 `.gitignore`
- `monitor_state.json` 儲存持倉監控狀態（TP1 目標、entry_time、trade_id），程式重啟後自動恢復
- `trade_journal.db` 記錄所有交易進出場，可用 SQL/pandas 分析與回測對比
- 實盤預估回測績效打 3~5 折
- v4 → v5 升級前須清除 `monitor_state.json`（schema 不同）

---

## 歷史修復紀錄（v4 時期，2026/03/31）

> 以下修復已整合到 v5 程式碼中，不再需要額外處理。

1. **Max 持倉限制**：移除 `_entry_count` 計數器，改用 journal `get_open_trades()` 即時查詢
2. **TP1 SL 保本重試**：v5 不再需要（TP1 = 全平，無保本動作）
3. **Phase 2 Trail SL 只升不降**：v5 不再需要（無 Phase 2）
4. **同方向多倉 state 覆蓋**：state key = `{symbol}_{side}_{entry_price}`（已保留）
5. **Monitor 用 iloc[-2]**：已保留（使用已收盤 bar）
6. **mark_price 重試**：已保留
7. **K 線快取**：已保留（60s TTL）
8. **TG 重試**：已保留（3 次 + rate limit）
9. **啟動偵測孤兒持倉**：已保留
10. **Session 自動重建**：已保留（30 分鐘）

### 已知限制

- **無每日最大虧損停機**：如果策略連續虧損，不會自動停止交易
- **PnL 記錄用 mark_price**：journal 的 exit_price 用最後一次檢查的 mark price，非實際成交價
- **手續費差異**：回測假設 0.04% maker，但 market order 實際是 taker 費率（可能 0.04%~0.05%）
- **同方向多倉共用安全網 SL**：Binance `closePosition=true` 只允許同 symbol 同方向一個 SL，2 個多單共用同一個安全網 SL
