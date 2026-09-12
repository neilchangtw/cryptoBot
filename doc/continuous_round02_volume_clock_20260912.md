# 持續研究第2輪 Q：成交量持倉時鐘預登記

P極值年齡已REJECTED。這輪考慮成交量時鐘、成交筆數時鐘、固定收盤後二次確認。選Q：凍結原K已包含volume；成交筆數不在本次原candles schema且相關進場特徵已研究；二次確認與既有retest/delay重疊。只對Q計事件。

去重：V11/V13/V25/V26及9/8測固定MH、regime MH、價格給機會／分批止盈損；V8/V10的volume/count是進場量能，9/8 vol10/vol15是S進場量比gate。定向查volume/business/market/activity clock、volume_time、持倉成交量等，未找到把累積成交量用作MaxHold進度的同機制；不重跑MH固定時間掃描。

假說：相同6～10小時可能有不同市場參與程度，價格路徑消化訊息的進度應與成交量相關。成交量快則較早檢查MH，慢則給較長觀察；不因當時浮盈虧選擇快慢、不使用未來結局。

主候選：進場bar e，冻结ref=mean(volume[e-24:e])，排除進場當根。後續每次已收盤bar i累計Vclock=sum(volume[e+1:i+1])/ref。只替換原`bh>=effective_MH`條件：**bh>=ceil(effective_MH/2) 且 (Vclock>=effective_MH 或 bh>=2*effective_MH)**。L effective_MH仍沿用原regime及bar2 conditional reduction；S仍沿用原regime。原MH到期時的價格正負判斷、延長2bar及BE、TP、MFE、SafeNet全部保留。延長的2bar仍為原牆上時鐘，不再用量能時鐘。最長期限有2倍硬上限，不加倉、不提高原stop距離。

只有reference在進場前凍結；已收盤每根成交量在當根決策時才入時鐘，不把未完成根完整volume提前使用。沿用基準小時收盤代理成交；若主候選通過再測額外1h延遲，不能聲稱已驗證即時成交。15／24暖機後任何缺值／負量／零reference停止，不填補。零實際量使時鐘暫停，但硬上限照常。

直接事件：在基準0bp原持倉狀態逐根比較原exit/extension動作與Q動作，先執行未變的高優先級出場；同價平倉不算差異。每一原root進場只計**第一個**平倉／延長狀態差異。不讀原最終PnL或出場分類選事件，不拿連鎖替代交易充數。24h群全>=30、後期>=15才進收益。

完整狀態、固定2026-01-01切分、三成本以及嚴格經濟／後續驗證遵循continuous契約。兩個預登記鄰域僅reference=12／48根，主規則不變；主候選一項失敗就不跑鄰域，不反轉成交量快慢、單挑某個方向或重選上下限救援。
