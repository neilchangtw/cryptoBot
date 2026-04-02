# CryptoBot

BTC/ETH 自動交易機器人（Binance Futures, Testnet/Production）。

**詳細文件在 `doc/` 資料夾，本文件只放摘要和索引。**

---

## 文件索引

| 文件 | 內容 |
|------|------|
| **CLAUDE.md**（本文件） | 專案摘要、目前狀態、架構、最新結論 |
| [doc/backtest_history.md](doc/backtest_history.md) | 所有回測結果的完整記錄（v5系列/趨勢/波動率/FR） |
| [doc/v5_strategy.md](doc/v5_strategy.md) | v5 策略詳細規格（進場/出場/指標/風控） |
| [doc/api_reference.md](doc/api_reference.md) | Binance API 端點參考 |

---

## 目錄結構

```
cryptoBot/
├── main.py              # 入口（Runner + Monitor 兩個 thread）
├── strategy_runner.py   # v5 信號引擎（5m RSI+BB + 過濾）
├── cryptobot_monitor.py # 持倉監控（TP1 全平 + 8h TimeStop）
├── binance_trade.py     # Binance API 下單模組
├── trade_journal.py     # SQLite 交易日誌
├── telegram_notify.py   # TG 通知
├── backtest/            # 回測系統
├── doc/                 # 詳細文件
├── data/                # K線快取
├── .env                 # API 金鑰
├── monitor_state.json   # 持倉狀態
└── trade_journal.db     # 交易日誌
```

---

## 目前狀態

- **運行中**：v5 在 Testnet 跑模擬盤
- **最新回測結論**：見下方

---

## 最新回測結論（2026/04/02）★

### 策略排名（1 年資料，全部嚴格回測）

| # | 策略 | 幣種 | 年PnL | PF | OOS | 特性 |
|---|------|------|-------|-----|------|------|
| **1** | **ETH 波動率 Squeeze<20** | **ETH** | **+$1,620** | **1.28** | **+$786** | **BB收窄突破，最高收益** |
| 2 | BTC 1h Swing+EMA50+FR | BTC | +$997 | 1.27 | +$169 | 趨勢跟隨 |
| 3 | ETH 1h Swing+EMA50+FR | ETH | +$457 | 1.08 | +$629 | ETH趨勢 |
| 4 | BTC v5.2 均值回歸(5m) | BTC | +$229 | 2.24 | 10/10折 | 最穩但賺最少 |
| 5 | BTC D2 TP50%+EMA50(1h) | BTC | +$617 | 1.15 | 滾動5/10 | 混合型 |

### ★ 目前主力：ETH 波動率 Squeeze<20

```
進場：1h BB 寬度百分位 < 20 → 突破 BB Upper 做多 / BB Lower 做空 + Vol>1x
出場：EMA20 trailing + 安全網 ±3%
年 PnL：+$1,362（333筆，深入分析後修正）
OOS(4m)：+$680（PF 1.36）
滾動 WF：+$1,084（6/10 折）
WR 31.2%，RR 2.7:1（W均 +$71 / L均 -$26）
持倉 avg 15h

收益拆解：
  Trail +$2,673 / SafeNet -$1,312 / 手續費 -$533
  SafeNet 佔 Trail 的 49% ← 最大改善空間
```

**SafeNet 優化後（C4 趨勢+min6h）：**
```
1年：309筆 +$1,985, PF1.34, WR34%
OOS: +$675(PF1.37), 滾動: +$1,609(6/10)
改善：全樣本 +46%（+$1,362→+$1,985）
方法：順趨勢(EMA50)做 + 前6h不讓EMA20踢出
```

### 多幣種合計潛力

```
ETH 波動率：+$1,620
BTC Swing趨勢：+$997
合計：+$2,617/年（vs 之前 v5.2 的 +$229）
```

### 排除的方向

- 5m 均值回歸 + STOP_MARKET SL（滑價吃光 edge）
- 資金費率套利（全部虧損，費率可維持極端）
- 多維度過濾（2 個以上 → 過度過濾）
- 等確認進場（追高更差）
- ETH 均值回歸（-$320，不適合）

---

## v5 策略規格（簡版）

```
進場：5m RSI<30 + BB Lower + ATR<=75 + EMA21<2% + 1h RSI方向 + ATR<MA50 + BB寬<50
出場：TP1 1.5x ATR 全平 / 8h TimeStop / 安全網 ±3%
風控：100U保證金 / 20x / Max 2同向
```

完整規格 → [doc/v5_strategy.md](doc/v5_strategy.md)

---

## 系統架構（簡版）

```
main.py → Thread "Runner"（信號+開倉）+ Thread "Monitor"（TP1+TimeStop）
共享：trade_journal.db（SQLite WAL）+ Binance API + monitor_state.json
Session：30分鐘自動重建 + 時間同步
```

---

## 環境設定

```ini
BINANCE_TESTNET=true
BINANCE_API_KEY=<key>
BINANCE_API_SECRET=<secret>
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<id>
SYMBOL=BTCUSDT
MARGIN_PER_TRADE=100
LEVERAGE=20
MAX_SAME_DIRECTION=2
```

---

## 待辦

- [ ] 深入優化 ETH 波動率策略（Squeeze 參數、出場方式）
- [ ] ETH 波動率 Walk-Forward 滾動驗證
- [ ] 多幣種同時運行架構設計
- [ ] v5 → v6 升級（從 5m 均值回歸改為 1h 趨勢/波動率）
- [ ] 正式環境部署

---

## 歷史修復紀錄

v4 時期的 7 個 bug fix + 5 個優化已整合到 v5。
詳見 [doc/backtest_history.md](doc/backtest_history.md)。
