# V87 成交配對與損益橋接（離線匯總器）

`fill_bridge.py` 只讀本機 CSV，不連網、不讀 `.env`、不匯入專案模組、不需要 API 金鑰。規則與判讀寫死在程式內，依據是 `doc/research_results/v87/v87_plan.md` §三、§四（預登記 commit 6b3684a）。

## 用途

輸入是使用者自己在 VPS 用 `extract_fills_readonly.py` 抓到的 `trades.csv`、`orders.csv`，機器人的 `data_live/trades.csv`，以及 1h K 線。輸出只有去識別的逐腿表、逐筆損益橋接表與摘要 JSON，不含 orderId、tradeId、價格、數量或金鑰。研究端只需要摘要 JSON；原始檔留在使用者私人目錄，不進 Git。

## 配對規則（摘要）

1. 逐筆成交依 tradeId 去重，依 orderId 合併（數量和、成交均價、首末成交時間、筆數、手續費、realizedPnl）。同一 orderId 有兩種 side 或 positionSide → 衝突，不配對。
2. 訂單屬性只取 orders.csv：缺訂單列、origType 空白、建單時間空白都不配對；不以首筆成交時間代替建單時間。
3. 市價腿候選：origType＝MARKET、同 side 與 positionSide、建單在 [T, T+30 分]、成交均價與本機紀錄價差 ≤1bp。SafeNet 出場：STOP_MARKET、首筆成交在 T±1 小時、價差 ≤1bp。
4. 全局一對一：腿恰有 1 個候選且該訂單只是 1 條腿的候選才配對；其餘標「模糊」。
5. SafeNet 的建單到觸發是掛單存續時間，單獨報，不併入市價延遲。
6. 同一筆交易進出場數量不等、或本機 `entry_signal_bar_close` 與 K 線收盤差 >1bp → 不入損益橋接。
7. K 線不在整點、重複、缺欄位，或任一已配對市價腿的 T 不在 K 線內 → 停止（結束碼 3），不輸出統計。

## 使用者自行執行（VPS 上，不需金鑰；需先把本分支併入 master 並 `git pull`）

每一行單獨貼上執行：

```
cd ~/cryptoBot
```
```
.venv/bin/python -I backtest/research/v87_fill_bridge/fill_bridge.py --selftest
```
```
.venv/bin/python -I backtest/research/v87_fill_bridge/fill_bridge.py --fills ~/fill_audit_out/m3/trades.csv --orders ~/fill_audit_out/m3/orders.csv --local data_live/trades.csv --klines /home/cryptobot/kl_ms.csv --bar-time open --out ~/fill_bridge_out/m3
```

只把最後一步印出的 JSON 貼回研究端。若印出「停止：……」，只貼那一行。

## 限制

- Funding 沒有查詢（`/fapi/v1/income` 不在提取工具允許清單），本機淨損益與交易所淨（不含 funding）的差列為「未拆分殘差」。
- 漏訊號與拒單無法由這些檔案觀測（需 signal.log／alerts.log，本工具不讀）。
- 2026-08-19 以前的交易已超過 userTrades／allOrders 的查詢期限，會列為「缺候選訂單」。
- 本機 SafeNet 出場紀錄價若不是成交均價，1bp 一致性檢查會使該腿不配對（刻意保守）。
