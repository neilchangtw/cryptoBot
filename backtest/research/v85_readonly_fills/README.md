# 實盤成交時間：唯讀提取工具與審查說明（V85 初版，V86 第三版）

狀態：**離線自我測試通過；VPS 查詢未執行、毫秒成交分析未執行、帳戶新回測未執行。** 研究端不執行真實查詢，也不宣稱「已驗證可在 VPS 安全執行」；是否執行由使用者審查後決定。

## 1. 為什麼需要

`data_live/trades.csv` 的 `entry_time_utc`、`exit_time_utc` 由 executor 寫入 K 線的 datetime 減 8 小時，是 K 線時間，不是成交時刻；`entry_price`、`exit_price` 是交易所回傳的平均成交價。orderId 只存在持倉狀態，沒有寫進交易紀錄，毫秒成交時刻也沒保存。V81 只證明 52 筆實盤價格落在整點後第一根 1 分 K 範圍內，這是「時點相容」，**不是成交延遲已驗證**。

## 2. 官方唯讀端點（2026-11-15 讀 developers.binance.com）

| 端點 | 權重 | 時間限制 | 保留期限 | 本工具 |
|---|---|---|---|---|
| GET /fapi/v1/time | 1 | 公開、不簽名 | — | 使用（校正簽名時間戳） |
| GET /fapi/v1/userTrades | 5 | startTime～endTime ≤ 7 天；fromId 不可與時間同用；limit 最多 1000 | 只支援近 3 個月 | 使用 |
| GET /fapi/v1/allOrders | 5 | 查詢期間 < 7 天；limit 最多 1000 | 建立超過 90 天的訂單查不到；已取消或過期且無成交的訂單 3 天後查不到 | 使用 |
| GET /fapi/v1/order | 1 | 需 orderId | 同上 | 不使用、程式拒絕 |
| GET /fapi/v1/algoOrder | 1 | 需 algoId | 同上 | 不使用、程式拒絕（SafeNet 觸發後的成交會出現在 userTrades） |
| GET /fapi/v1/trade/asyn（成交歷史匯出） | 1000 | 單次最長 1 年 | 每月 5 次，與網頁下載共用；連結 7 天失效 | **不使用、程式拒絕**，見 §8 |
| GET /fapi/v1/order/asyn（訂單歷史匯出） | 1000 | 單次最長 1 年 | 每月 10 次，與網頁下載共用 | **不使用** |

官方未寫明回應時間欄的單位（範例為 13 位數，符合毫秒），也未寫明匯出檔的欄位與時間精度。`check` 會印出 `time位數` 讓使用者確認。

每個 7 天窗口用 2 次權重 5 的呼叫；3 個月約 14 個窗口、約 140 權重，遠低於每分鐘 2,400 的帳戶上限。

**覆蓋估算**：以 2026-11-15 執行，近 3 個月約從 2026-08-15 起，約 26／55 筆可用一般查詢取得；其餘約 29 筆只能靠匯出，每晚一天執行就少一些。

## 3. 工具做什麼、不做什麼

- 只用 Python 標準函式庫（argparse、csv、hashlib、hmac、json、os、re、sys、time、urllib、datetime；selftest 另用 io、tempfile、contextlib、email.message、urllib.response）。不匯入 binance_trade、executor、strategy、requests 或任何專案模組，不啟動交易流程。
- 網路目的地只有 `https://fapi.binance.com`；允許清單 `ALLOWED` 只有上表三個「使用」的 GET。其他路徑丟 PermissionError；程式內沒有 POST、PUT、DELETE，沒有子程序。
- 標的固定 ETHUSDT。
- 金鑰只從環境變數 `BINANCE_API_KEY`、`BINANCE_API_SECRET` 讀。V86 已移除讀 `.env` 的選項。不寫檔、不印出。
- 錯誤只印「路徑 + HTTP 狀態碼 + Binance 錯誤碼」或網路錯誤類別，不印網址、簽章或回應全文。
- **任何 HTTP 重導向（301／302／303／307／308）一律拒絕**：用自訂的 `RefuseRedirect` 取代 Python 預設的重導向處理，在發第二個請求前就丟錯，所以 `Location` 指向的網址收不到金鑰標頭或簽章。回應的最終網址若不是 fapi.binance.com 也拒絕。（V86 第二版用的是預設 `urlopen`，會跟隨重導向並把金鑰與簽章帶過去；selftest 第 03b 項保留這個對照組。）
- 不自動重試。每個窗口完成就寫一個檢查點（含窗口起訖、工具 sha256、內容 sha256）；中斷後用同一個 `--run` 加 `--resume` 只補沒完成的窗口。續跑時若工具版本不同、檢查點損壞或被改、窗口不符、或目錄裡多出不屬於此範圍的檔案，一律拒絕，不混用。
- 單一窗口回傳達 1,000 筆時切半重查，最小到 1 分鐘仍滿就停止，避免截斷。合併時以 `id`（成交）與 `orderId`（訂單）去重，部分回應重疊不會重複。
- 輸出目錄若在任何 git 工作樹內就拒絕（以實際路徑判斷）。路徑任何一段是符號連結就拒絕；`--run` 必填，只能是 1～64 個英數字、底線、點或連字號，不得含 `/`、`\` 或 `..`。寫檔用 `O_EXCL`＋`O_NOFOLLOW` 原子寫入（先寫 .tmp 再改名），目標或暫存檔是連結時不會寫到連結目標。Linux／macOS 上目錄 700、檔案 600；Windows 沒有 POSIX 權限位元，selftest 第 16 項會明確顯示 SKIP，Linux 上必須是 PASS。每次執行另寫 `SHA256SUMS.txt`。
- 只保存 `TRADE_FIELDS`、`ORDER_FIELDS` 列的欄位；不保存原始回應、clientOrderId 或帳戶資訊。

**建議另建一把只開「讀取」權限的 API 金鑰**，可加 IP 白名單只允許 VPS。不要用實盤下單那把，也不要把金鑰交給研究端。

## 4. 審查重點

1. `grep -nE "POST|PUT|DELETE|subprocess|os.system|import binance|import executor|import strategy|requests" extract_fills_readonly.py`：只會出現在說明文字與 selftest 的檢查清單裡。
2. `ALLOWED` 只有 3 個路徑；`BASE` 是唯一網址。
3. `write_private()` 是唯一寫檔函式，`prep_out()` 拒絕 git 工作樹。
4. `run_check()` 只讀 `trades.csv`，印出的摘要不含價格、數量或識別碼。

## 5. 使用者執行步驟（在 VPS 親自執行；研究端不執行）

前提：本工具在 `claude/v86-research` 分支，尚未 push；要在 VPS 用，必須先由你決定 push 或手動複製這個檔案。**不要在 `~/cryptoBot` 切換分支**，以免影響運行中的服務。建議只把單一檔案複製到專案外：

```bash
mkdir -p ~/fill_audit && cd ~/fill_audit
# 把 extract_fills_readonly.py 放到這裡（手動貼上或 scp），然後核對雜湊，應與研究端公布的一致
sha256sum extract_fills_readonly.py

# 1) 離線自我測試（不連網）
python3 -I extract_fills_readonly.py selftest
#    成功：19 項逐項印出 PASS，最後一行「結果：19 PASS、0 SKIP、0 FAIL」，結束碼 0。
#    Linux 上有任何 FAIL 或 SKIP：停止，不要往下做。

# 2) 輸入唯讀金鑰（不進 shell 歷史、不顯示在螢幕）
read -rs BINANCE_API_KEY; echo; read -rs BINANCE_API_SECRET; echo
export BINANCE_API_KEY BINANCE_API_SECRET

# 3) 最近 7 天小樣本
python3 -I extract_fills_readonly.py fetch --sample --run sample1

# 4) 與自己的交易紀錄比對（唯讀），只印去識別摘要
python3 -I extract_fills_readonly.py check --run sample1 --trades ~/cryptoBot/data_live/trades.csv

# 5) 用完清除
unset BINANCE_API_KEY BINANCE_API_SECRET
```

### 預期輸出

`fetch` 成功時：

```
輸出：/home/cryptobot/fill_audit_out/sample1（窗口 新查 1、跳過 0；本機與伺服器時差 -35 ms）
  trades.csv：N 筆  sha256 ……
  orders.csv：M 筆  sha256 ……
呼叫紀錄（只含路徑與參數名稱）： [('/fapi/v1/allOrders', (...)), ('/fapi/v1/time', ()), ('/fapi/v1/userTrades', (...))]
```

最近 7 天如果沒有成交，CSV 只有表頭，也是有效結果（代表呼叫與格式可用），但無法驗證欄位，要等有成交的窗口。

`fetch` 失敗時（結束碼 2）：`失敗：/fapi/v1/userTrades HTTP 401，Binance 錯誤碼 -2015。已完成的窗口保留在檢查點……`。常見碼：-2015 金鑰、IP 或權限不符；-1021 時間戳超出 recvWindow（工具已用伺服器時間校正，仍出現代表本機時鐘跳動）；-1003 或 HTTP 429 請求過多（停 1 分鐘後用 `--resume`）；HTTP 418 IP 被封（停止，不要重跑）。

`check` 輸出的欄位：

| 欄位 | 意義 | 期待值 |
|---|---|---|
| 成交筆數、訂單筆數 | 窗口內筆數 | 和 Binance 網頁對得上 |
| 欄位齊全 | id、orderId、time、side、positionSide、price、qty、commission 是否每筆都有 | 全部 true |
| time位數、time看起來是毫秒 | 回應時間欄位數 | [13]、true |
| 成交都有對應訂單 | 每筆成交的 orderId 是否在 allOrders 裡 | true |
| 部分成交訂單數 | 同一 orderId 多筆成交 | 任意 |
| 本機交易紀錄筆數、配對到的進出場腿、多重候選腿 | 用 positionSide、side、紀錄時間前 1 小時到後 2 小時配對 | 多重候選應為 0 |
| 逐腿摘要 | 每腿：訂單建立減紀錄時間（分）、建立到最後成交（ms）、紀錄時間到最後成交（ms）、成交筆、紀錄價對交易所均價（bp）、訂單類型 | 見下 |

判讀：
- 「訂單建立減紀錄時間」若約 60 分，代表 trades.csv 的時間是 K 線開盤；若約 0 分，代表是收盤。這是本輪要確認的第一件事。
- 「紀錄價對交易所均價」應接近 0bp；明顯不為 0 代表紀錄價不是成交均價。
- 「建立到最後成交」是交易所接單到成交的毫秒。
- 觸發單（STOP_MARKET）的建立時間在進場當下，所以只會配到進場附近，不代表出場延遲。

### 要交回研究端的內容

**只貼 `check` 印出的 JSON 摘要。** 不要貼 trades.csv、orders.csv、金鑰、網址或錯誤全文。摘要內只有筆數、毫秒差、bp 差與欄位旗標，沒有價格、數量、orderId 或 tradeId。若你願意提供完整 CSV 給研究端做 §7 的分析，那是你另外的決定；研究端不要求。

## 6. 小樣本成功後才做：近 3 個月

```bash
python3 -I extract_fills_readonly.py fetch --start 2026-08-16 --end 2026-11-15 --run m3
# 中斷或報錯後：
python3 -I extract_fills_readonly.py fetch --run m3 --resume      # 不需再給起訖，沿用 run.json
python3 -I extract_fills_readonly.py check --run m3 --trades ~/cryptoBot/data_live/trades.csv
cd ~/fill_audit_out/m3 && sha256sum -c SHA256SUMS.txt     # 檔案校驗
```

開始日期請改成執行當天往前 90 天以內；超過時工具會提醒，較早的部分不會回傳。續跑時沿用 run.json 記錄的原始起訖，不能改範圍。

## 7. 收到資料後的分析方法（事前固定，fill_latency_analysis.py，V86 依使用者審查修訂）

1. 清洗：tradeId 去重；有 symbol 欄時只留 ETHUSDT；價格或數量不大於 0 剔除；剔除數都報出。同一 orderId 出現多個 side 或 positionSide 另計。
2. 訂單只依 origType 分類：MARKET＝市價單；STOP_MARKET、TAKE_PROFIT_MARKET＝觸發單；其他（LIMIT 等）＝其他類型，不進統計只計數。
3. 訊號時間 T 取自本機 trades.csv 的紀錄時間，不從訂單時間推定；`--bar-time open|close` 必填，由 §5 小樣本的「訂單建立減紀錄時間」決定（open＝紀錄時間＋1 小時）。
4. 配對：同 positionSide、同 side；一般進出場配市價單且訂單建立在 [T, T+30 分]；SafeNet 出場配觸發單且第一筆成交在 [T±1 小時]。0 個或多個候選都不配對，分別標「缺資料」「多重候選」；重試、延遲、手動或限價單會留在「未配對訂單」依類型計數，不會被配到錯的訊號。
5. 延遲分段：(a) K 線收盤到取得資料、(b) 醒來到送單 **沒有本機時間戳，標「未驗證」**，只報合計 T→訂單建立；(c) 交易所訂單建立→第一筆與最後一筆成交；另報 T→最後成交。
6. 方向性價差：市價單對 T 收盤、SafeNet 對 stopPrice；買單正號、賣單負號，正值對策略不利。K 線檔先驗證收盤都落在整點、無重複，P_T 缺值另計。
7. 分組：進場／出場 × 多／空、出場類型、月份、波動（T 時 ATR14% 樣本內三分位），另列最大最小各 5 腿。
8. 輸出只能寫到專案外、尚不存在的目錄（拒絕 git 工作樹與覆寫），700／600；逐腿檔不含 orderId、tradeId、價格或數量。
9. 55 筆實盤是已看過的 2026 樣本，只代表其期間與配置，不是樣本外證據；F0（T 收盤）與 F2（延後 15 分）不是成交價上下界。

自我測試：`python3 -I fill_latency_analysis.py --selftest`（合成資料：重複與零數量、限價單、晚 75 分重試、SafeNet、git 目錄、既有目錄、不覆寫，研究端已通過）。

## 8. 歷史匯出（只列可行性，不啟動）

要補 2026-06～08 中的約 29 筆，只能用成交歷史匯出（每月 5 次，與網頁下載共用，權重 1000，單次最長 1 年，連結 7 天失效）。官方沒寫匯出檔欄位與時間精度，未實測前不能宣稱可涵蓋全部 55 筆。本工具拒絕呼叫匯出端點；是否在網頁上手動申請一次由使用者決定，研究端不提出使用。
