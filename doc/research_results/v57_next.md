# 下一輪接續狀態（2026-11-13，V55、V56 結束後）

接續點：分支 `claude/v55-research`，commit 見 `git log`（V56 結果為 a1bffc8 之後的一筆）。下一個編號 V57。資料以三支 fetch 腳本重建（v53_fetch_data、v54_fetch_basis、v55_fetch_spot、v56_fetch_mark）。

## 已盤點、尚未執行的來源，以及為什麼沒有直接開 V57
以下是「未看任何結果」的事前篩選，不入試驗台帳；若日後要做，仍須完整預登記。
| 來源 | 可想到的機制 | 事前判斷 |
|---|---|---|
| 現貨 ETHBTC | ETH 相對 BTC 跑贏／跑輸後回歸或補漲 | 與 V9（BTC-ETH RelDiv）、V12 R4（背離做空，FAILED）、V44 第 11 輪相對強弱出場同一機制；沒有能解釋原失敗的新證據，跳過 |
| 幣本位永續、USDⓈ-M 溢價指數 | 合約對指數的溢價＝槓桿需求 | 與 V30／V43／V48-E（funding）、9/10 溢價 overlay、V51、V54 基差同屬擁擠度，皆失敗；換欄位不構成新機制，跳過 |
| funding 結算時點前後 1 小時的價格漂移 | 為了躲 funding 的調倉流量 | 被可交易性下限排除：每年約 1,095 個事件，平均每筆需毛報酬約 31bp，而 1 小時平均絕對波動只有 30~79bp，等於要求近乎每次都對。未跑 |
| 5m K 線 | 小時內的波動結構 | 5m 均值回歸、V18（15m／30m 非突破）、V45 5m 提前進場、9/8 小時內確認皆失敗；目前提不出與這些不同、又有經濟機制的假說 |

## 確切缺口（補上才有新方向）
1. 真實清算資料（V56 只否定了免費代理）。data.binance.vision 的 liquidationSnapshot 路徑取不到；其他來源需要使用者決定是否開放新的資料網域或付費。
2. 選擇權隱含波動與偏斜的長歷史（DVOL 自 2021-03 起，V40 已測水準；偏斜與期限結構未取得）。
3. 鏈上與穩定幣供給、美國現貨場（Coinbase）逐時價格：雲端網路白名單目前只有 data.binance.vision，需要另外開放。
4. 幣本位 ETHUSD 指數歷史成分、標記價公式變更日：V55、V56 的未驗證項。

## 可交易性下限（V56 計畫第 0 節）
全時段交易的情境下，日頻方向訊號需要 55%~63%（以漲跌幅加權）的命中率才夠每年 3,600U，2023 年最嚴。先前所有輪次的訊號都遠低於此。這是情境換算，不是不可能性證明；事件型策略改用各年事件數換算。任何新假說先過這一關再寫預登記。

## 下次可直接執行
```
git checkout claude/v55-research
python backtest/research/v53_fetch_data.py && python backtest/research/v54_fetch_basis.py && python backtest/research/v55_fetch_spot.py && python backtest/research/v56_fetch_mark.py
python backtest/research/v53_baseline.py   # 基準 +6,026.15U／690 筆
python backtest/research/v55_d0.py && python backtest/research/v55_d1.py
python backtest/research/v56_d0.py
```
