import os
import requests
from dotenv import load_dotenv
from datetime import datetime

# 載入 .env 設定檔
load_dotenv()

# === 發送 Telegram 訊息主函式 ===
def send_telegram_message(
        message=None,
        signal=None,
        symbol=None,
        price=None,
        strategy=None,
        interval=None,
        stop_loss=None,
        take_profit=None,
        timestamp=None
):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ 請確認 .env 已正確設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")
        return

    # 當未提供完整 message 內容時，嘗試自動組裝訊號格式
    if not message and signal and symbol and price:
        color_emoji = "🟢" if signal.upper() == "BUY" else "🔴"
        msg_lines = [
            f"🚨 *交易訊號通知*",
            f"{color_emoji} *動作：{signal.upper()}*",
            f"📈 幣種：{symbol}",
            f"💰 價格：{price}",
        ]
        if strategy:   msg_lines.append(f"📊 策略：{strategy}")
        if interval:   msg_lines.append(f"⏰ 週期：{interval}")
        if stop_loss:  msg_lines.append(f"🛑 停損：{stop_loss}")
        if take_profit:msg_lines.append(f"🎯 停利：{take_profit}")
        msg_lines.append(f"📅 時間：{timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        message = "\n".join(msg_lines)

    elif not message:
        print("❌ 無訊息內容，未發送通知")
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
        print("✅ Telegram 通知已發送")
    except Exception as e:
        print(f"❌ 發送 Telegram 通知失敗：{e}")
