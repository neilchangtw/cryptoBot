# 研究資料來源、版本與重建方法（2026-11-13）

打包檔不含大型原始行情資料。下表列出來源、重建指令與保存需求；各檔 sha256 在 `DATA_SHA256.txt`。
`data/` 內前 10 個檔已在研究分支 `claude/v55-research` 的 commit a4162fa（Deribit 逐筆為 .csv.gz）。本輪新增的分鐘線與逐筆成交日檔不在 git。

| 檔案 | 來源 | 重建 | 可否重新取得 |
|---|---|---|---|
| ETHUSDT_1h_2020_2026.csv、ETHUSDT_funding_2020_2026.csv、ETHUSDT_metrics_1h_2021_2026.csv | data.binance.vision（USDⓈ-M 月檔／日檔） | `python backtest/research/v53_fetch_data.py` | 可；交易所歷史檔偶有回補，校驗碼可能不同 |
| ETHUSD_quarterly_1h.csv、ETHUSD_index_1h.csv | data.binance.vision（COIN-M） | `python backtest/research/v54_fetch_basis.py` | 可 |
| ETHUSDT_spot_1h.csv | data.binance.vision（現貨） | `python backtest/research/v55_fetch_spot.py` | 可 |
| ETHUSDT_mark_1h.csv | data.binance.vision（markPriceKlines） | `python backtest/research/v56_fetch_mark.py` | 可 |
| deribit_eth_option_trades_22_24.csv | history.deribit.com 逐筆成交，每日 22～24 時 UTC | `python backtest/research/v60_fetch_deribit.py`（可續傳，約 1 小時） | 可，但耗時；建議保存 |
| stablecoins_total_daily.csv | stablecoins.llama.fi | `python backtest/research/v59_fetch_stables.py` | **不可完全重現**：來源會重算歷史，須保存本檔 |
| eth_exchange_flows_daily.csv | community-api.coinmetrics.io | `python backtest/research/v59_fetch_flows.py` | **不可完全重現**：來源會重算歷史，須保存本檔 |
| data/audit_1m/*.csv | data.binance.vision 1m klines 與 markPriceKlines 日檔 | `python backtest/research/v61_audit.py`、`v61_liq_audit.py` 會自動下載 | 可 |
| data/audit_agg/*.zip（5 日，約 100MB） | data.binance.vision futures/um/daily/aggTrades/ETHUSDT | 手動下載 2021-01-05、2021-02-24、2022-11-22、2022-12-16、2023-08-29 | 可 |
| doc/research_results/v57/live_trades_viewer_20261113.csv、viewer_backtest_20261113.csv | 專案 viewer API 2026-11-13 快照 | 無 | **不可重現**（快照），已在 git |

保存需求：雲端工作區與 /mnt/project-files 都不是永久備份。需要長期保存的是本打包檔、兩個會被來源重算的日檔，以及 Deribit 逐筆檔。
