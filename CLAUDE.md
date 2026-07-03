# CryptoBot

ETH 1h Garman-Klass Compression-Breakout 自動交易機器人（Binance Futures）。
雙策略 L+S，Binance Hedge Mode（雙向持倉），Paper/Live 模式切換。

---

## 快速上手

```
雙擊 start.bat 或 dashboard.bat → 啟動儀表板 + 自動啟動機器人
關閉儀表板視窗               → 自動停止機器人
雙擊 stop.bat                → 強制停止（備用）
```

- Python 環境在 `.venv/`，**不在系統 PATH**，所有指令必須透過 `.bat` 或先 `call .venv\Scripts\activate`
- **儀表板 = 控制中心**：開啟儀表板自動啟動機器人（subprocess），關閉儀表板自動停止
- 儀表板可查看即時日誌（system.log / signal.log / alerts.log）

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
| [doc/v12_research.md](doc/v12_research.md) | V12 全新 S 進場研究（8 輪、15+ 方向 — 結論：GK 壓縮突破無可取代） |
| [doc/v13_research.md](doc/v13_research.md) | V13 全時框探索 + GK 窗口優化 + 出場增強（R5: OOS $3,526 +26%, 8/8 PASS） |
| [doc/v14_research.md](doc/v14_research.md) | V14 出場機制創新（R6: L OOS +$293 +16.8%, MFE trail + Conditional MH, WF 6/6, 12/13 正月） |
| [doc/v15_research.md](doc/v15_research.md) | V15 進場過濾優化（10-Gate 稽核 REJECTED：ATR 事後選擇 + Cascade 100th pctile 運氣，V14 維持最佳） |
| [doc/v16_research.md](doc/v16_research.md) | V16 全新策略探索（TBR Flow Reversal APPROVED w/ downgrade：10-Gate 稽核 6P/4C/0F，核心 alpha 在 breakout 非 TBR，V14 backup） |
| [doc/v17_research.md](doc/v17_research.md) | V17 非 breakout alpha 探索（4 輪、572 配置 — 結論：ETH 1h 非 breakout alpha 不存在，15-bar breakout 是唯一 alpha source） |
| [doc/v18_research.md](doc/v18_research.md) | V18 多時框非 breakout 搜索（15m/30m/1h、644 配置 — 結論：ETH 非 breakout alpha 在任何時框都不存在，手續費非瓶頸） |
| [doc/v19_research.md](doc/v19_research.md) | V19 跳脫框架探索（宏觀/情緒/HMM — 結論：所有可取得數據源下 ETH alpha = breakout only，非 BRK 是 random walk） |
| [doc/v20_research.md](doc/v20_research.md) | V20 多標的 V14 框架測試（9 幣種 locked-parameter 篩選 — 結論：V14 是 ETH-specific，9/9 FAIL IS<0） |
| [doc/v21_research.md](doc/v21_research.md) | V21 Path A 獨立 edge + Path B V14.1 改良（10 輪全 REJECTED — 結論：V14 局部最佳，OHLCV 已耗盡） |
| [doc/v22_research.md](doc/v22_research.md) | V22 古典 TA 理論掃描（8 輪 Ichimoku/H&S/三角/Harmonic/Fib/Pivot/Elliott/Wyckoff 全 REJECTED — regime-dependent 或極端過擬合） |
| [doc/v23_research.md](doc/v23_research.md) | V23 壓力測試 + 3 條 overlay（**Path R 非對稱 slope gate PROMOTED 12/13 gates**，V14+R: PnL +6%/MDD -11%/Sharpe +18%/Worst30d -35%；Path V/H REJECTED） |
| [doc/v24_research.md](doc/v24_research.md) | V24 風險工程（B 槓桿線性可調 5x/10x/15x/20x；**A vol overlay REJECTED 0/23、C 多標的分散 REJECTED 0/10**；BEST = V14+R @ 可調槓桿，paper 建議 10x） |
| [doc/v25_research.md](doc/v25_research.md) | V25 Regime-conditional exits（**V25-D PROMOTED 12/12 gates**，S_MH_UP 10→8 + L_TP_DOWN 3.5→4.0 + L_MH_MILD_UP 6→7：PnL +3.1%、WR +0.7%、MDD -10.5%、G4 6/6 鄰域穩定） |
| [doc/v26_research.md](doc/v26_research.md) | V26 MaxHold-loss 優化（上線後觸發；**R1 give-up + R2 分批止盈 + R3 分批止損 三面 130+ 配置全 REJECTED**：bar N 時贏家輸家尚未分化——give-up 砍慢熱贏家、止盈 75% 課稅在大贏單、止損 43% 砍在會反彈贏家；WR 可升但 PnL 必降，MH 確認 negative-by-construction 非漏洞） |
| [doc/v27_research.md](doc/v27_research.md) | V27 Meta-labeling 進場過濾（**R1 提前 REJECTED**：23 進場時特徵 × logit/GBM 預測 MH 結局，WF AUC 最佳 0.614 perm p=0.13、OOS 確認 0.44~0.58 ≈ 擲硬幣，靜態過濾移除的全是正 PnL——進場當下也分不出誰會變 MH，**MH 方向徹底關閉**：進場分不出(V27)+持倉中未分化(V26)+桶恆≤0 是選擇效應） |
| [doc/v28_research.md](doc/v28_research.md) | V28 複利化研究（**帳戶層 PROMOTED 未部署**；R1 恆 4x 複利：bootstrap P(複利輸固定)=0%、MDD 中位 31.9%；**R2 使用者「獲利加倉」方案（200U+獲利×20x、虧損退回地板）+ 名目上限 = 建議採用形態**：cap $12K/$20K 終值 $23.6K/$38.2K vs 固定 $8.8K、MDD 24%、地板使下行=現行；無上限版 REJECTED MDD 92%+；前提=熔斷百分比化+edge 存續，建議實盤滿 50 筆貼合回測後再切換） |

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
├── labels.py              # 共用中文(英文)詞彙對照（出場原因/進場趨勢/方向）+ 全形對齊工具
│                          # 所有顯示層（analyze/check_health/run_backtest/Telegram）統一取詞，改字只改這一處
├── paths.py               # 多實例路徑解析：INSTANCE_DIR 分流 data/logs/state（多人使用）
│                          # 未設 INSTANCE_DIR 則沿用程式目錄（單人 = 原行為，向後相容）
│
│  ── 診斷工具 ──
├── check_health.py        # 策略健康報告（8 項指標，中文(英文)輸出：月交易量/安全網率/勝率/PF/回撤...）
├── analysis_report.py     # 收益分析共用計算（Telegram /analysis + analyze.py 共用，只讀 CSV；中文(英文) 出場/趨勢）
├── analyze.py             # 終端機收益分析 CLI（彙總 + -t 交易列表，VPS 上免開 dashboard 看績效）
├── signal_status.py       # 即時開單條件共用計算（Telegram /signal + check_signal.py 共用）
├── check_signal.py        # 終端機開單條件 CLI（L/S 每 gate ✅/❌ + 可開單時段；勿命名 signal.py 會撞內建模組）
├── menu.py                # 終端機指令清單（等同 Telegram /help，列出所有 CLI + Telegram 指令）
├── run_backtest.py        # 終端機回測 CLI（可選日期範圍，= 儀表板回測 tab 同引擎 V14+R+V25-D）
│                          # 預設「貼近實盤」成交（TP/BE 市價收盤成交）；--ideal 切理論價、--slip 加滑價
├── fetch_backtest_data.py # 補回測 K 線快取（Binance 公開端點分頁抓 730 天，VPS 跑回測用）
├── verify_mainnet.py      # 正式盤上線前唯讀體檢（API/餘額/Hedge Mode/精度/K線）
│
│  ── 儀表板（Dashboard） ──
├── dashboard/
│   ├── app.py             # FastAPI 後端（7 個 API 端點）+ 機器人子進程管理 + PyWebView
│   └── static/
│       ├── index.html     # SPA 主頁（5 個 tab：狀態/K線/交易/分析/日誌）
│       ├── app.js         # 前端邏輯（圖表、表格、日誌查看、自動刷新）
│       └── style.css      # 深色主題樣式
│
│  ── 啟動腳本 ──
├── start.bat              # 一鍵啟動儀表板（自動啟動機器人）
├── stop.bat               # 一鍵停止（備用，關儀表板視窗即可）
├── dashboard.bat          # 同 start.bat（啟動儀表板 + 機器人）
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
| `ETHUSDT_1h_latest730d.csv` | ETH 1h K 線快取（730 天，~2.5MB） | fetch_backtest_data.py |
| `BTCUSDT_1h_latest730d.csv` | BTC 1h K 線快取（回測/對比用） | fetch_backtest_data.py |
| `ETHUSDT_4h_latest730d.csv` | ETH 4h K 線快取（回測研究用） | fetch_backtest_data.py |
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

### ⚠️ 待部署（2026-07-02 本機 workspace 產出，尚未更新 VPS）

> 本段記錄 2026-07-02 在 Windows 工作機完成、**還沒部署到 VPS** 的變更。
> 換電腦接手時：先確認這些變更已 commit + push（否則只存在原本機的 working tree），
> 再到 VPS `git pull` → 挑**無持倉**時 `systemctl restart cryptobot@<名字>`。

**1. strategy.py 動態風控（程式變更，向後相容）**
- FEE / 日虧 / L 月虧 / S 月虧 四條 $ 風控線改為以 200U 保證金為基準、
  隨 `.env` 的 `MARGIN_PER_TRADE` 等比縮放（`_RISK_SCALE = MARGIN/200`）
- `MARGIN_PER_TRADE=200`（預設）時數值與舊版**完全相同**（-200/-75/-150/FEE 4），
  部署重啟無行為變化；多實例其他使用者不受影響
- 已驗證：200/350/1000U 三檔縮放正確、相關檔案 py_compile 全過

**2. 多實例 .env 誤載修正（嚴重 bug，多實例上線前必上）**
- 問題：python-dotenv 的 `find_dotenv()` 從「程式檔所在目錄」找 .env 而**非 cwd**
  → 原設計（unit 只設 WorkingDirectory）下，每個實例都會誤載程式目錄的 .env
  （= 擁有者的 Binance key / Telegram token 被別人的實例拿去下單）。已本機實測重現
- 修正（三層）：`paths.py` 以 `INSTANCE_DIR/.env` **override 重載**（核心）；
  main_eth / analyze / check_signal 把 `import paths` 提到專案模組最前（覆寫先於
  strategy/binance_trade 讀 env）；`cryptobot@.service` 加 `EnvironmentFile=` 雙保險
- 已驗證：INSTANCE_DIR 指到實例目錄時 MARGIN/PAPER_TRADING/INSTANCE_NAME 全部取實例值、
  動態風控跟著實例保證金縮放；未設 INSTANCE_DIR（單人）行為完全不變
- **VPS 部署注意**：多實例 commits（b073bbd/a3f991c/40921f4）已在 GitHub 但 VPS 未 pull，
  此修正必須與之一起上；若 VPS 已複製過舊版 template →
  `sudo cp deploy/cryptobot@.service /etc/systemd/system/ && sudo systemctl daemon-reload`

**3. V27 + V28 研究（文件 + 腳本，不影響機器人運行）**
- V27 meta-labeling REJECTED（詳見 doc/v27_research.md）→ MH 方向徹底關閉
- V28 複利化 PROMOTED（帳戶層，doc/v28_research.md）：
  採用形態 = **人肉複利**（.env 調 `MARGIN_PER_TRADE`，程式已支援免改 code）
  SOP：每月 1 號 `保證金 = 200 + 累計獲利×50%`（封頂 1000U、地板 200U），
  挑無持倉時改 .env + 重啟；400U 錢包全明細見 doc/v28_half_ratchet_trades.txt
- 連敗條件勝率分析（backtest/research/streak_wr_analysis.py）：
  連敗數對下一筆 WR 無影響（perm p=0.861），不做任何連敗加碼/跳過規則

**4. 同月改兩次保證金的注意事項（重要）**
- 月度虧損熔斷是「美元累加 vs 當下 cap」比較：**月中調小保證金**可能使已累積虧損
  立刻 ≤ 新 cap → 該邊熔斷鎖單到月底（例：1000U 虧 -$120 後調回 200U，-120 ≤ -75 即鎖）
- 月中調大則使當月風控偏鬆。**只在每月 1 號（計數器歸零後）調整就完全沒有此問題**
- 若未來需要月中自由調整 → 待辦：executor 月度計數器改「R 單位」正規化
  （每筆 PnL ÷ 當筆保證金，cap 比 -0.375R / -0.75R / -1.0R）

**5. 本機（Windows 工作機）環境備註**
- `.venv` 為研究補裝了 numpy / pandas / scikit-learn / python-dotenv（非機器人依賴）
- `data/` 已抓 ETH+BTC 1h 730 天快取（2024-07-02 ~ 2026-07-02）

- **策略版本**：**V14+R + V25-D**（兩者皆已部署上線）
  - V14+R Path R 斜率 gate 部署 commit `3ff1e82` / `74cdd22`（2026-04-22 23:06）
  - V25-D Regime-conditional exits 部署 commit `971d934`（2026-04-23 11:54）
- **V14 稽核狀態（2026-04-21）**：G4 參數鄰域 20/20 PASS、G7 WF 5/6、G9 移除最佳月全正 — **參數穩健**；但 **G8 時序翻轉 FAIL**（反轉 OHLCV 後 $-4,627）→ V14 是 **regime-dependent**，依賴 ETH 多頭 drift + 壓縮突破結構，非 risk-neutral alpha。詳見 [doc/v22_research.md](doc/v22_research.md) V14 自稽核章節。
- **V23 overlay 研究結果（2026-04-22）**：Path R 非對稱 per-side SMA200 斜率 gate 通過 12/13 gates（G5 cascade 97th percentile、G7 WF 4/6、G8 時序翻轉改善 +$438），V14+R 參數 TH_UP=0.045 / TH_SIDE=0.010，2 年回測 PnL +$380 / MDD -$54 / Sharpe +0.91 / Worst30d -$197
- **V23 G6 驗證（2026-04-22）**：獨立驗證 V14 baseline G6 兩方向全 FAIL（Fwd -113.8% / Bwd +53.2%），V14+R 只 Fwd FAIL（-94.4%）且 Bwd 由 FAIL 轉 PASS（+48.6%）；overlay 貢獻 IS/OOS 同向（+$258 / +$121）但衰退率 52.9% 邊緣 → 情境 A 成立，V14+R 可部署但對 +$380 改善量級須降級信心至 +$120~$380 區間
- **V25 Regime-conditional exits（2026-04-22）**：V25-D PROMOTED 12/12 gates — `S_MH_UP 10→8` + `L_TP_DOWN 3.5→4.0%` + `L_MH_MILD_UP 6→7`，2Y PnL $6,789（+$206, +3.1%）、WR 62.3%（+0.7%）、**MDD $334（-$39, -10.5%）**、Sharpe 6.23、G4 6/6 鄰域穩定、G8 reversed 改善（-3717 vs -3981）。V25-D 是 V14+R 純出場優化，進場 100% 沿用
- **模式**：**LIVE 正式盤（mainnet 真錢）**，資料寫在 `data_live/`（VPS systemd 服務跑；連線/維運/部署見 [deploy/cheatsheet.txt](deploy/cheatsheet.txt)）
- **Hedge Mode**：已啟用（dualSidePosition=true），L/S 倉位互不影響
- **帳戶**：$1,000 / $200 保證金 / 20x / $4,000 名目
- **演進**：GK v1.1 → v6 L+S → V10 → V11-E → V13 → V14 → **V14+R → V14+R+V25-D（線上）**
- **Dashboard**：FastAPI + TradingView LW Charts + PyWebView 原生視窗（**目前已停用，改用終端機 + Telegram**）
- **顯示慣例**：所有終端機/Telegram 輸出的出場原因、進場趨勢、方向一律「中文 (英文)」格式，統一由 `labels.py` 產生（如 `止盈 (TP)`、`偏多 (MILD_UP)`）；交易列表時間顯示為**實際成交時刻**（K 棒收盤 = 開盤+1h，對齊幣安後台）
- **每小時心跳**：有持倉時額外顯示該倉的出場條件（止盈/安全網價位、最長持倉剩餘、浮盈回吐狀態）
- **回測成交假設**：`run_backtest.py` 預設「貼近實盤」（TP/BE 用市價收盤成交，非理論價；SafeNet 維持真實 stop），`--ideal` 可切回理論價對照、`--slip` 加滑價壓測；引擎 `simulate_v14_detailed(realistic=,slip_bps=)`，研究腳本預設仍理想化
- **多實例（多人使用，方案 A）**：一份程式碼 + 每人一個實例（各自 Binance key / Telegram bot / 資料 / 狀態）。靠環境變數 `INSTANCE_DIR` 分流 `data/`·`logs/`·`eth_state*.json`（`paths.py`，未設則沿用程式目錄=單人原行為）。部署用 systemd template `cryptobot@<名字>` + `instances/<名字>/.env`，步驟見 [deploy/cheatsheet.txt](deploy/cheatsheet.txt)「多實例」段。每則 Telegram 訊息開頭標「👤 實例名」（`INSTANCE_NAME`，未設則用目錄名；單人為空不加），讓多人各自確認查到自己的。注意每人需各自一支 Telegram bot（同 token 兩進程會搶更新）。**共用 K 線**：多實例時每小時只有一個實例真的抓 Binance、其他讀共用檔（`data_feed.py`，flock 去重，快取在 `cache/`，所有實例共用；fail-open 退回各自抓；單人維持原樣）

### 模擬盤運行狀態（2026-05-20 21:00 UTC+8 快照；此為轉正式盤前的 Testnet 模擬期紀錄）

- **線上策略**：V14+R + V25-D（strategy.py，最新 commit `0bb5161`）
- **帳戶餘額**：$4,325.54（Testnet 起始非 $1K，cumulative_pnl 為 Testnet 基準值）
- **累計實現 PnL（37 天，自 2026-04-14 起）**：**+$83.45**（10 筆，平均 $8.3/筆）
- **本月（2026-05）**：L 2 entries / S 1 entry，月度 PnL L -$12.79 / S -$17.14 = **-$29.93**
- **當前持倉**：無（trade #10 於 2026-05-20 05:00 出場）
- **連虧狀態**：連虧 2 筆（trade #9/#10），未到 4 筆冷卻門檻

**已完成交易（10 筆，37 天）**

| # | 時間(UTC+8) | 方向 | 進場 | 出場 | 出場類型 | 當下版本 | regime | PnL | 結果 |
|---|---|---|---:|---:|---|---|---|---:|---|
| 1 | 04-14 14:00 | L | 2372.44 | 2389.20 | BE (ext) | V14 | MILD_UP | +$12.52 | WIN |
| 2 | 04-14 22:00 | S | 2359.06 | 2313.31 | TP | V14 | MILD_UP | +$74.64 | WIN |
| 3 | 04-16 21:00 | S | 2306.44 | 2346.92 | MaxHold | V14 | UP | -$71.79 | LOSS |
| 4 | 04-21 20:00 | S | 2307.51 | 2310.42 | MaxHold | V14 | MILD_UP | -$8.25 | LOSS |
| 5 | 04-22 10:00 | L | 2368.44 | 2390.48 | MFE-trail | V14 | MILD_UP | +$33.99 | WIN |
| 6 | 04-22 20:00 | L | 2403.41 | 2395.80 | MaxHold | V14 | MILD_UP | -$15.86 | LOSS |
| 7 | 04-23 08:00 | S | 2369.70 | 2315.56 | TP | V14+R | MILD_UP | +$88.12 | WIN |
| 8 | 05-01 09:00 | L | 2266.61 | 2277.22 | MFE-trail | V14+R+V25-D | SIDE | +$15.52 | WIN |
| 9 | 05-05 11:00 | L | 2382.22 | 2367.27 | MaxHold | V14+R+V25-D | SIDE | -$28.30 | LOSS |
| 10 | 05-19 19:00 | S | 2110.34 | 2117.69 | MaxHold | V14+R+V25-D | DOWN | -$17.14 | LOSS |

**績效統計**：WR 5/10 = 50%、實現 PnL +$83.45、無 SafeNet 觸發、無熔斷觸發
**出場分佈**：MaxHold 5（-$141.34）/ TP 2（+$162.76）/ MFE-trail 2（+$49.51）/ BE 1（+$12.52）/ SafeNet 0
**月度**：2026-04 +$113.37（7 筆）/ 2026-05 -$29.93（3 筆）
**版本驗證**：所有出場 hold_bars 與當下版本邏輯一致；V25-D 部署後 3 筆 regime 皆 fallback 到 default（SIDE/DOWN 不在覆寫鍵），V25-D 差異尚未在實盤體現

---

## 策略規格 V14（鎖定）

> V14 目標：V13 L 出場機制創新（MFE Trailing + Conditional MH）。S 完全不動。
> 完整研究過程見 [doc/v14_research.md](doc/v14_research.md)。
> V13 研究見 [doc/v13_research.md](doc/v13_research.md)。

### L 策略（做多）— GK<25 壓縮突破 + TP 3.5% + MFE trail + MaxHold 6(cond5) + ext2 BE

```
方向：Long-only
時框：1h（純 1h，無 4h 數據）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee

進場（與 V13 完全相同）：
  1. GK pctile < 25（波動壓縮）
     gk = 0.5×ln(H/L)² - (2ln2-1)×ln(C/O)²
     ratio = mean(gk,5) / mean(gk,20)   ← L 用 5/20
     pctile = ratio.shift(1).rolling(100).apply(rank pctile)
  2. Close breakout 15 bar（c > c.shift(1).rolling(15).max()）
  3. Session filter: block hours {0,1,2,12} UTC+8, block days {Sat,Sun}
  4. Exit Cooldown: 6 bar
  5. Monthly Entry Cap: 20
  6. maxTotal = 1

出場（優先順序）：
  1. SafeNet -3.5%（含 25% 穿透模型，max 單筆虧損 ~$158）
  2. TP +3.5%（固定止盈）
  3. 【V14】MFE Trailing：浮盈曾達 1.0% 後回吐 0.8% → bar_close 出場
     - running_mfe = max(所有 bar 的 (high - entry) / entry)
     - 啟動：running_mfe >= 1.0%
     - 觸發：(running_mfe - current_close_pnl%) >= 0.8%
     - 最早 bar 1 可觸發（min_bar=1）
     - Extension 期間也有效
  4. 【V14】Conditional MH：bar 2 虧 >=1.0% → MH 從 6 縮短為 5
     - 只在 bars_held == 2 時判定一次
     - (close - entry) / entry <= -1.0% → mh_reduced=True
  5. MaxHold 6 bar（或 5 bar if reduced）→ 若正收益，延長 2 bar + BE trail
     - Extension: 額外 2 bar，期間若 low <= entry_price → BE 出場
     - ext 超時 → MH-ext 收盤出場
     - 負收益 → 直接 MaxHold 收盤出場

風控熔斷：
  日虧 -$200 停 / 月虧 -$75 停 / 連虧 4 筆 → 24 bar 冷卻

OOS: $+2,034, WR 60%, MDD $228
WF:  6/6
```

### S 策略（做空）— GK_S<35 壓縮突破 + TP 2.0% + MaxHold 10 + ext2 BE

```
方向：Short-only（純 1h，無 4h 數據）
帳戶：$1,000 / $200 margin / 20x / $4,000 notional / $4 fee
V14：完全不動（V14 R2/R3/R4 測試 70+ 種 S 出場調整全部更差）

進場：
  1. GK pctile_S < 35（波動壓縮）
     ratio_S = mean(gk,10) / mean(gk,30)   ← S 用 10/30
     pctile_S = ratio_S.shift(1).rolling(100).apply(rank pctile)
  2. Close breakout 15 bar（c < c.shift(1).rolling(15).min()）
  3. Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
  4. Exit Cooldown: 8 bar
  5. Monthly Entry Cap: 20, maxTotal: 1

出場（優先順序）：
  1. SafeNet +4.0%（含 25% 穿透模型，max 單筆虧損 ~$200）
  2. TP -2.0%（固定止盈）
  3. MaxHold 10 bar → 若正收益，延長 2 bar + BE trail
     - Extension: 額外 2 bar，期間若 high >= entry_price → BE 出場
     - ext 超時 → MH-ext 收盤出場
     - 負收益 → 直接 MaxHold 收盤出場

風控熔斷：
  日虧 -$200 停 / 月虧 -$150 停 / 連虧 4 筆 → 24 bar 冷卻

OOS: $+2,408, WR 62%, MDD $313
WF:  5/6, 7/8
```

### L+S 合併績效 (OOS)

```
合計：$4,549, 12/13 正月, worst month -$91
L+S 互補：S 弱月有 L 撐，L 弱月有 S 撐
V13 對比：$4,004→$4,549（+14%），L 改善 +$293（+17%）
```

### 風控

```
$200 保證金 / 20x 槓桿 / $4,000 名目（.env MARGIN_PER_TRADE / LEVERAGE 可調）
Fee: $4/筆（taker 0.04%×2 + slip 0.01%×2）
L/S 各最多 1 筆同時持倉（maxTotal=1），合計最多 2 筆
L 月度 entry cap = 20 筆，S 月度 entry cap = 20 筆
L 月虧上限 -$75，S 月虧上限 -$150
日虧上限 -$200，連虧 4 筆 → 24 bar 冷卻
※ 以上 $ 值為 200U 基準；FEE/日虧/月虧會隨 MARGIN_PER_TRADE 等比縮放（動態風控）
```

### 不要做的事

- 壓縮期結構止損（ETH 突破噪音 > 壓縮區間，<12h 全掃）
- S 策略加 EMA Trail（TP+MaxHold 更適合）
- Trailing TP for S（研究全部 mirage 或虧損）
- L 用 EMAcross 出場（WR 22-28%，MDD 無法控制）
- L 用 Pullback 進場（0/306 pass）
- 4h 數據（V10-S v1 的 4h EMA20 前瞻偏差已證實無效）
- 替換 S 進場信號（V12 8 輪研究證實 GK 壓縮突破無可取代）
- 均值回歸做空（EMA overext / RSI overbought / SMA dev — ETH 趨勢性太強）
- 月相交易信號（統計不顯著 p>0.3）
- S 出場參數調整（V14 R2/R3/R4 測試 70+ 種調整，全部更差，S 是 globally optimal）
- S 加 MFE Trailing（V14 R2 測試 45 種配置全部更差）
- S 加 Conditional MH（V14 R3 測試 57 種配置全部更差）
- ATR 最低門檻進場過濾（V15 10-Gate 稽核 REJECTED：IS 中低 ATR 交易正收益，過濾器為事後選擇）
- GK percentile 最低門檻進場過濾（V15 稽核：OOS 被移除交易正收益 +$133，改善全靠 cascade 運氣）
- 任何依賴 cascade 效果的進場過濾器（V15 稽核：cascade 在 100x 隨機模擬排 100th percentile）
- 把 breakout 過濾器（GK/TBR/其他）當成獨立 edge source（V16 稽核：核心 alpha 在 15-bar breakout，過濾器各降 PnL 26% 換 MDD 減半）
- 同時運行 V14+S2 在 $1K 帳戶（V16 稽核：最多 4 持倉，帳戶無法承受）
- 任何非 breakout 的 ETH 1h 進場信號（V17 4 輪 572 配置全部失敗：均值回歸 MFE 不足、rpos 是 breakout 代理 93%、candle/volume/EMA cross/time-of-day 全為噪音）
- Range position (rpos) 作為獨立信號（V17 R2/R3：rpos>0.90 = breakout，去掉 breakout bars 後 ALL IS 負）
- 在 15m/30m 上尋找非 breakout alpha（V18：30m 35 信號 ALL IS 負、15m 37 信號 1 IS+ OOS 失敗，手續費非瓶頸 fee%=7-11，問題是方向預測力為零）
- 跨市場宏觀預測 ETH（V19 R1：SPX/DXY/VIX/US10Y 全部 r<0.08 p>0.05，ETH 即時反應無滯後）
- Fear & Greed Index 做反向/順向交易（V19 R1：FGI 五分位 Q5-Q1 spread = +0.01% p=0.98）
- HMM / regime detection 找非 breakout alpha（V19 R2：3-state HMM 所有 state 的 non-BRK fwd=0, p>0.7）
- 動量/偏度 regime 交易非 breakout bars（V19 R2：IS/OOS 全部巨虧 -$3K~-$24K）
- V14 框架直接套用其他加密貨幣（V20 R0：9 個主流幣 locked-parameter 篩選 9/9 FAIL，全部 IS<0，breakout 後方向性是 ETH-specific）
- Weekly / Daily OR / Staircase / Prior Day HL / Monthly / Swing Pivot 的事件錨定 breakout（V21 Path A 7 輪全 REJECTED，edge 依賴 ETH 多頭方向，時序翻轉即消失）
- V14 的 cooldown 擴展 / 早切 / L-only 連敗跳過（V21 Path B B1/B2/B3 全 REJECTED：V14 已是 tightly-optimized local maximum，動 IS 必降或過擬合）
- Ichimoku Cloud 訊號（TK 交叉 / Cloud breakout / 3-confirm / 4-confirm — V22 R1 ALL OOS 負 -$225~-$2092）
- Head & Shoulders / Inverse H&S 形態（V22 R2 iHS IS 73% WR $1458 → G6 Fwd +69%/Bwd -225% + G8 時序翻轉 -$343，regime-dependent）
- 三角形態 breakout（Ascending / Descending / Symmetrical — V22 R3 ASC L IS $851/OOS $1614 看似正向但 G6 Fwd -89.7% + G8 -$834 + N×M 孤峰）
- Harmonic XABCD patterns（Gartley / Bat / Butterfly / Crab — V22 R4 ETH 1h 2 年僅 1 match，樣本過稀）
- Fibonacci retracement swing entry（38.2 / 50 / 61.8% — V22 R5 0 IS+ configs）
- Classical Pivot Points 突破或均值回歸（Daily/Weekly PP/R1/R2/S1/S2 — V22 R6 brk_wS1 S IS 85% WR $2044 → OOS -$61，極端過擬合）
- Elliott Wave 3-wave impulse trigger（V22 R7 EW3_S_N7 IS $747 → OOS $278 衰減 63%）
- Wyckoff Spring / Upthrust via volume + wide-range bars（V22 R8 Upthrust top 8 配置 OOS 全部 -$552~-$2648，巨翻轉）
- 對稱單一 regime gate 過濾 L+S（V23 R1-R3 SMA 斜率 / ATR pctile / ADX 全 REJECTED：V14 L 最弱 UP、S 最弱 SIDE，需 per-side 非對稱才有效）
- Size scaling 做 regime 風險降低（V23 Path V 3 輪 R1-R3 soft scaling 全部劣於 Path R hard block：V14 是 binary edge，部分倉位無法選擇性避開輸家）
- Inverse-volatility position sizing（V23 Path V R3：TARGET/ATR% 公式在 V14 系統上 PnL -$185、WF 0/6，最壞 30d 未改善因該段同時高 PnL 高 ATR）
- BTC V14 作為 ETH V14 的 hedge（V23 Path H R1：BTC V14 標的 PnL -$1,762、月度相關 -0.016 但 7 個 ETH 負月中 BTC 僅 2 個月正收益，結構失敗）
- 在 V14+R 之上疊加波動 overlay 改善尾端（V24 Direction A：ATR/RV/HL 23 配置全 FAIL，R gate 已吸收 vol 過濾可做的工作，overlay 只能移除好 trade）
- 在 $300 子帳戶跑 crypto 獨立策略做分散（V24 Direction C：Donchian+trend filter 11 幣 × 3 TF × 166 配置，37 個通過 KPI 但 10/10 Top 候選 IS/OOS 失敗或 r>=0.3，crypto 在 bull regime 共漲結構性相關）
- Mixed L/S 槓桿以為可改善 Sharpe（V24 Direction B：L/S 不對稱槓桿全部劣化 Sharpe 5.58-5.94 vs uniform 6.03，純風險偏好請用 uniform）
- 把 L_TP_DOWN 放寬至 5% 以上（V25 R2：+$300 PnL 但 G4 邊緣、WR 未同步提升，V25-E 邊界 4/4 含 0.055 側；V25-D 保守版 4.0% 6/6 鄰域全 PASS）
- 縮短 S_MH 在 MILD_UP 或 DOWN regime（V25 R2：S MILD_UP WIN P75=8.5 / DOWN P75=8.8 高於 MH 邊緣，縮短必砍 WIN，ΔPnL -$91~-$467 全部劣化）
- 拉長 L_MH 在 DOWN 或 SIDE regime（V25 R2：L WIN 已被 MFE-trail 吸收，L_MH_DOWN=7/8/9 全部 -$282~-$409，L_MH_SIDE 各值也全部劣化）
- 調整 L_TP_MILD_UP（V25 R2：±0.005 都降 PnL，MILD_UP regime L 已是局部最佳）
- 提升 S_TP 至 2.5%/3.0%（V25 R2：WR 下降 2-4%，不符雙改善（WR+PnL）目標）
- MFE-floor / running-MFE give-up 出場（V26 R1：K×FLOOR 24+ 配置全劣於 baseline，終端 MFE 的乾淨分離是事後諸葛，因果逐根套用會砍掉慢熱贏家，#GU 常 > 77 筆 MH 總數，最佳僅 -$249）
- 分批 / scaled-out 止盈降 MH 虧損（V26 R2：N×F 36+ 配置全劣於 baseline，分批 75% 打在最終贏家上削大贏單，最終 MH 輸家近 6 成在 bar N 已在水下分批碰不到，WR 升但 PnL 必降，MH 桶幾乎沒縮）
- 分批止損 / loss-side scale-out 降 MH 虧損（V26 R3：72 配置全劣於 baseline，43% 砍在會反彈的贏家且恰是後來打 TP 的大單，WR 還降，MH 桶甚至更負；與 R1/R2 同根因——bar N 時贏家輸家未分化）
- 把「MH 桶虧損大」當可修漏洞去優化（V26：MH 出場 PnL 恆 ≤0 是 V14 設計使然——正報酬單已被送進延長/TP，MH 是 TP 的入場費非 leak）
- Meta-labeling / ML 分類器進場過濾（V27：23 進場時特徵 × logit/GBM 預測 MH/虧損結局，WF AUC 最佳 0.614 perm p=0.13、OOS 單次確認 0.44~0.58，靜態過濾移除的全是正 PnL +$306~+$702——被標記「最像 MH」的進場多是贏家）
- 任何「預測單筆交易結局」的進場端 ML（V27 同根因：IS 僅 33 個 MH 正樣本 / 23 特徵，訊號不存在且樣本量不支持，更複雜模型只會過擬合更兇）

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
  positions dict: {trade_id: {sub_strategy: "L"/"S", running_mfe, mh_reduced, ...}}
  last_exits: {"L": bar, "S": bar}（各策略獨立 cooldown）
  maxTotal=1 per side（最多 1L+1S）
  V14: L 持倉新增 running_mfe(float) + mh_reduced(bool)，重啟後恢復

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
  啟動 main_eth.py 作為子進程（subprocess），關閉視窗自動停止

  API 端點（所有端點支援 ?mode=paper|live）：
    GET /api/status     → 餘額、持倉、今日PnL、GK pctile、健康度
    GET /api/klines     → 1500 根 K 線 + EMA20 + GK pctile（不分 mode）
    GET /api/trades     → 全部交易記錄
    GET /api/daily      → 每日彙總
    GET /api/analytics  → 收益統計（勝率、PF、equity curve、出場分佈）
    GET /api/bot-status → 機器人運行狀態（PID、running/stopped）
    GET /api/logs       → 日誌檔案最後 N 行（system/signal/alerts）

  路徑切換：
    Paper: eth_state.json + data/
    Live:  eth_state_live.json + data_live/

dashboard/static/:
  index.html — SPA 5 個 tab
  app.js     — 前端邏輯：TradingView LW Charts v4.2 + 表格 + 日誌 + 自動刷新
  style.css  — 深色主題（bg #0f0f1a, card #1a1a2e, green #26a69a, red #ef5350, gold #f0b90b）

  5 個頁面：
    1. 即時狀態：餘額、持倉、今日PnL、GK 壓縮指數（含區間說明）、策略健康度
    2. K 線圖：互動式 K 線 + EMA20 + GK 副圖 + 進出場標記
    3. 交易記錄：可排序/篩選表格，點擊行跳轉圖表
    4. 收益分析：統計卡片、equity curve、每日PnL、出場分佈、策略比較
    5. 系統日誌：即時查看 system.log / signal.log / alerts.log

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
MARGIN_PER_TRADE=200        # 每筆保證金（預設 200；調整此值 = 人肉複利的唯一旋鈕）
LEVERAGE=20                 # 槓桿倍數（預設 20）
```

> **動態風控（2026-07-02 起）**：FEE、日虧、L/S 月虧熔斷線全部以「200U 保證金」為基準
> 隨 `MARGIN_PER_TRADE` **等比縮放**（strategy.py `_RISK_SCALE`）。調保證金不需要改任何程式，
> 風控自動跟上；設 200 時與歷史行為完全相同。
> 人肉複利 SOP（V28 R2 建議形態）：每月 1 號 `保證金 = 200 + 累計獲利×50%`（封頂 1000），
> 挑無持倉時改 .env 並重啟服務；虧回去照公式下調，最低 200。詳見 [doc/v28_research.md](doc/v28_research.md)。

---

## 常用指令

```bash
# 透過 .bat 啟動（推薦，不需手動 activate）
雙擊 start.bat 或 dashboard.bat  # 啟動儀表板 + 機器人
關閉儀表板視窗                    # 自動停止機器人
雙擊 stop.bat                    # 強制停止（備用）

# 或手動（需先 activate）
call .venv\Scripts\activate
python dashboard/app.py         # 啟動儀表板（自動啟動機器人）
python main_eth.py              # 單獨啟動機器人（不開儀表板）
python check_health.py --days 30  # 健康報告

# 終端機看績效 / 開單條件（VPS 無 dashboard 時用；等同 Telegram 指令；依 .env 自動選 data/ 或 data_live/）
python analyze.py               # 收益分析彙總（=/analysis）總損益/WR/PF/最大回撤/出場分佈/L vs S/regime
python analyze.py 30            # 收益分析（最近 30 天）
python analyze.py -t            # 對齊好讀的交易列表（=/trades，最近 20 筆）
python analyze.py -t 50 --live  # 交易列表近 50 筆，強制讀 data_live/
python check_signal.py          # 即時開單條件（=/signal）L/S 每個 gate ✅/❌ + 可開單時段
python menu.py                  # 終端機指令清單（=/help）忘記指令時看這個

# 終端機回測（= 儀表板回測 tab，V14+R+V25-D，可選日期範圍；VPS 上免儀表板）
# 預設「貼近實盤」成交：TP/BE 用市價收盤成交（實盤無 TP 限價單，每小時市價平）；
# SafeNet 維持真實 stop 模型。差異主要在 TP 筆，MH/MFE 兩模式相同。
python run_backtest.py                              # 全期間（貼近實盤）
python run_backtest.py --start 2025-01-01 --end 2025-06-30   # 選日期
python run_backtest.py --ideal                     # 理想化成交（TP 鎖理論價，= 舊版/研究基準）對照
python run_backtest.py --slip 2                     # 加 2bp 市價滑價壓測（高波動更保守）
python run_backtest.py --refresh                   # 先抓最新 K 線再跑

# 回測前補 K 線快取（data/ 被 gitignore，fresh 環境/VPS 需先抓；run_backtest --refresh 會自動補）
python fetch_backtest_data.py   # Binance 公開端點分頁抓 ETH+BTC 1h 730 天 → data/
```

> VPS 維運 / 部署文件（都在 `deploy/`）：
> - **[deploy/cheatsheet.txt](deploy/cheatsheet.txt)** — 日常維運速查（服務控制/日誌/績效/多實例操作）
> - **[deploy/new_vps_setup.md](deploy/new_vps_setup.md)** — 換電腦(git clone)、從零架新 VPS、**更新既有 VPS（轉 git checkout → 之後 `git pull`）**、本版新東西
> - **[deploy/onboarding.md](deploy/onboarding.md)** — 多人：跟對方收集什麼（可轉發）+ 在 VPS 新增一個使用者
> - **[deploy/VPS_DEPLOY.md](deploy/VPS_DEPLOY.md)** — 單人歷史部署紀錄（tar/scp 時代 + 依賴雷）

---

## Telegram 指令（main_eth.py 內建，每 10s 輪詢）

| 指令 | 功能 |
|------|------|
| `/status`（`/pos`） | 倉位同步狀態（內部 vs Binance） |
| `/bal`（`/balance`） | 帳戶餘額 + 未實現損益 |
| `/pnl` | 今日 / 本月 / 近 7 天損益報表 |
| `/analysis [天數]`（`/stats`、`/report`） | 收益分析（WR/PF/最大回撤/出場分佈/L vs S/regime），可帶天數如 `/analysis 30` |
| `/signal`（`/cond`、`/check`） | 即時開單條件檢查（L/S 每個 gate ✅/❌ + 可開單時段 + 即時 GK/regime） |
| `/trades` | 最近 5 筆交易 |
| `/alerts`（`/warn`） | 今日告警日誌 |
| `/cb` | 風控熔斷狀態 |
| `/pause` / `/resume` | 暫停 / 恢復開新倉 |
| `/cleanup`（`/clean`） | 清理孤兒倉位 |
| `/help` | 顯示指令列表 |

> `/pnl` 近 7 天與 `/analysis` 都改讀持久化的 `trades.csv`（`daily_stats` 每日 rollover 會被清空，
> 不能當歷史來源）。`/analysis` 與終端機 `analyze.py` 共用 `analysis_report.py`；
> `/signal` 與 `check_signal.py` 共用 `signal_status.py`，Telegram 與終端機數字一致。

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

> 多數腳本讀 `data/ETHUSDT_1h_latest730d.csv`。`data/` 被 gitignore，**fresh clone / VPS 上要先跑
> `python fetch_backtest_data.py` 補快取**才能執行。

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
- V12: 所有非 GK 的 S 做空進場信號（8 輪 15+ 方向全部不如 V11-E S）
  - 動量衰竭 / RSI 過買 / EMA 過延伸 / MACD 背離 / Donchian（ALL FAILED）
  - 成交量異常 / BB / ATR（有微弱 edge 但只有 V11-E S 的 37-39%）
  - BTC-ETH 背離 + TP+MaxHold（$337，-75%）
  - 複合/Score/GK Expansion 反轉（均不可取）
  - 月相（p>0.3，NOT SIGNIFICANT）

---

## 已知限制 & 注意事項

- **Testnet 行為差異**：MARKET 訂單返回 NEW 非 FILLED、不支持 STOP_MARKET via new_order、closePosition SL 每方向限 1 個
- **Python 不在 PATH**：必須用 `.bat` 或手動 activate `.venv`
- **eth_state.json 勿手動修改**：除非機器人已停止且需要清理錯誤狀態
- **Dashboard port 8050**：啟動時自動清理舊進程，但如果機器人佔用 port 會衝突
- **K 線快取 CSV (~2.5MB)**：data_feed.py 每次從 Binance API 即時抓取，CSV 是離線回測用
