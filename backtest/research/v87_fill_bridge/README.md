# V87 成交配對與損益橋接（離線匯總器，第二版）

`fill_bridge.py` 只讀使用者指定的本機一般檔案，產生三樣東西：去識別的逐腿表、逐筆損益橋接表、摘要。配對規則寫死在程式內，依據 `doc/research_results/v87/v87_plan.md` §三、§四（預登記 commit 6b3684a）。第二版修正了第一版（3c11426，sha256 da96dd4a…c583）的五個問題，見 `v87_results.md` §8。配對規則與 1bp 容差都沒有改。

**狀態**：只在合成資料上測試過，實盤資料未執行，研究端沒有任何真實成交的橋接數字。

## 檔案

| 檔案 | 用途 |
|---|---|
| `fill_bridge.py` | 匯總器本身，內含 `--selftest`（25 項） |
| `test_fill_bridge_cli.py` | 命令列端到端測試，以獨立行程執行匯總器（23 項）。只有這支測試檔使用 subprocess |
| `synthetic/*.csv` | 固定的合成輸入，不是實盤資料。可用 `--write-synthetic` 重建，再以 `git diff` 比對 |

## 依賴與匯入（以 AST 列出）

- Python 3 加 numpy、pandas。研究端在 Python 3.13.18 下用 pandas 3.0.5／numpy 2.5.3 與 pandas 2.2.3／numpy 2.1.3 兩組各測一次；VPS `.venv` 的版本未回報。
- `fill_bridge.py` 匯入：argparse、hashlib、io、json、math、os、platform、re、stat、sys、numpy、pandas；selftest 內另匯 tempfile。
  - 不匯入專案或交易模組（binance_trade、executor、strategy、main_eth、paths），也不匯入 requests、urllib、socket、http、subprocess。
  - pandas 匯入時會自己載入 `subprocess` 模組。本檔不呼叫它；selftest 在分析與寫檔期間把 `subprocess.Popen` 與 `os.system` 換成會報錯的函式，以此證明沒有開子程序。
- `test_fill_bridge_cli.py` 匯入：hashlib、importlib.util、json、os、shutil、subprocess、sys、tempfile、numpy、pandas。
  - 用 subprocess 執行受測匯總器，並用 `git show 3c11426:…` 取出修正前的版本做對照。

## 網路

- 匯總器不使用網路。四個輸入在讀取前先做兩項檢查：
  - 字串檢查：拒絕 `://`、任何 `scheme:` 開頭（Windows 的 `C:\` 除外）、空白與 `-`。
  - `lstat` 檢查：不存在、符號連結、非一般檔案都拒絕。
- 通過檢查後以 `O_NOFOLLOW` 開檔，`fstat` 再確認一次是一般檔案，整檔讀成位元組。pandas 只拿到記憶體中的 `BytesIO`，不會拿到路徑或 URL。
- 端到端測試在守衛下逐一把四個參數換成 https、http、file URL，結果都是結束碼 2，守衛沒有被觸發，也沒有建立輸出。
- 守衛把 socket 連線、DNS、`subprocess.Popen`、`os.system`、`os.posix_spawn` 換成會留紀錄並報錯的函式。
- 正向對照：把同一個 URL 交給修正前版本，守衛被觸發，代表 pandas 真的嘗試連網，也代表守衛有效。

## 檔案讀寫與例外路徑（逐檔）

**`fill_bridge.py` 正式執行**
- 讀：
  - 四個輸入檔（唯讀）
  - 自身檔案，用來計算工具 SHA-256
- 寫：
  - 先在記憶體算完分析，並把 JSON 與兩份 CSV 都序列化好，之後才碰檔案系統。
  - 建立 `<out>.incomplete`（700）。三檔逐一以 `O_CREAT|O_EXCL|O_NOFOLLOW` 建立（600）並 fsync，全部完成後改名為 `<out>`。
  - `<out>` 的上層目錄不存在時，以 700 建立。
- 不覆寫：
  - `<out>` 或 `<out>.incomplete` 已存在，就拒絕。
  - 改名前一刻會再檢查一次 `<out>`。這一刻與改名之間仍有極短的空窗，在使用者私人目錄內可接受。
- 失敗時：寫到任一檔失敗，就刪掉本次建立的檔案與暫存目錄，不留下完成目錄。可能覆寫的檔案：無。

結束碼與例外路徑：

| 結束碼 | 情況 | 檔案系統 |
|---|---|---|
| 0 | 完成；JSON 印到 stdout | 只多出 `<out>` 與其中三檔 |
| 2 | 輸入被拒（URL／scheme、不存在、目錄、符號連結、空白）；argparse 參數錯誤也是 2 | 未建立任何東西 |
| 3 | 資料前提不成立（K 線不整點／重複／缺欄位／缺必要 T、bar-time、方向無法判讀、缺必要欄位、CSV 無法解析） | 未建立任何東西 |
| 4 | 輸出被拒（git 工作樹內、已存在、路徑含符號連結）或寫檔失敗 | 不留下 `<out>`；寫一半時暫存目錄也會刪除 |

**`--selftest`**
- 在 `tempfile.mkdtemp()`（通常是 `/tmp`）下建立：測試目錄、假 `.git`、符號連結、測試輸出。
- 不碰其他位置，也不刪除這些測試檔。

**`test_fill_bridge_cli.py`**
- 把 `synthetic/` 複製到暫存目錄後執行，結束時刪除整個暫存目錄。
- 另有一個案例會檢查 `<repo>/fb_should_not_exist`，確認它不會被建立。
- `--write-synthetic` 會覆寫 `synthetic/` 內的六個合成檔。

## 輸入

| 參數 | 內容 | 必要欄位 |
|---|---|---|
| `--fills` | 提取工具的 `trades.csv`（userTrades） | time、id、orderId、side、positionSide、price、qty、commission、commissionAsset、realizedPnl |
| `--orders` | 提取工具的 `orders.csv`（allOrders） | orderId、time、side、positionSide、origType、stopPrice |
| `--local` | 機器人 `data_live/trades.csv` 的**唯讀快照** | entry_time_utc、exit_time_utc、direction、entry_price、exit_price、exit_type、net_pnl_usd；有 entry_signal_bar_close 時一併核對 |
| `--klines` | ETHUSDT 1h K 線 | open_time（毫秒）或 datetime（UTC+8 開盤字串），以及 close |
| `--klines-source` | 選填，K 線來源說明 | 只寫進 summary.json |
| `--bar-time` | open 或 close，必填 | 本機紀錄時間是 K 線開盤還是收盤 |
| `--out` | 尚不存在的新目錄 | 不得在 git 工作樹內、路徑不得含符號連結 |

`data_live/trades.csv` 是機器人持續寫入的檔。不要直接讀它，先由使用者在私人目錄做唯讀快照並記下雜湊。匯總器會把快照的 SHA-256 寫進 summary.json，兩者可以互相核對。

## 輸出（`<out>` 內三檔）

| 檔案 | 內容 |
|---|---|
| `legs_deidentified.csv` | 逐腿：序號、腿、方向、出場類型、月份、狀態（相容配對／未配對）、原因、訂單類型、價差參考、成交筆、建單−T／首筆−T／末筆−T／建單到末筆（毫秒）、掛單存續（SafeNet）、方向性 bp、價差成本美元、實際名目美元、參考名目美元、手續費USDT、手續費狀態、realizedPnl狀態 |
| `bridge_deidentified.csv` | 逐筆：序號、月份、方向、出場類型、出場參考（T收盤價／停損價）、實際毛損益、參考價反事實毛損益、價差美元、進場／出場價差成本美元、進場／出場手續費USDT、手續費USDT、淨損益狀態、funding（固定「未取得」）、交易所淨（不含 funding）、本機淨、未拆分殘差、realizedPnl 核對差、缺失成本 |
| `summary.json` | 執行資訊＋彙總；同樣內容印到 stdout |

**執行資訊**包含：
- 工具檔名與 SHA-256
- 四個輸入的檔名、SHA-256、位元組數（只記檔名，不記完整路徑）
- Python、pandas、numpy 版本
- bar-time
- K 線來源說明，以及 K 線覆蓋期間（根數、首末根收盤 UTC）

JSON 的鍵一律是字串。市價腿統計是 `[{"腿": "進場", "方向": "LONG", …}]` 這種清單，不再用 tuple 當鍵。NaN 一律輸出為 `null`，並以 `allow_nan=False` 強制檢查。

**隱私**
- 輸出不含 orderId、tradeId、單筆成交價、均價、數量欄或金鑰。
- 逐腿**名目美元**加上公開 K 線，可以推回約略的部位數量。分享逐腿檔之前請自行決定；研究端只需要 `summary.json`。
- 原始成交、訂單、實盤交易紀錄與金鑰都不進 Git。

## 口徑

**相容配對**
- 同時符合時間窗、方向、持倉方向、價格 ≤1bp，且在全局一對一下唯一的腿，稱為「相容配對」，不稱「已配對」或「已核對」。
- 本機 trades.csv 沒有 orderId 與數量，所以無法證明那張訂單是機器人下的，手動單的身份可能無法排除。

**參考價**
- 市價腿用 T 的 K 線收盤價。
- SafeNet 出場用停損價，標「出場參考＝停損價」，不稱為收盤價反事實。
- 欄名統一為「參考價反事實毛損益」。

**手續費**
- 每筆成交的 commission 都要是有效數值，commissionAsset 也要非空白。
- 一張訂單所有成交的資產**全是 USDT**，才算「USDT」，加總進手續費USDT。
- 其他情況標「非USDT（X）」「混合資產（…）」或「缺值或無效」，該腿手續費USDT留空。

**USDT 淨損益**
- 進出場兩腿手續費都是 USDT，該筆才有交易所淨（不含 funding）與未拆分殘差，並標「已驗證（USDT 手續費完整，不含 funding）」。
- 否則標「未驗證」，這兩欄留空，不當作 0。
- 摘要的「USDT淨損益」只加總已驗證筆，並分別列出已驗證筆與未驗證筆的筆數。
- 「價格橋接」（實際毛損益、參考價反事實、價差美元）照列所有進橋接的交易。

**其他缺值**
- realizedPnl 有任一筆缺值，該訂單的核對差留空，標「realizedPnl 缺值」。
- funding 一律「未取得」。
- 未拆分殘差＝本機淨−交易所淨（不含 funding），包含 funding、四捨五入與其他差異，**不是** funding 本身。

**重算公式**
- 方向性 bp（正值＝對策略不利）＝(實際名目 ÷ 參考名目 − 1) × 1e4，買單取正、賣單取負。
- 價差成本美元＝參考名目 × bp ÷ 1e4。
- 橋接價差美元＝實際毛損益 − 參考價反事實毛損益＝−(進場＋出場價差成本美元)。
- 交易所淨（不含 funding）＝實際毛損益 − 手續費USDT。

## 配對規則（固定，與第一版相同）

1. 逐筆成交依 tradeId 去重，依 orderId 合併。同一 orderId 出現兩種 side 或 positionSide，視為衝突，不配對。
2. 訂單屬性只取自 orders.csv。缺訂單列、origType 空白、建單時間空白，都不配對。不以首筆成交時間代替建單時間，也不以 type 代替 origType。
3. 候選條件：
   - 市價腿：origType＝MARKET、同 side 與 positionSide、建單在 [T, T+30 分]、成交均價與本機紀錄價差 ≤1bp。
   - SafeNet 出場：origType＝STOP_MARKET、首筆成交在 T±1 小時、價差 ≤1bp。
4. 全局一對一：腿恰有 1 個候選，且該訂單只是 1 條腿的候選，才算相容配對。其餘標「模糊」，不猜。
5. SafeNet 從建單到觸發的時間是掛單存續，單獨報，不併入市價成交延遲。
6. 下列情況不入損益橋接：同一筆交易進出場數量不等；本機 `entry_signal_bar_close` 與 K 線收盤價差 >1bp。
7. 下列情況停止（結束碼 3），不輸出統計：K 線不在整點、重複、缺欄位；任一相容配對市價腿的 T 不在 K 線內。

**1bp 容差的性質**：這是看過使用者回報（50 腿紀錄價對交易所均價 0.0bp）之後訂的工程容差，不是事前依據，本版沒有為了結果調整。換一批資料前應重新審查。

## 重跑測試（研究端已執行）

```
python3 -I backtest/research/v87_fill_bridge/fill_bridge.py --selftest
```
```
python3 -I backtest/research/v87_fill_bridge/test_fill_bridge_cli.py
```

完整輸出見 `doc/research_results/v87/cli_test_output.txt`。

## VPS 上的執行命令：尚未提供

依使用者指示，修訂版要先通過獨立審查，研究端才會提出精確命令，由使用者親自在 VPS 專案目錄外執行。到時預計的步驟如下，先列出供審查，**不是可直接貼上的命令**：

1. 在 VPS 專案目錄外建立私人目錄，權限 700。
2. 用 `git show <審查通過的 commit>:backtest/research/v87_fill_bridge/fill_bridge.py` 取出單一檔案，核對 SHA-256 等於審查公布值。
3. 在私人目錄建立 `data_live/trades.csv` 的唯讀快照（chmod 400），記下 SHA-256。
4. 準備 K 線檔。第一版 README 寫的 `/home/cryptobot/kl_ms.csv` **尚未核實是否存在、內容是否正確**，不能當作已存在；到時要另外確認路徑、欄位、覆蓋期間與雜湊。
5. 用專案 `.venv` 的 Python 以 `-I` 先跑 `--selftest`，再跑正式命令，並以 `--klines-source` 註明 K 線來源。
6. 只把 stdout 的 JSON 貼回研究端；若出現「拒絕：」或「停止：」，只貼那一行。

## 限制

- Funding 沒有查詢（`/fapi/v1/income` 不在提取工具允許清單）。
- 漏訊號與拒單無法由這些檔案觀測（需要 signal.log／alerts.log，本工具不讀）。
- 2026-08-19 以前的交易超過 userTrades／allOrders 的查詢期限，會列為「缺候選訂單」。
- 本機 SafeNet 出場紀錄價若不是成交均價，1bp 檢查會讓該腿不配對（刻意保守）。
- 相容配對不能證明訂單身份。
- 25 筆是已曝光的 2026 樣本，不外推到 2021～2026。
