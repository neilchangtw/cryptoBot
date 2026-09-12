# 持續研究第1輪 P：突破邊界形成時間（預登記）

本輪考慮3個機制：P區間極值年齡；regime持續時間（與既有trend/壓縮持續資訊可能重疊）；圓整價位（eth_dir_d_price_range及backtest_history已有研究，排除）。選P，只有OHLC與已知時間，沒有新增接收資料負擔，預期能影響較廣的原突破事件。

去重：定向查argmax/argmin、extreme/peak/trough/breakout/regime age、bars/time since、Aroon/阿隆／極值年齡。既有命中主要是收益峰谷索引及冷卻，不是極值位置特徵。V27的23項是突破幅度、壓縮長度、ret/streak、range position、波動／量能等，沒有15根邊界極值年齡。既有breakout窗口掃描只改範圍長度，本輪固定15，新增突破對象形成多久的時序資訊；不是替換名稱重跑窗口優化。

假說：壓縮後突破近期形成的極值，比跨越較舊極值更可能延續；較舊邊界可能只是先前波動的回訪。固定規則以15根窗口的中點區分，不根据原交易收益或事後出場挑閾值。

主候選：對原所有進場條件已通過的L/S，在決策i，以**i-15到i-1的收盤價**找L最大值／S最小值的最近一次出現。age=i-last_extreme_index，介於1～15；**只有age<=7才進場**。L/S同規則；相等極值取最近，不回填未確認轉折。不更改15-bar breakout本身、GK、regime、日週時間、出場、名目或槓桿。

時點：age僅依前一根以前的已收盤K，source_last_close=目前bar開盤，早於決策至少1h。當根收盤只由原breakout規則使用，age不讀當根OHLCV。不用未來高低、最終MFE/MAE、交易出場理由或完成交易收益。15根有缺值就資料停止，不補值或降閾值。

直接事件=基準0bp原允許進場而age>7；30/15群准入、2026-01-01固定切分、三成本與完整風控均遵循continuous_strategy_contract_20260912.md。主候選通過才測age<=6及<=8兩鄰域。任一主經濟門檻失敗立即REJECTED，不改成偏好老極值、不更換方向救援。
