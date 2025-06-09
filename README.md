cryptoBot/
│
├── main.py # 單次測試用（手動呼叫下單功能）
├── server.py # Webhook 接收主伺服器（可供 TradingView Webhook 呼叫）
├── telegram_notify.py # 發送 Telegram 快訊通知
├── bybit_trade.py # 下單邏輯，含反手平倉、止損、自動計算倉位
├── webhook_listener.py # 綜合功能接收端，包含冷卻/價格差判斷 + 下單執行
├── trade_pnl_log.csv # 每次平倉後自動記錄損益紀錄
├── .env # 儲存 API 金鑰與 BOT 設定
├── requirements.txt # 所需套件列表
└── venv/ # Python 虛擬環境資料夾


---

## 🔧 使用設定（`.env`）
TELEGRAM_BOT_TOKEN=你的_bot_token
TELEGRAM_CHAT_ID=你的_chat_id
BYBIT_API_KEY=你的_api_key
BYBIT_API_SECRET=你的_api_secret
BYBIT_BASE_URL=https://api.bybit.com
ORDER_USD_AMOUNT=100    # 每筆下單 USDT 數量

---

系統架構與流程
1.策略透過 TradingView 訊號來源 Webhook 發出 JSON 快訊
2.Webhook 接收處理 (webhook_listener.py)
-驗證與解析快訊資料（動作、幣種、價格）。
-發送交易訊息至 Telegram。
-檢查是否符合下單條件（避免重複下單）：
-冷卻時間：10 分鐘內不重複下單。
-價格變動：至少 ±5 USDT 才重新下單。
-檢查目前是否持有反向倉位：
-若有，先進行平倉。
-平倉後，記錄 PnL（損益）至 pnl_log.csv。
-最後根據新訊號進行 市價單下單（含 10% 止損）。

3.Bybit 下單邏輯 (bybit_trade.py)
-讀參數/風控
-檢查冷卻/最大單/價格/數量
-如需反手，先平倉、記錄損益
-市價單下單（帶SL/TP）
-失敗自動重連3次
-成功/失敗都推播Telegram

4.Telegram 快訊通知 (telegram_notify.py)
-所有下單、略過條件、錯誤、平倉 PnL 都會透過 Telegram 通知。