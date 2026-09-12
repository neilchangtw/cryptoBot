# 持續研究查重與下一步（沒有新增收益試驗）

目前P極值年齡、Q量能時鐘、R新突破續期、T持倉量加權收盤線皆經濟REJECTED；S鏈上basefee與V修復SampEn為DATA LIMITED；U持倉Path R資格只有4／1群，INSUFFICIENT SAMPLE。不得用已拒方向的窗口、方向、確認数或新止損救援。

本次追加查重（未登記成新輪次，未看新事件或收益）：

- 5m「已接受突破」：`intrahour_entry_results_20260908.md`已有最近30/60分鐘5m/15m/30m收盤站穩，完整狀態、funding/mark、三成本均失敗。不能把6/12比例或只選最後5分钟當成新資料；V37是另一個已預登記但未執行的高GK補單題，也不能套用舊資料去救已拒F移除GK。
- GK移除outlier／robust variance：V7已測RBV與kurtosis，沒有新模態，不以微調標準化當新機制。
- 圓整價位／心理關卡：`eth_dir_d_price_range.py`及backtest_history有D2；不重跑。
- 影線阻力／Donchian：舊CRB與price-channel已有close/high-low、結構stop研究；尚未找到足夠不同且具體的未測機制，不啟動類似掃描。

S新增的小樣本證據：Blockscout可讀本期早、中、晚3個reference-date前後區塊，時間括區與基本欄位通過。這推翻「鏈上一定要付費才能取得原始block欄位」的過強說法；但不能推翻歷史時點門檻，仍不能跑候選PnL。

歷史consensus finality小樣本補查已完成：PublicNode genesis成功，第一個歷史slot finality_checkpoints回傳HTTP403即停。總3個對外GET/340bytes；包含一次解析錯誤後重取genesis，完整原始檔與限額說明見finality_probe/REPORT.md。不能因此斷言所有archive都沒有資料；但本次沒有補齊S歷史時點證據，不重試或擴量。

SampEn去重找到具體舊缺陷：m+1模板少一個，A=0的無限大與B=0未定義混成NaN，後接完整100根rolling擴散缺值。R2只有normalize註解沒有修復程式；r乘std本來具尺度不變性，不能說小std本身造成全NaN。V保持20-return、m2、r.2，修復配對與明列rank定義後仍有2218根undefined、只有443根有效完整rank，原269個gate中260個invalid。沒有事件／PnL；不能事後加長窗口或補值救援。

其他查重仍維持：trade count/平均單筆、Hurst/ER/PE/自相關/峰度等已有研究；不重複重跑。新資料方向目前需要可實際取用的歷史版本／接收證據或逐筆撮合資料；新機制必須先證明與上述舊研究不同。尚無可直接放行完整驗證或部署的候選。

研究仍ACTIVE，不是已找到改善或所有策略都無效的結論。任何下一輪仍須獨立預登記、30/15事件、全狀態三成本與原嚴格經濟門檻。
