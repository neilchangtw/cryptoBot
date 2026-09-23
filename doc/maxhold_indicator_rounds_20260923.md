# MaxHold 新指標三輪預登記（2026-09-23）

本文件在執行本輪候選回測前建立。研究只修改獨立腳本；線上策略、設定及資料均不改動。既有的單棒收盤強度／ATR 不利收盤、固定延長、淺虧 grace、MFE-floor、regime 固定 MH、突破界線收回均已測過，不在本輪重測。

## 共同規則與資料

- 基準：本機現行 V14+R+V25-D、L15/S15、完整狀態引擎、historical margin schedule、realistic fills、每筆額外 $5，並用同一引擎測 0／2／5bp 滑價。校驗既有 SHA256 凍結 1h K 線、funding、mark；期間截至 2026-09-08 09:00 開盤棒。所有讀值在決策棒收盤後才使用，`decision_time = datetime + 1h`。
- 只在原 MaxHold 棒、原有 SafeNet／TP／MFE 都未出場且當棒方向性浮盈 `<= 0` 時作判斷。觸發則額外最多持有 2 根完整 1h K；原有 SafeNet、TP、MFE、正收益 extension／BE 仍優先；若到期仍負，以原 MaxHold 市價收盤出場。後續交易與風控全部由完整引擎重跑。
- 依下列順序執行。只有主門檻同時提升全期淨 PnL 與勝率，並通過 0／2／5bp、較晚期、MDD、最差 30 日、鄰域及至少 30 個受影響／15 個較晚期直接事件，才停止並列為待 shadow 驗證；否則進下一輪。後續輪數不得超過三。至少淨 PnL +5%、勝率 +1 個百分點為實用目標，不代表統計顯著。
- 固定較晚期切分為 2026-01-01。此段已被舊研究間接看過，只能稱歷史較晚期檢查，不能宣稱真正未見 OOS。結果需報直接觸發、替代交易、錯砍／漏掉的贏家、funding、持倉時數與前綴穩定性。

## 第一輪：主動成交方向

資訊來源是 `taker_buy_volume` 與 `volume`，均來自截至當棒收盤的最近 3 根已完成 1h 棒。`taker_share = sum(taker_buy_volume,3)/sum(volume,3)`；Long 用 taker_share，Short 用 `1-taker_share`。主門檻方向性 share `>=0.54`，鄰域 `0.52／0.56`。假說：虧損但有主動成交支持原方向的倉位可能晚到 TP；風險是主動量僅短暫反彈。

## 第二輪：多棒價格效率

截至決策棒的 4 根已完成 close 變化：`efficiency = directional(close[i]-close[i-4]) / sum(abs(diff(close)), 4)`。要求方向性淨變動 `>=0.4 ATR14`，且主門檻 efficiency `>=0.50`；鄰域 `0.40／0.60`。ATR14 含決策棒已完成範圍。假說：過去 4 小時方向一致代表突破仍在延續；風險是與舊單棒強度訊號高度相關。

## 第三輪：持倉末段波動收縮

`range_ratio = mean(TR[i-2:i],3) / mean(TR[i-14:i-3],12)`，TR 為已完成棒的 true range，分母嚴格止於決策棒前 3 根。主門檻 `<=0.75`，鄰域 `0.65／0.85`。假說：虧損持倉若波動已收斂，有限等待或許能避免在暫時盤整時出場；風險是停滯後繼續逆行，且延長會占用部位。

任何輪未改善全期淨 PnL 與勝率，即判定該預定規則 `REJECTED`；即使兩者改善但獨立事件不足，也只能是 `INSUFFICIENT_SAMPLE`／`SHADOW_ONLY`，不部署。沒有真實 forward OOS 的限制必須保留。
