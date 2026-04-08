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

- **策略**：雙策略 L+S（GK Compression-Breakout + CMP-Portfolio）
- **模式**：Paper Trading（模擬盤），Binance Testnet
- **Hedge Mode**：已啟用（dualSidePosition=true），L/S 倉位互不影響
- **起始餘額**：$10,000（Testnet）
- **演進**：GK v1.1 → 雙策略 L+S（OOS $23,996, 8/8 Gate PASS）
- **Dashboard**：FastAPI + TradingView LW Charts + PyWebView 原生視窗

---

## 策略規格（鎖定）

### L 策略（做多）— 趨勢跟隨

```
進場（OR-entry + AND 條件）：
  OR: GK pctile < 30 OR Skew(20) > 1.0 OR RetSign(15) > 0.60
    gk = 0.5×ln(H/L)² - (2ln2-1)×ln(C/O)²
    ratio = mean(gk, 5) / mean(gk, 20)
    pctile = min-max percentile over 100 bars with shift(1)
    skew = ret.rolling(20).skew().shift(1)
    ret_sign = (ret>0).rolling(15).mean().shift(1)
  AND:
    1. Close Breakout 10 bar（向上突破）
    2. Session Filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
    3. Exit Cooldown: L 上次出場後 12 bar
    4. 持倉 < 9 筆

出場（優先順序）：
  1. SafeNet -5.5%（安全網，含滑價模型）
  2. EarlyStop: bars 7-12, loss > 1%（OR Trail）
  3. EMA20 Trail: min hold 7 bar

OOS: 428t $13,882, PF 2.94, WR 45%
```

### S 策略（做空）— CMP-Portfolio 4 子策略

```
子策略配置：
  S1: GK<40, BL8,  maxSame=5, EXIT_CD=6
  S2: GK<40, BL15, maxSame=5, EXIT_CD=6
  S3: GK<30, BL10, maxSame=5, EXIT_CD=6
  S4: GK<40, BL12, maxSame=5, EXIT_CD=6

進場（各子策略獨立）：
  1. GK pctile < 子策略 threshold
  2. Close Breakout {8/10/12/15} bar（向下突破）
  3. Session Filter（同 L）
  4. 子策略 Cooldown 6 bar
  5. 子策略持倉 < 5 筆

出場（優先順序）：
  1. SafeNet +5.5%（安全網）
  2. TP: 固定止盈 2%
  3. MaxHold: 12 bar 時間止損

OOS: 1124t $10,113, PF 1.73, WR 65%
```

### 風控

```
$100 保證金 / 20x 槓桿 / $2,000 名目
Fee: $2/筆（taker 0.04%×2 + slip 0.01%×2）
L 最多 9 筆同時持倉，S 每個子策略最多 5 筆
```

### 不要做的事

- TP1 部分止盈（截斷 L 大贏家，破壞趨勢跟隨 edge）
- 壓縮期結構止損（ETH 突破噪音 > 壓縮區間，<12h 全掃）
- S 策略加 EMA Trail（CMP 用 TP+MaxHold 更適合）
- Trailing TP for S（研究全部 mirage 或虧損）

---

## 系統架構

### 交易機器人 (main_eth.py)

```
main_eth.py (單執行緒)
  ├── 啟動時檢查 Hedge Mode（dualSidePosition=true）
  ├── 每小時整點 +10s 喚醒
  ├── data_feed.fetch_eth_and_btc()          # 抓最新 K 線
  ├── strategy.compute_indicators()           # 計算 GK/EMA/Breakout 指標
  ├── 出場檢查：L → check_exit(), S → check_exit_cmp()
  ├── 進場信號：evaluate_long_signal() + evaluate_short_signals()
  ├── recorder.record_bar_snapshot() + record_position_bar()
  └── executor.save_state()                   # 持久化到 eth_state.json
```

### 執行引擎 (executor.py)

```
executor.py:
  positions dict: {trade_id: {sub_strategy: "L"/"S1"/"S2"/"S3"/"S4", ...}}
  last_exits: {"L": bar, "S1": bar, ...}（每個子策略獨立 cooldown）
  can_open_sub(): 按子策略計數

  Hedge Mode 邏輯：
    - open_position(): 自動帶 positionSide=LONG/SHORT
    - SL 只在該方向第一筆倉位時下單（closePosition=true 覆蓋整個方向）
    - close_position(): 只在該方向最後一筆倉位平倉時才取消 SL

狀態持久化：eth_state.json（atomic write, 支持舊格式自動遷移）
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
