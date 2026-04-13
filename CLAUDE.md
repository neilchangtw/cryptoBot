# CryptoBot

ETH 1h Garman-Klass Compression-Breakout 自動交易機器人（Binance Futures）。
雙策略 L+S，Binance Hedge Mode（雙向持倉），Paper/Live 模式切換。

---

## 快速上手

```
雙擊 start.bat      → 啟動交易機器人（模擬盤）
雙擊 dashboard.bat   → 啟動監控儀表板（原生 Windows 視窗）
雙擊 stop.bat        → 停止交易機器人
```

- Python 環境在 `.venv/`，**不在系統 PATH**，所有指令必須透過 `.bat` 或先 `call .venv\Scripts\activate`
- 機器人和儀表板是獨立程序，可以同時運行
- 儀表板是**唯讀**的，不會控制機器人

---

## 文件索引

| 文件 | 內容 |
|------|------|
| **CLAUDE.md**（本文件） | 專案總覽、架構、策略規格、目錄結構 |
| [doc/backtest_history.md](doc/backtest_history.md) | 所有回測結果完整記錄（含 R10 Fix 16 輪 + GK 研究 + 稽核） |
| [doc/dual_strategy_research.md](doc/dual_strategy_research.md) | 雙策略 L+S 研究過程 |
| [doc/v9_research.md](doc/v9_research.md) | V9 $1K 帳戶最佳可行策略研究（4 輪、2400+ 配置、3 組 8/8 PASS） |
| [doc/v10_research.md](doc/v10_research.md) | V10 $1K 帳戶穩定獲利研究（6 輪 + 稽核、6700+ 配置、Short-only 4h-1h） |
| [doc/v11_research.md](doc/v11_research.md) | V11 TP/MH 出場優化研究（R1 掃描 204 組、V11-E 冠軍 OOS $2,801 +28%） |

---

## 目錄結構

```
cryptoBot/
│
│  ── 核心模組 ──
├── main_eth.py            # 入口：單執行緒主循環，每小時整點 +10s 喚醒
├── strategy.py            # 純指標計算 + 信號判斷（無副作用，不碰 API）
├── executor.py            # Paper/Live 執行引擎 + 狀態持久化 + Hedge Mode 倉位管理
├── data_feed.py           # Binance Futures 公開 API 抓 1h K 線（不需 API key）
├── binance_trade.py       # Binance API 下單模組（Hedge Mode, Algo Order SL）
├── recorder.py            # 4 層 CSV 記錄系統
├── telegram_notify.py     # Telegram 通知（進出場、每日摘要、錯誤告警）
│
│  ── 診斷工具 ──
├── check_health.py        # 策略健康報告（8 項指標：月交易量/SafeNet率/勝率/PF/DD...）
├── compare_backtest.py    # 回測 vs 實盤逐筆對比
├── verify_strategy.py     # strategy.py GK 指標 + 交易邏輯正確性驗證
│
│  ── 儀表板（Dashboard） ──
├── dashboard/
│   ├── app.py             # FastAPI 後端（5 個 API 端點）+ PyWebView 原生視窗啟動器
│   └── static/
│       ├── index.html     # SPA 主頁（4 個 tab）
│       ├── app.js         # 前端邏輯（圖表、表格、篩選、自動刷新）
│       └── style.css      # 深色主題樣式
│
│  ── 啟動腳本 ──
├── start.bat              # 一鍵啟動交易機器人
├── stop.bat               # 一鍵停止交易機器人（按 PID/視窗標題）
├── dashboard.bat          # 一鍵啟動儀表板
│
│  ── 狀態與設定 ──
├── .env                   # 環境變數（API key, Telegram token, PAPER_TRADING 開關）
├── eth_state.json         # Paper 模式持倉狀態（系統自動維護，勿手動修改）
├── eth_state_live.json    # Live 模式持倉狀態（未來正式盤用）
├── monitor_state.json     # 監控狀態（目前未使用）
├── trade_journal.db       # SQLite 交易日誌（目前未使用）
│
│  ── 資料目錄 ──
├── data/                  # Paper 模式資料（詳見下方）
├── data_live/             # Live 模式資料（未來正式盤用，結構同 data/）
├── logs/                  # 日誌檔
├── doc/                   # 文件
│
│  ── 回測研究 ──
├── backtest/research/     # 130+ 支回測研究腳本（歷史研究用，不影響運行）
│
│  ── 其他 ──
├── .venv/                 # Python 虛擬環境（Python 3.11）
├── requirements.txt       # pip 依賴清單
└── .gitignore
```

---

## data/ 目錄（交易資料）

| 檔案 | 說明 | 誰寫入 |
|------|------|--------|
| `trades.csv` | 每筆交易完整記錄（進出場價、PnL、MAE/MFE、出場原因...） | recorder.py |
| `bar_snapshots.csv` | 每根 K 線的快照（指標值、信號、持倉狀態） | recorder.py |
| `position_lifecycle.csv` | 持倉期間每根 bar 的狀態追蹤 | recorder.py |
| `daily_summary.csv` | 每日彙總（交易數、PnL、勝率...） | recorder.py |
| `ETHUSDT_1h_latest730d.csv` | ETH 1h K 線快取（730 天，~2.5MB） | data_feed.py |
| `BTCUSDT_1h_latest730d.csv` | BTC 1h K 線快取（回測/對比用） | data_feed.py |
| `ETHUSDT_4h_latest730d.csv` | ETH 4h K 線快取（回測研究用） | data_feed.py |
| `bar_snapshots_v5_backup.csv` | v5 升級前的備份 | 一次性 |

---

## logs/ 目錄

| 檔案 | 說明 |
|------|------|
| `system.log` | 主循環運行日誌（每小時一筆，含指標值、信號、倉位變動） |
| `signal.log` | 進出場信號專用日誌（方便快速查看觸發記錄） |
| `alerts.log` | 錯誤/告警日誌（API 失敗、異常狀態） |

---

## 目前狀態

- **策略版本**：**V11-E** — 雙策略 L+S（GK 壓縮突破 + TP + MaxHold，出場優化）
- **模式**：Paper Trading（模擬盤），Binance Testnet
- **Hedge Mode**：已啟用（dualSidePosition=true），L/S 倉位互不影響
- **帳戶**：$1,000 / $200 保證金 / 20x / $4,000 名目
- **演進**：GK v1.1 → v6 L+S → V10 → **V11-E（L+S $2,801, 12/13 正月, worst -$8）**
- **Dashboard**：FastAPI + TradingView LW Charts + PyWebView 原生視窗

---

## 策略規格 V11-E（鎖定）

> V11-E 目標：V10 出場優化。L/S 各 maxTotal=1，純 1h 無 4h 前瞻。
> 完整研究過程見 [doc/v11_research.md](doc/v11_research.md)。
> V10 研究見 [doc/v10_research.md](doc/v10_research.md)。

### L 策略（做多）— GK<25 壓縮突破 + TP 3.5% + MaxHold 6

```
方向：Long-only
時框：1h（純 1h，無 4h 數據）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場：
  1. GK pctile < 25（波動壓縮）
     gk = 0.5×ln(H/L)² - (2ln2-1)×ln(C/O)²
     ratio = mean(gk,5) / mean(gk,20)
     pctile = ratio.shift(1).rolling(100).apply(rank pctile)
  2. Close breakout 15 bar（c > c.shift(1).rolling(15).max()）
  3. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  4. Exit Cooldown: 6 bar
  5. Monthly Entry Cap: 20
  6. maxTotal = 1

出場（優先順序）：
  1. SafeNet -3.5%（含 25% 穿透模型，max 單筆虧損 ~$158）
  2. TP +3.5%（固定止盈）← V10 為 2.0%
  3. MaxHold: 6 bar（時間止損）← V10 為 5

風控熔斷：
  日虧 -$200 停 / 月虧 -$75 停 / 連虧 4 筆 → 24 bar 冷卻

OOS: $+1,473, WR ~59%, PF ~1.68
IS:  $+326
WF:  5/6, 7/8
```

### S 策略（做空）— GK<30 壓縮突破 + TP 2.0% + MaxHold 7

```
方向：Short-only（純 1h，無 4h 數據）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場：
  1. GK pctile < 30（波動壓縮）
  2. Close breakout 15 bar（c < c.shift(1).rolling(15).min()）
  3. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  4. Exit Cooldown: 8 bar
  5. Monthly Entry Cap: 20, maxTotal: 1

出場（優先順序）：
  1. SafeNet +4.0%（含 25% 穿透模型，max 單筆虧損 ~$200）
  2. TP -2.0%（固定止盈）← V10 為 1.5%
  3. MaxHold: 7 bar（時間止損）← V10 為 5

風控熔斷：
  日虧 -$200 停 / 月虧 -$150 停 / 連虧 4 筆 → 24 bar 冷卻

OOS: $+1,328, WR ~71%, PF ~2.65
IS:  $+709
WF:  5/6, 7/8
```

### L+S 合併績效 (OOS)

```
合計：$2,801, 12/13 正月, worst month -$8
L+S 互補：S 弱月有 L 撐，L 弱月有 S 撐
MDD: $186
V10 對比：$2,190→$2,801（+28%），PM 10/13→12/13，worst -$137→-$8
```

### 風控

```
$200 保證金 / 20x 槓桿 / $4,000 名目
Fee: $4/筆（taker 0.04%×2 + slip 0.01%×2）
L/S 各最多 1 筆同時持倉（maxTotal=1），合計最多 2 筆
L 月度 entry cap = 20 筆，S 月度 entry cap = 20 筆
L 月虧上限 -$75，S 月虧上限 -$150
日虧上限 -$200，連虧 4 筆 → 24 bar 冷卻
```

### 不要做的事

- 壓縮期結構止損（ETH 突破噪音 > 壓縮區間，<12h 全掃）
- S 策略加 EMA Trail（TP+MaxHold 更適合）
- Trailing TP for S（研究全部 mirage 或虧損）
- L 用 EMAcross 出場（WR 22-28%，MDD 無法控制）
- L 用 Pullback 進場（0/306 pass）
- 4h 數據（V10-S v1 的 4h EMA20 前瞻偏差已證實無效）

---

## 系統架構

### 交易機器人 (main_eth.py)

```
main_eth.py (單執行緒)
  ├── 啟動時檢查 Hedge Mode（dualSidePosition=true）
  ├── 每小時整點 +10s 喚醒
  ├── data_feed.fetch_eth_and_btc()          # 抓最新 K 線
  ├── strategy.compute_indicators()           # 計算 GK/EMA/Breakout 指標
  ├── executor.update_period_keys()           # 更新日/月熔斷計數器
  ├── 出場檢查：L → check_exit_long(), S → check_exit_short()
  ├── 進場信號：check_circuit_breaker() + evaluate_long/short_signal()
  ├── recorder.record_bar_snapshot() + record_position_bar()
  └── executor.save_state()                   # 持久化到 eth_state.json
```

### 執行引擎 (executor.py)

```
executor.py:
  positions dict: {trade_id: {sub_strategy: "L"/"S", ...}}
  last_exits: {"L": bar, "S": bar}（各策略獨立 cooldown）
  maxTotal=1 per side（最多 1L+1S）

  風控熔斷：
    - check_circuit_breaker(side): 日虧/月虧/連虧/月度 cap 檢查
    - update_period_keys(): 日/月 rollover 重置計數器
    - consec_losses: 連虧計數，4 筆 → 24 bar 冷卻

  Hedge Mode 邏輯：
    - open_position(): 自動帶 positionSide=LONG/SHORT，per-side SafeNet
    - SL 只在該方向第一筆倉位時下單（closePosition=true 覆蓋整個方向）
    - close_position(): 只在該方向最後一筆倉位平倉時才取消 SL

狀態持久化：eth_state.json（atomic write, 支持 v6→V10 自動遷移）
記錄系統：4 層 CSV（bar_snapshots / position_lifecycle / trades / daily_summary）
```

### Binance API (binance_trade.py)

```
Hedge Mode（雙向持倉）：
  - 所有訂單帶 positionSide=LONG 或 SHORT
  - SL 用 Algo Order API（/fapi/v1/algoOrder）+ closePosition=true
  - 每個方向只能有 1 個 closePosition SL
  - cancel_all_orders() 支持 position_side 參數，只取消指定方向的訂單

Testnet 特殊處理：
  - MARKET 訂單返回 avgPrice=0, status=NEW → 輪詢 query_order 最多 5 次
  - STOP_MARKET 不支持 new_order 端點 → 改用 Algo Order API
```

### 儀表板 (dashboard/)

```
dashboard/app.py:
  FastAPI 後端 (port 8050) + PyWebView 原生 Windows 視窗
  啟動時自動 kill_port(8050) 防止 port 衝突
  等 server 就緒後才開視窗

  API 端點（所有端點支援 ?mode=paper|live）：
    GET /api/status    → 餘額、持倉、今日PnL、GK pctile、健康度
    GET /api/klines    → 1500 根 K 線 + EMA20 + GK pctile（不分 mode）
    GET /api/trades    → 全部交易記錄
    GET /api/daily     → 每日彙總
    GET /api/analytics → 收益統計（勝率、PF、equity curve、出場分佈）

  路徑切換：
    Paper: eth_state.json + data/
    Live:  eth_state_live.json + data_live/

dashboard/static/:
  index.html — SPA 4 個 tab
  app.js     — 前端邏輯：TradingView LW Charts v4.2 + 表格 + 篩選 + 自動刷新
  style.css  — 深色主題（bg #0f0f1a, card #1a1a2e, green #26a69a, red #ef5350, gold #f0b90b）

  4 個頁面：
    1. 即時狀態：餘額、持倉、今日PnL、GK 壓縮指數（含區間說明）、策略健康度
    2. K 線圖：互動式 K 線 + EMA20 + GK 副圖 + 進出場標記
    3. 交易記錄：可排序/篩選表格，點擊行跳轉圖表
    4. 收益分析：統計卡片、equity curve、每日PnL、出場分佈、策略比較

  介面語言：繁體中文（英文）格式，例如「勝率 (Win Rate)」
  時間顯示：UTC+8（直接用 calendar.timegm，不做時區轉換）
```

---

## 環境設定 (.env)

```ini
PAPER_TRADING=true          # true=模擬盤, false=正式盤
BINANCE_TESTNET=true        # true=Testnet, false=正式 API
BINANCE_API_KEY=<key>
BINANCE_API_SECRET=<secret>
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<id>
```

---

## 常用指令

```bash
# 透過 .bat 啟動（推薦，不需手動 activate）
雙擊 start.bat              # 啟動機器人
雙擊 dashboard.bat           # 啟動儀表板
雙擊 stop.bat                # 停止機器人

# 或手動（需先 activate）
call .venv\Scripts\activate
python main_eth.py              # 啟動模擬盤
python dashboard/app.py         # 啟動儀表板
python check_health.py --days 30  # 健康報告
python compare_backtest.py       # 回測 vs 實盤對比
python verify_strategy.py        # 驗證策略一致性
```

---

## 回測研究腳本 (backtest/research/)

130+ 支研究腳本，涵蓋：
- `r1_*` ~ `r8_*`：8 輪策略迭代（波動率估計 → 進場過濾 → 出場敏感度 → 倉位管理...）
- `r10_fix_r1` ~ `r10_fix_r16`：16 輪 Bug Fix 回測
- `r10_validation.py`：最終驗證
- `dual_*`：雙策略 L+S 研究（13 輪）
- `explore_*`：GK 指標探索（skew, ret_sign, autocorr, kurtosis...）
- `phase2_gk_*`：GK 參數最佳化
- `audit_*`：最終稽核
- `eth_*`：ETH 專屬策略研究
- `btc_*`：BTC 策略研究（最終未採用）
- `theory_*`：理論驗證（ADX, Parkinson, FVG, Inside Bar...）

這些腳本是歷史研究記錄，不影響機器人運行。

---

## 排除的方向

完整記錄見 [doc/backtest_history.md](doc/backtest_history.md)。

- 5m 均值回歸（滑價吃光 edge）
- 資金費率套利（全部虧損）
- ETH 均值回歸（-$320）
- 所有壓縮期結構止損（ETH 1h 無效）
- Kaufman Efficiency Ratio / TTM Squeeze / Body Ratio（無 edge）
- Keltner / Choppiness / MultiScale Volatility Cone（均不如 GK）
- GK thresh=40（稽核發現 data-mining 風險，pass rate 66% 過鬆）

---

## 已知限制 & 注意事項

- **Testnet 行為差異**：MARKET 訂單返回 NEW 非 FILLED、不支持 STOP_MARKET via new_order、closePosition SL 每方向限 1 個
- **Python 不在 PATH**：必須用 `.bat` 或手動 activate `.venv`
- **eth_state.json 勿手動修改**：除非機器人已停止且需要清理錯誤狀態
- **Dashboard port 8050**：啟動時自動清理舊進程，但如果機器人佔用 port 會衝突
- **K 線快取 CSV (~2.5MB)**：data_feed.py 每次從 Binance API 即時抓取，CSV 是離線回測用
