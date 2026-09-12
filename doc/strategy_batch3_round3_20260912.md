# 第三批第3輪 O：SafeNet雙參考觸發預登記

M經濟REJECTED；N模型0直接事件，樣本不足且缺歷史入帳時點。第三輪只选O；另考慮清算流來源，但V21/V36已記錄缺長歷史，沒有新資料不重跑；未實現浮虧風控容易重疊既有曝險gate，未選、未算事件或收益。

查重：先前premium是進場資訊overlay，SafeNet距離／滑價是既有止損参数；未查到「保留contract保護並增加mark觸發」的完整時序回放。V14研究引擎以contract high/low觸發；當前binance_trade.py `_place_stop_order`沒有明訂workingType。只讀本機程式，沒有查VPS訂單設定或更改任何停損。

主候選：保留原contract SafeNet；進場後額外監控mark，L mark<=entry*(1-0.035)或S mark>=entry*(1+0.04)時，與原contract停損擇先觸發且只平同方向實際剩餘倉位。原閾值、名目、槓桿不增加，原contract保護不取消；不依MFE/MAE調整。不用mark作成交價，實際執行仍是合約市價。假說是mark先反映風險時提早離場，降低尾部損失。

[Binance官方交易API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)列出MARK_PRICE／CONTRACT_PRICE的workingType。這僅為可表達此機制的規格參考，不代表本機雙觸發、取消競態、重複closePosition已通過實盤驗證。沒有下單。

決策必須是當下已收到的mark tick，精確到原contract觸發及成交／取消的先後。當根1h高低價是事後彙總，**不能在小時開始預知，也不能拿來當小時內可執行指令**。本次只能用已凍結mark 1h與基準原持倉確認可能受影響的bar：前一bar結束仍持倉，下一bar的mark穿越原stop。區分mark-only／both-touch；both-touch不代表同時、不代表同價、也不能排除先後差異。bar內時刻未知，群數僅為診斷，不能冒充已認證獨立事件。

可用性先查凍結mark時間與OHLC品質、原持倉狀態因果關係。真實received／修訂及contract/mark雙序列tick與市價成交時序不足時，DATA LIMITED，不計候選PnL；不得套用原contract最低／最高的25%穿透公式到mark觸發補出假的成交價。仍保留全30／後15事件門檻，不能改stop距離增加樣本。

無鄰域（0個）。若資料與主候選日後全部過關，延遲壓測用1／2秒market close及三成本0/2/5bp；依round1固定early/late切分、完整雙向狀態、funding、期末倉與mark帳本以及相同經濟門檻、WF/bootstrap/多重比較。這次若門檻不過即第三輪結案，不進第四輪、不啟動背景收集。
