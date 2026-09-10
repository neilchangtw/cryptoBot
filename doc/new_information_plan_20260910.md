# 2026-09-10 新資訊研究：事前契約

本計畫在候選收益計算前建立；第一順位 OI，第二順位 premium。只做本機歷史研究，不修改正式策略、設定、VPS，不下單、不 commit／push。使用者授權來源為 Downloads/strategy_research_prompt_20260910.md 及「依文件開始研究」。原資料與未提交工作保留。

## 查重與排序

完整文件、程式、結果與 SHA256 索引由 `index_new_information_20260910.py` 產生在 `doc/research_results/20260910_new_information/history_index.json`。索引涵蓋 V9～V37、9/8～9/9 結果及現有未提交研究。以下是具體研究定義的分類，不是市場定理。

狀態 A＝已研究支持保留／已採用；B＝具體定義未通過；C＝正向線索但不足；D＝只有計畫；E＝資料受限未完成。

| 歷史範圍 | 已測定義與狀態 | 文件／程式依據與本輪邊界 |
|---|---|---|
| V9～V10 | BTC/ETH 相對報酬、均值回歸、EMA、GK、時框與 L/S 組合；部分為當時 A，已被後續基準取代 | v9_research、v10_research；explore_v9*、v10*、explore_v10* |
| V11～V14 | TP/MH、2h/GK/TBR、breakout窗口、extension；V14 MFE/conditional MH A | v11～v14_research；v14_export_trades.py；不重掃 |
| V15～V18 | ATR/entry momentum、TBR reversal、非 breakout、15m/30m 價量等 B；V16 有降級 backup，未取代基準 | v15～v18_research 及對應腳本；OI 不是成交量/TBR |
| V19 | BTC/宏觀/FGI/HMM 等具體定義 B；OI/LSR E | v19_research:24,38,48；v19_r0_download_data.py:93～103 只用 futures/data 近期 API，未用 daily metrics |
| V20～V22 | 多標的、冷卻/時框等 overlay、古典 TA B | v20～v22_research 及對應腳本；不改名再測 |
| V23～V25 | R slope gate、regime exits A；vol overlay/跨幣風險 B | v23～v25_research、v25_engine.py、strategy.py |
| V26～V27 | running-MFE 提早認賠、部分止盈止損、23 特徵 meta-label B | v26/v27_research、v27_r1_predictability.py；OI/premium 未列入原特徵 |
| V28～V29 | 複利 A 未部署、CUSUM A 通知 | v28/v29_research；本輪禁止提高資金或曝險，排除 |
| V30 | 最近結算 funding、7日均值、90日 percentile 的分桶 B | v30_r1_funding_diag.py:1～55；未測 premiumIndexKlines 的小時偏離/變化 |
| V31～V34 | MH grace、L13/S16、S MILD_UP MH8、SIDE1.3% 等 C 或 B；基準保留 A | v31～v34 文件及稽核；不把回測冠軍升級 |
| V35～V36 | 高 GK 補單、episode、CUSUM recovery、Beta B；執行品質 C；OI/book/options E | v35_high_gk_acceleration.py、v36_r1_v29_event_recovery.py、v36_multitrack_research:62～73 |
| V37 | 高 GK L + 至少9/12根5m站穩，以及前瞻收集 D | v37_microstructure_shadow_plan；仍只有計畫，不與9/8原策略進場確認混淆 |
| 9/8 MH 計畫/執行 | MH延長 B；S/DOWN TP3% C；ADX/volume/retest/delay B | strategy_optimization_plan、optimization_execution、execute_optimization_plan_20260908.py |
| 9/8 進場診斷/子K | 當根/6h順向量差診斷；5/15/30m站穩 B | maxhold_entry_diagnostic、intrahour_entry_results；同名腳本 |
| 9/8 突破失效 | 回到固定突破界線立即/確認退出 B | breakout_failure_results、breakout_failure_20260908.py |
| 9/9 獲利保護 | 5m/15m提前保護提高勝率但少賺 B | profit_protection_results、profit_protection_20260909.py |
| 9/9 成交成本 | 省費算術與實盤偏差診斷；maker執行驗證 E | execution_cost_results、execution_cost_20260909.py |
| 9/9 再次突破 | 原單結束後固定界線再進場、等待策略 B | second_breakout_results、second_breakout_20260909.py |
| 9/9 現貨同步 | 各自15h close界線同步，266/269及75/75 MH已通過 B | spot_futures_sync_results、spot_futures_sync_20260909.py |
| 9/9 量價交互 | 順向F15>0且F15>F45、R15≤0；空單僅3原事件 C | flow_price_results、flow_price_20260909.py |
| 未提交9/4工作 | 虧損後路徑、動態TP、price/candle、viewer等；診斷/少量shadow | loss_followthrough_analysis.py、tp_sensitivity_analysis.py 與對應MD；納入索引，保留檔案 |

## 最多三個初選方向

| 排名 | 情境、資訊與可推翻假說 | 資料/可知性/樣本機會 | 對照、成本與停止 |
|---|---|---|---|
| 1 OI | 壓縮突破發生前後，總未平倉合約數仍在減少；假說：濾掉近期 OI 不增加的原始突破，可避免較多淨虧損且完整策略收益提高。OI 為存量，不等於 volume/TBR，不能解讀為新資金或大戶意圖。最近是V19/V36資料阻擋 | Binance ETHUSDT USD-M daily metrics，2024-09/2025-09/2026-09三日各288列；無金鑰。預估兩年約8.6MB；比例符號切分有機會影響數十筆，實際先計數。歷史received_ts未知，採15m隔離並另留時間限制 | base、quality、單側、同拒絕數隨機；3主候選。若缺漏>1%不能稱完整覆蓋；經濟/樣本不過即停 |
| 2 premium | 最近已完整結束一小時的溢價，在突破方向上同時高於自身24h中位並继续擴大；假說：此狀態下追價的淨結果较差。來源是premium index，與已結算funding非同一時間尺度/映射，不用spot同步改名 | Binance premiumIndexKlines 1h，2024-09/2025-09/2026-08月檔已核實；無金鑰，完整約0.5MB。故意延遲一根1h後才用；未知歷史發布/修訂仍需揭露。二個符號交互有機會影響數十筆，不能先宣称樣本足 | base、quality、level-only、change-only、同數量隨機；3主候選。與單因子比較；經濟/樣本不過即停 |
| 3 持倉分布 | 大戶position與全市場account方向差異；存量分布不同於總OI與成交流，可測群體分歧 | metrics樣本有欄位，但官方當前top trader即時端點有API key要求，歷史欄位映射與可用時間尚未完整核實 | 本輪不執行：選兩個獨立方向已足，避免把同一新資料檔開成大量feature掃描；D/E，不算已測無效 |

Order book/逐筆強平/期權不列成本輪合格候選：未核實對齊兩年且總50MB內的免費來源；不下載逐筆全歷史、不假稱已證明不可得。不存在要求硬湊五個方向。

## 共同基準與門檻

- 資料：凍結 `data/maxhold_review_20260908/candles.csv`，17,519根，台北開盤2024-09-08 11:00～2026-09-08 09:00；最後收盤2026-09-08 10:00。不刷新覆寫。
- 部位200U×20=4,000U；每笔原引擎成本$4+歷史funding。realistic=True，TP/BE/MH等收盤市價、SafeNet原穿透模型；額外每次成交0/2/5bp。保證金歷史排程仅第二口徑。
- 已先重現269笔、净$7,926.701053608431、WR63.197026%、mark MDD$368.531247；逐筆side/entry/exit/reason/pnl/funding/net與9/8帳本一致。
- 所有来源、引擎、正式檔與本計畫 SHA256 存 registration.json。原引擎的日/月熔斷以交易PnL計算，funding另入帳，與現行executor語意一致；不偷偷改熔斷成含funding。
- 全期净收益相對基準至少+5%（分母基準净收益），净勝率至少+1百分點（净損益>0/平倉筆數）。MDD不增、最差完整720h不更負，容差僅浮點1e-7。前後期收益不得倒退，0/2/5bp的全期收益/勝率改善方向保留。
- 早期exit<2026-01-01、後期exit≥2026-01-01、最近exit≥2026-06-01；期間净收益另按mark equity差額計，跨界持倉差異與closed_net分列。
- 樣本：直接因新條件被拒的原交易，排除單純品質缺失；先以跨方向24h間距分群，至少30群、後期至少15群（以進場日期）。原交易移除/共同變動/替代新增另報，不把替代新增算獨立證據。群數/原事件數/年均率/累積30群年數全揭露。
- 完整狀態：重用intra.simulator及cost.account；L/S占倉、cooldown、日月/連敗熔斷、月上限全保留。若期末有持倉先擴展同一帳本或停止評分，不靜默丟掉。
- 前綴測試cut=10000,16000：截掉未來原始資料重新計算特徵，檢查過去特徵、gate、已完成交易。最小fixture含零變化、缺值、時間邊界，且與真實歷史績效分開。
- 不确定性：每日cash增量，circular block bootstrap 7日/2,000抽，附30日敏感度；6個主候選Holm校正。資料已重用多輪，p值僅本輪校正，無法抹掉歷史搜尋。全部不是新OOS。
- 同數量隨機：每主候選100次，固定seed。依side×calendar quarter，在原基準入場集合抽相同新條件拒絕數；其餘技術合格gate另抽相同拒絕數，保留quality。再跑完整狀態引擎。這是事後匹配的診斷對照，不是可部署規則，随机收益不是新候選。
- 資料最低品質：排序後唯一、無衝突、有限正OI、premium OHLC合理；不補造數值，不跨缺口前填。每公式要求涉及的完整連續窗口。quality gate與base比較明示。大缺口時DATA_BLOCKED，不以凍結價格重寫資料。

## 方向一：OI 事前公式

在原1h bar開盤 t、決策/成交D=t+1h，使用 metrics create_time（UTC轉台北）q=D−15min及q−60min，精確reindex，不近似匹配。

`delta_oi = OI(q)/OI(q−60min)−1`；以 `sum_open_interest` 而非含價格變動的 `sum_open_interest_value`。兩端之間13個5m點均須存在、finite且>0；相等視為未增加。

3主候選：`oi_both` 對L/S都拒絕delta_oi≤0；`oi_long`僅L；`oi_short`僅S。共同 `oi_quality` 在涉及OI的一小時缺值就不開新倉，兩側都套用，以便分離品質效果；原出場照常。主假說是both，單側是預登記解剖，不能事後當獨立發現。

create_time按官方OI endpoint為期間終點解讀，保守同時記`close_ts_bound=q+5min`、`assumed_available=q+10min`，必須≤D。這是明示延遲假設，不是假裝有received_ts。archive檔下載/發布在事後，不能聲稱當年讀archive即時交易。若歷史修訂無法排除，研究結果仅是固定archive版本下的反證/候選，部署需要前瞻原始接收記錄。

只有主候選基本经济和樣本皆合格才跑窗口30/120min鄰域、額外60min資料延遲、10bp及連續狀態WF。OI最多5真正候選（3主+2窗口）；不反向改成拒絕OI增加，不增新閾值。

## 方向二：premium 事前公式

只在方向一完成其停止判定後啟動候選收益。先固定定義以避免被OI績效反饋改規則。

對原bar t、決策D=t+1h，取premium的t−1h bar收盤p，該bar於t已收盤，留完整1h接收緩衝；不使用t bar的premium。取前24個更早已收盤premium收盤中位m（不含p），前一值p_prev。

L sign=+1，S sign=−1。`A=sign*(p−m)>0`，`B=sign*(p−p_prev)>0`。交互拒絕A且B；相等不拒絕。quality需這25個premium小時全有資料且OHLC有效。

3主候選`premium_both/long/short`；兩個單因子對照`premium_level`(A)、`premium_change`(B)均雙側，另base/premium_quality。不把premium當價格、不要求其為正、不使用無效volume欄。

只有基本经济及樣本合格者追加中位窗口18/30h鄰域、再延遲1h、10bp與WF。真正候選最多5（3主+2窗口），單因子為已登記診斷對照。若pair收益/WR不能超過單因子或同拒絕數隨機不支持額外价值，不稱交互alpha。

## 重驗、停止與交付

基本不合格即停止該方向，不追加heavy掃描。若合格，先刪除最佳增量月份/單一配對事件檢查，再做至少6段連續狀態WF（不重置倉位；使用當時已完成且purge/embargo48h的訓練交易選本輪固定候選或base），參數鄰域、10bp、前綴與時間延遲稽核。通過也只能HISTORICAL_CANDIDATE，真正未見資料仍待收集。

分類順序：具體資料無法支持有效計算→DATA_BLOCKED；收益為負或風險/分期倒退→REJECTED；偏正且基本风险不差但独立样本不够→INSUFFICIENT_SAMPLE；样本够且偏正但幅度不足→SMALL_EFFECT；全部歷史條件過才HISTORICAL_CANDIDATE。附所有失败项，不靠单一标签隐藏其他缺点。

完整交易、funding、equity、gate、配對、收益分解、隨機對照摘要、樣本群與JSON放data/new_information_20260910；小型结果、索引、registration、資料品質與報告放doc/research_results/20260910_new_information。不把合成fixture當回測證據。

## 已查證官方來源（2026-09-10）

- [Binance public data](https://github.com/binance/binance-public-data)：免費公開封存、每日/月檔、checksum及事後修訂說明。
- [OI endpoint](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#open-interest-statistics)：近1月、5m等頻率、總持倉數/價值、期間終點。
- [Premium endpoint](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#premium-index-kline-data)：premium OHLC，open_time識別。
- [Top-trader endpoint](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#top-trader-longshort-position-ratio-market_data)：大戶持倉分布的定義及目前key要求。
- 下載manifest逐筆保存原始官方URL、checksum、headers、bytes；3個metrics日樣本+3個premium月樣本合計84,727 bytes，均200/checksum通過。metrics檔可能未排序；一些老日期Last-Modified為2026，不能據此證明未修訂。
