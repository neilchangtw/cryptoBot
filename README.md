# 🚀 自動化加密貨幣量化交易系統（Bybit）

支援 TradingView 策略訊號、Webhook 快速自動下單、完整風控、完整交易紀錄、Telegram 快訊通知、實盤穩定部署。

---

## 📦 專案架構

cryptoBot/
│
├── webhook_listener.py # TradingView webhook 監聽與下單入口
├── bybit_trade.py # Bybit 下單邏輯（含限價單 + 槓桿 + 倉位計算 + 損益紀錄）
├── telegram_notify.py # Telegram 通知模組
├── trade_pnl_log.xlsx # 每次平倉自動寫入損益 Excel
├── .env # 環境變數設定檔
├── requirements.txt # 套件需求列表
└── venv/ # Python 虛擬環境（本地部署用）


---

## 🔧 環境變數設定 `.env`

請先建立 `.env` 檔，內容如下：

```env
# Bybit API (Unified Account)
BYBIT_API_KEY=你的BybitApiKey
BYBIT_API_SECRET=你的BybitApiSecret
BYBIT_BASE_URL=https://api.bybit.com

# Telegram 通知
TELEGRAM_BOT_TOKEN=你的TelegramBotToken
TELEGRAM_CHAT_ID=你的TelegramChatId

# 交易參數
FIXED_AMOUNT=100          # 單次保證金投入 (USDT)
LEVERAGE=20               # 槓桿倍數
MAX_ORDER_AMOUNT=300      # 最大單次下單額度 (USDT)
COOLDOWN_SECONDS=600      # 單商品冷卻時間 (秒)

```

## 1.TradingView 策略發出 Webhook 訊號格式
{
  "action": "BUY",
  "symbol": "{{ticker}}",
  "price": "{{close}}",
  "sl": "{{longSL}}",
  "tp": "{{longTP}}",
  "strategy": "V-Ultimate-PRO-AI",
  "interval": "{{interval}}"
}

## 2.Webhook Listener 處理 (webhook_listener.py)
接收 TradingView webhook 快訊
驗證訊號參數（動作、幣種、價格）
發送進場訊號通知至 Telegram
檢查風控條件：
冷卻時間：避免重複下單
價格變動門檻：±10 USDT 內不重複下單
符合條件後執行自動下單

## 3.Bybit 下單邏輯 (bybit_trade.py)
自動計算倉位：
槓桿計算
最低倉位限制
自動設定交易對槓桿 (Unified Account API v5)
限價單 (Limit + PostOnly) 下單
平倉後自動記錄損益至 Excel (trade_pnl_log.xlsx)
所有交易行為皆推送 Telegram 通知

## 4.Telegram 通知模組 (telegram_notify.py)
下單成功、冷卻跳過、略過條件、平倉損益、錯誤皆推送通知
支援 HTML 格式化，利於訊息閱讀清晰

## 建立虛擬環境：
1.建立虛擬環境
python -m venv venv

2.啟動虛擬環境 (Windows)
venv\Scripts\activate

2.1或 (Mac/Linux)
source venv/bin/activate

## 安裝套件:
pip install -r requirements.txt

requirements.txt 內容範例：
python-dotenv
requests
flask
pybit
openpyxl

## 啟動系統:
python webhook_listener.py
系統會監聽本地 5000 端口，提供 TradingView Webhook 呼叫。
ex: http://你的伺服器IP:5000/webhook

