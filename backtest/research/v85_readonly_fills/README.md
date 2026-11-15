# 實盤成交時間：唯讀提取方案與審查說明（V85）

狀態：**工具已寫好並通過離線自我測試；VPS 查詢未執行、毫秒成交分析未執行**（等使用者審查並自行執行後提供輸出）。

## 1. 為什麼需要

現有 `data_live/trades.csv` 的 `entry_time_utc`／`exit_time_utc` 是 K 線時間，不是成交時刻；`entry_price` 是交易所回傳的平均成交價。已平倉交易的 orderId 只暫存在持倉狀態裡，沒有寫進交易紀錄，毫秒成交時刻也沒有保存。V81 只證明 52 筆實盤成交價都落在整點後第一根 1 分 K 的價格範圍內，這是「時點相容」，**不是成交延遲已驗證**。

## 2. 官方唯讀端點（2026-11-15 讀 developers.binance.com）

| 端點 | 權重 | 時間限制 | 保留期限 | 本工具 |
|---|---|---|---|---|
| GET /fapi/v1/userTrades | 5 | 不帶時間預設近 7 天；startTime～endTime 不可超過 7 天；fromId 不可與時間同用；limit 最多 1000 | 只支援近 3 個月 | 使用 |
| GET /fapi/v1/allOrders | 5 | 查詢期間小於 7 天 | 已取消或過期且無成交的訂單 3 天後查不到；建立超過 90 天的訂單查不到 | 使用 |
| GET /fapi/v1/order | 1 | 需 orderId | 同上 | 不使用（allOrders 已涵蓋） |
| GET /fapi/v1/algoOrder | 1 | 需 algoId | 同上 | 不使用；SafeNet 觸發後的成交會出現在 userTrades |
| GET /fapi/v1/trade/asyn（成交歷史匯出） | 1000 | 單次最長 1 年 | 每月 5 次，與網頁下載共用；連結 7 天後失效 | **不使用**，見 §5 |
| GET /fapi/v1/order/asyn（訂單歷史匯出） | 1000 | 單次最長 1 年 | 每月 10 次，與網頁下載共用 | **不使用** |

文件沒有寫明回應時間欄位的單位（範例是 13 位數，符合毫秒），也沒有寫明匯出檔的欄位與時間精度。這兩點要以小樣本實測確認。

**覆蓋估算**：以 2026-11-15 執行為準，近 3 個月約從 2026-08-15 起。V81 用的 52 筆實盤（2026-06-02～10-28）中有 23 筆在此之後，加上 11 月的 3 筆，約 26／55 筆可用一般查詢取得；其餘約 29 筆（6～8 月中）只能靠匯出，而且每晚一天執行就少一些。

## 3. 工具（extract_fills_readonly.py）

- 只用 Python 標準函式庫，不匯入 `binance_trade`、`executor`、`strategy` 或任何專案模組。
- 允許清單只有 3 個 GET：`/fapi/v1/time`（公開）、`/fapi/v1/userTrades`、`/fapi/v1/allOrders`（簽名）。其他路徑直接拒絕；程式裡沒有 POST、PUT、DELETE。
- 標的固定 ETHUSDT，主網位址固定。
- 金鑰只從環境變數讀，或用 `--env-file` 唯讀讀出 `BINANCE_API_KEY`、`BINANCE_API_SECRET` 兩行。不寫檔、不印出；輸出不含簽章或原始回應。**建議另建一把只開「讀取」權限的 API 金鑰**來跑，不用實盤下單那把。
- 輸出兩個 CSV（成交、訂單）到 `~/fill_audit_out/`（專案目錄之外），並印出筆數與 sha256。
- 自我測試：`python3 extract_fills_readonly.py --selftest`（不連網；確認只發 GET、路徑在允許清單、窗口不超過 7 天、輸出不含金鑰與簽章）。研究環境已執行通過。

### 審查重點

1. 搜尋 `POST`、`PUT`、`DELETE`、`import binance`、`import executor`：應該都找不到。
2. `ALLOWED` 只有 3 個路徑。
3. `write()` 只寫 `TRADE_FIELDS`、`ORDER_FIELDS` 列出的欄位。

## 4. 建議執行順序（使用者在 VPS 親自執行）

```
cd ~/cryptoBot && git pull                    # 取得本工具；不重啟服務
python3 backtest/research/v85_readonly_fills/extract_fills_readonly.py --selftest
# 第一步：最近 7 天小樣本
BINANCE_API_KEY=唯讀金鑰 BINANCE_API_SECRET=唯讀密鑰 \
  python3 backtest/research/v85_readonly_fills/extract_fills_readonly.py --sample
```

最近 7 天如果沒有成交，CSV 只有表頭，這也算一個有效結果（確認呼叫與格式可用）。小樣本確認欄位與時間單位後，再跑近 3 個月：

```
... extract_fills_readonly.py --start 2026-08-16 --end 2026-11-15
```

把兩個 CSV 交給研究端即可，不需要也不要提供金鑰。

## 5. 歷史匯出要不要用

只有在近 3 個月的資料已能正確配對、並確認時間欄是毫秒之後，才值得考慮用一次成交歷史匯出（每月 5 次）補 2026-06～08 的約 29 筆。匯出檔欄位與時間精度官方未說明，未實測前不宣稱能涵蓋全部 55 筆。本輪不提出使用。

## 6. 收到資料後的分析方法（事前固定，fill_latency_analysis.py）

1. 以 orderId 合併逐筆成交：成交量加權均價、第一與最後成交毫秒、總費用。
2. 市價單與觸發單（SafeNet 等）分開；觸發單不屬於整點決策。
3. 參考整點 T＝訂單建立時刻向下取整到整點；晚於整點 30 分鐘的市價單另列。
4. 分開報：T→訂單建立、訂單建立→最後成交、T→最後成交（另列 T→第一筆成交）。
5. 方向性成交價差：對 T 收盤，買單正號、賣單負號，正值表示對策略不利。
6. 缺少本機時間戳（程式醒來、K 線取得完成、送單）時，「T→訂單建立」無法再拆，報告中明標。
7. 這些實盤樣本只代表其期間與配置，不能外推為 2021～2026 的獨立樣本外績效；F0（T 收盤）與 F2（延後 15 分）不是成交價的上下界。

自我測試：`python3 fill_latency_analysis.py --selftest`（合成資料，已通過）。
