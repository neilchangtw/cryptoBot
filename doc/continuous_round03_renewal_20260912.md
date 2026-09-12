# 持續研究第3輪 R：持倉新突破續期預登記

Q成交量時鐘已經濟REJECTED，不改窗口或快慢救援。本輪考慮R持倉新訊號續期、GK outlier穩健化、跨資產退出。後兩者分別與V7 RBV/kurtosis及V8跨市場研究重疊，先不重跑；只選R。R的到期依據是新的價格區間突破事件，不是累計市場成交活動，不是Q的窗口／閾值改名。

查重：second_breakout_20260909測進場前等待及原倉出場後再進場；V36 episode用過去已完成TP；V21有pivot觸發entry重設。定向查renew/refresh/reset hold/mh、續期、重新計時，未找到**仍持倉**時用新15bar close突破更新MH年齡的規則。

假說：原進場後若出現新同向15bar收盤突破，原訊號獲得新的結構確認；到期時間可從最新確認算起，讓仍有新訊號的持倉保持。既有TP/MFE/SafeNet仍優先，不能靠放寬保護增加PnL。

主候選：L持倉內每根收盤若close[i]>max(close[i-15:i])，更新last_confirmation=i；S相反小於min。原entry bar為初值，不要求新開倉的時段／GKgate，也不加倉、不重設entry價格／qty／stop／TP。只有原非extension MH條件替換為：**i-last_confirmation>=effective_MH 或 bh>=2*effective_MH**。effective_MH仍受原regime與L conditional減少控制；進入extension後原2bar／BE規則不變。任意浮盈或浮虧用相同規則，不讀原最終MFE/MAE／收益／出場理由。

新confirmation只在該根收盤後知道，不預先使用未完成bar；採基準同一收盤代理成交，若主候選通過再驗證延遲1h。只用當前及過去15根，無中心化或未確認轉折。缺資料停止，不前填／後填。固定名目／槓桿／原SafeNet保護不變。

事件門檻：基準0bp每一原進場root的第一個平倉／extension狀態差異；其他高優先級出場若同價結束，不算差異；每root只計一次再作24h傳遞群，全30／後15。同一套固定2026-01-01切分與三成本全狀態嚴格經濟門檻。

最多兩鄰域：硬上限1.5×MH（取ceil）／2.5×MH（取ceil），新突破窗口一直15。主候選一項失敗不跑鄰域、不加入單方向／價格條件救援。其他門檻與進階驗證見continuous契約。
