# V54 資料覆蓋查證（2026-11-13）

查證方式：對 data.binance.vision 的月檔做 HTTP 檢查；標「已下載」者另做列數、缺漏、重複檢查。只有「已下載並檢查」算已驗證，其他一律是未驗證。
實盤可得性一欄：雲端環境連不到 Binance REST，因此全部是「依 Binance 公開 API 文件推定」，上 shadow 前必須在 VPS 實測，尚未驗證。

| 資料 | 2021 覆蓋 | 檢查結果 | 時間戳 | 決策時可得（推定，未實測） | 狀態 |
|---|---|---|---|---|---|
| USDⓈ-M ETHUSDT 1h K 線 | 有（2020-09 起） | 已下載，54,048 根至 UTC 2026-10-31，缺漏 0、重複 0 | 開盤時間，收盤後才用 | fapi klines（實盤現用） | 已驗證 |
| USDⓈ-M funding | 有（2020-09 起） | 已下載至 2026-10 | 結算時間 UTC | fapi fundingRate | 已驗證 |
| Binance metrics（OI、多空比） | **無**（2021-12 起） | 已下載；2022 年大戶比多缺值 | create_time | futures/data 端點只留近 30 天 | 已驗證，不涵蓋 2021 |
| COIN-M ETHUSD 季度交割合約 1h K 線 | 有（201225 合約 2020-09 起） | 已下載 25 檔合約 106,725 列；近月序列（距到期 ≥14 天）在 2020-09-15~2026-10-31 缺 96 小時（2023-08-28 起 4 天，230929／231229 各 1 處缺口）；近月零成交小時 2021~2025 為 0% | 開盤時間 UTC | dapi klines | 已驗證 |
| COIN-M ETHUSD 指數價 1h | 有（2020-09 起） | 已下載 53,952 列，缺口 1 處、重複 0 | 開盤時間 UTC | dapi indexPriceKlines | 已驗證 |
| COIN-M ETHUSD_PERP 1h | 2021-01 檔存在 | 僅 HTTP 200 | 未檢查 | dapi klines | 未驗證 |
| USDⓈ-M premiumIndexKlines 1h | 2021-01 檔存在 | 僅 HTTP 200；repo 舊研究只用 2024-09 起且 2026-06-29 缺一天 | 未檢查 | fapi premiumIndexKlines | 未驗證 |
| USDⓈ-M markPrice／indexPrice K 線 | 2021-01 檔存在 | 僅 HTTP 200 | 未檢查 | fapi | 未驗證 |
| USDⓈ-M 5m K 線、aggTrades | 2021-01 檔存在 | 僅 HTTP 200（aggTrades 體積大，使用者先前婉拒全量） | 未檢查 | fapi | 未驗證 |
| 現貨 ETHUSDT、ETHBTC 1h | 2021-01 檔存在 | 僅 HTTP 200 | 未檢查 | api klines | 未驗證 |
| liquidationSnapshot（um、cm） | 已測日期皆 404 | 此路徑取不到；其他免費來源未查 | - | - | 未取得，原因未查證 |
| COIN-M fundingRate 2021-01、um 季度 ETHUSDT_210326、BVOLIndex、um ETHBTC、bookTicker 2021-06 | 已測路徑 404 | 只代表該路徑該月沒有檔 | - | - | 未取得 |

結論只有兩句：（1）季度合約基差是一個 2021 年有完整免費資料、且先前各輪沒用過的資料來源；（2）「免費資料已用盡、只剩付費資料」沒有被查證，不得當作研究結論。
重跑：`python backtest/research/v54_fetch_basis.py`
