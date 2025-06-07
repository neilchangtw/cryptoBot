import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def send_telegram_message(message=None, signal=None, symbol=None, price=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ 請確認 .env 已正確設定 BOT_TOKEN 與 CHAT_ID")
        return

    # 若未給定完整訊息，使用 signal 組裝
    if not message and signal and symbol and price:
        color_emoji = "🟢" if signal.upper() == "BUY" else "🔴"
        message = (
            f"🚨 *交易訊號通知*\n"
            f"{color_emoji} *動作：{signal.upper()}*\n"
            f"📈 幣種：{symbol}\n"
            f"💰 價格：{price}\n"
            f"📅 時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    elif not message:
        print("❌ 無訊息可發送（未提供 message 或必要欄位）")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, data=data)
        response.raise_for_status()
        print("✅ 成功發送 Telegram 通知")
    except Exception as e:
        print(f"❌ 發送失敗: {e}")
