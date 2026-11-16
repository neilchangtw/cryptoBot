# V87 成交配對與損益橋接（離線匯總器）

`fill_bridge.py` 只讀使用者指定的本機 CSV，算出去識別的逐腿表、逐筆損益橋接表與摘要。規則寫死在程式內，依據 `doc/research_results/v87/v87_plan.md` §三、§四（預登記 commit 6b3684a）；審查交付版的輸出欄位補充見 `v87_results.md` §7。

**狀態**：只在合成資料上測試過。實盤資料未執行，研究端沒有任何橋接數字。

## 依賴

- Python 3（研究端測試：Python 3.13.18、pandas 3.0.5、numpy；VPS 的 `.venv` 版本未回報）
- 第三方套件只有 numpy、pandas
- 匯入清單（以 AST 列出）：argparse、json、os、sys、numpy、pandas；selftest 內另匯 tempfile
- **不匯入**專案或交易模組（binance_trade、executor、strategy、main_eth、paths 等），不匯入 requests、urllib、socket、subprocess
- 不使用網路、不開子程序、不讀 `.env`、不讀環境變數中的金鑰

## 輸入（全部唯讀）

| 參數 | 內容 | 必要欄位 |
|---|---|---|
| `--fills` | 提取工具的 `trades.csv`（userTrades） | time、id、orderId、side、positionSide、price、qty、commission、commissionAsset、realizedPnl |
| `--orders` | 提取工具的 `orders.csv`（allOrders） | orderId、time、side、positionSide、origType、stopPrice |
| `--local` | 機器人 `data_live/trades.csv` | entry_time_utc、exit_time_utc、direction、entry_price、exit_price、exit_type、net_pnl_usd；有 entry_signal_bar_close 時一併核對 |
| `--klines` | ETHUSDT 1h K 線 | open_time（毫秒）或 datetime（UTC+8 開盤字串），以及 close |
| `--bar-time` | open 或 close，必填 | 本機紀錄時間是 K 線開盤還是收盤 |
| `--out` | 尚不存在的新目錄 | 不得在 git 工作樹內、路徑不得含符號連結 |

## 輸出與寫檔清單

正式執行只寫這 3 個檔，都在 `--out` 新目錄內（目錄 700、檔案 600）：

| 檔案 | 內容 |
|---|---|
| `legs_deidentified.csv` | 逐腿：序號、腿、方向、出場類型、月份、狀態、原因、訂單類型、價差參考、成交筆、建單−T／首筆−T／末筆−T／建單到末筆（毫秒）、掛單存續（SafeNet）、方向性 bp、價差成本美元、實際名目美元、參考名目美元、手續費、手續費已驗證 |
| `bridge_deidentified.csv` | 逐筆：序號、月份、方向、出場類型、實際毛損益、收盤價反事實毛損益、價差美元、進場／出場價差成本美元、進場／出場手續費、手續費、手續費已驗證、funding（固定「未取得」）、交易所淨（不含 funding）、本機淨、未拆分殘差、realizedPnl 核對差、缺失成本 |
| `summary.json` | 彙總；同樣內容印到 stdout |

- **可能覆寫的檔案：無**。目錄已存在就拒絕；每個檔以 `O_CREAT|O_EXCL|O_NOFOLLOW` 建立，已存在即失敗。
- 不含 orderId、tradeId、單筆成交價、均價、數量欄或金鑰。**會**輸出每腿名目美元（數量×價格），供審查者重算；搭配公開 K 線可推回大約的部位數量，請自行決定是否分享逐腿檔。研究端只需要 `summary.json`。
- `--selftest` 會在系統暫存目錄（`tempfile.mkdtemp()`，通常是 `/tmp`）建立測試目錄、一個假的 `.git` 目錄、一個符號連結與兩個測試檔，不碰其他位置，也不刪除。

## 可獨立重算的口徑

- 方向性 bp（正值＝對策略不利）＝(實際名目 ÷ 參考名目 − 1) × 1e4，買單取正、賣單取負。參考價：市價腿用 T 的 K 線收盤價，SafeNet 用停損價。
- 價差成本美元＝參考名目 × bp ÷ 1e4（正值＝成本）。
- 橋接價差美元＝實際毛損益 − 收盤價反事實毛損益＝−(進場＋出場價差成本美元)。
- 交易所淨（不含 funding）＝實際毛損益 − 進場手續費 − 出場手續費。
- 未拆分殘差＝本機淨 − 交易所淨（不含 funding），內含 funding、四捨五入與其他差異。
- 缺失成本逐筆標示：funding 一律「未取得」；手續費非 USDT 標「未換算」；出場參考價缺另標。

## 配對規則（固定）

1. 逐筆成交依 tradeId 去重，依 orderId 合併。同一 orderId 有兩種 side 或 positionSide → 衝突，不配對。
2. 訂單屬性只取 orders.csv：缺訂單列、origType 空白、建單時間空白都不配對；不以首筆成交時間代替建單時間，也不以 type 代替 origType。
3. 市價腿候選：origType＝MARKET、同 side 與 positionSide、建單在 [T, T+30 分]、成交均價與本機紀錄價差 ≤1bp。SafeNet 出場：STOP_MARKET、首筆成交在 T±1 小時、價差 ≤1bp。
4. 全局一對一：腿恰有 1 個候選且該訂單只是 1 條腿的候選才配對；其餘標「模糊」，不猜。
5. SafeNet 的建單到觸發是掛單存續時間，單獨報，不併入市價成交延遲。
6. 同一筆交易進出場數量不等、或本機 `entry_signal_bar_close` 與 K 線收盤差 >1bp → 不入損益橋接。
7. K 線不在整點、重複、缺欄位，或任一已配對市價腿的 T 不在 K 線內 → 停止（結束碼 3），不輸出統計。

**1bp 容差的性質**：這是看過使用者回報（50 腿紀錄價對交易所均價 0.0bp）之後訂的工程容差，不是事前依據；換一批資料前應重新審查。

## 在 VPS 專案目錄外執行的命令草案（本輪未執行）

只把這一支審查過的檔案拿到專案外的私人目錄，用專案 `.venv` 的 Python 跑。不需要金鑰，不查 Binance，不動機器人。每一行單獨貼上：

```
mkdir -m 700 ~/fill_bridge_tool
```
```
cd ~/cryptoBot
```
```
git fetch origin claude/v87-research
```
```
git -C ~/cryptoBot show FETCH_HEAD:backtest/research/v87_fill_bridge/fill_bridge.py > ~/fill_bridge_tool/fill_bridge.py
```
```
chmod 600 ~/fill_bridge_tool/fill_bridge.py && sha256sum ~/fill_bridge_tool/fill_bridge.py
```
（雜湊必須等於審查時公布的值，不同就停止。）
```
cd ~/fill_bridge_tool
```
```
~/cryptoBot/.venv/bin/python -I fill_bridge.py --selftest
```
```
~/cryptoBot/.venv/bin/python -I fill_bridge.py --fills ~/fill_audit_out/m3/trades.csv --orders ~/fill_audit_out/m3/orders.csv --local ~/cryptoBot/data_live/trades.csv --klines /home/cryptobot/kl_ms.csv --bar-time open --out ~/fill_bridge_out/m3
```

`git fetch` 只更新 `~/cryptoBot/.git` 內的遠端參照與 FETCH_HEAD，不改工作目錄的檔案、不切分支。只把最後一步印出的 JSON 貼回研究端；若印出「停止：……」，只貼那一行。

## 限制

- Funding 沒有查詢（`/fapi/v1/income` 不在提取工具允許清單）。
- 漏訊號與拒單無法由這些檔案觀測（需 signal.log／alerts.log，本工具不讀）。
- 2026-08-19 以前的交易超過 userTrades／allOrders 的查詢期限，會列為「缺候選訂單」。
- 本機 SafeNet 出場紀錄價若不是成交均價，1bp 一致性檢查會讓該腿不配對（刻意保守）。
- 25 筆是已曝光的 2026 樣本，不外推到 2021～2026。
