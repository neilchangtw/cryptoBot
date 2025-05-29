import os
import requests
from dotenv import load_dotenv

load_dotenv()  # ⬅️ 加上這一行才能正確載入 .env 變數

def send_telegram_message(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ 請確認 .env 已正確設定 BOT_TOKEN 與 CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(url, data=data)
        response.raise_for_status()
        print("✅ 成功發送 Telegram 通知")
    except Exception as e:
        print(f"❌ 發送失敗: {e}")
