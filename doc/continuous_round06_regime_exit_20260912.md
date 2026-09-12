# 第6輪 U：持倉期間Path R資格失效

T量加權收盤線三成本全失敗，不改確認數救援。本輪考慮Path R持倉資格、GK持倉後再壓縮、額外maker exit；選第一個，既有資料及鎖定閾值可直接重用；GK與既有壓縮／MH研究近似、maker缺撮合／接收資料，兩者不測。

去重：V23 Path R只在進場判斷；V25-D在進場鎖定regime與TP/MH，dynamic_tp_research_20260904明定持倉中不隨regime變化。全庫regime-change/flip/exit、持倉Path R定向檢索未見持有中因Path R資格消失而平倉。U沒有更改既有4.5%／1%閾值、不動TP/MH查表、不用T價量重心或R突破续期結果。

假說：原策略不願新進入的regime若在持有途中出現，原倉可能也應結束。每完成1h bar i，使用和原entry相同的lagged slope：SMA200(close)相對100根前SMA200的變動，再shift(1)。L當slope>0.045；S當abs(slope)<0.010，原規則未平倉時額外市價平倉RGX。主候選1根確認，最早entry+1，原所有平倉規則優先（剛進extension仍算持有）。不問浮盈、MFE、事後結果或原出場類型。原進场／曝險／冷卻／熔斷照常完整回放。

在事件和收益前固定：鄰域僅連續2/3根同樣資格失效，且確認根都必須在entry之後；main全通過才跑。用原0bp逐bar狀態每原entry第一次新增平倉差異，24h傳遞群>=30／2026-01-01後>=15，未通過不計PnL。既有暖機310根後若必要slope非有限即DATA LIMITED，不補值。只用本機凍結完成K，資料／funding／mark假設沿用基準。前三成本no-op及物理前綴後才評估三成本全部原經濟門檻；失敗即停，後續驗證依continuous契約。
