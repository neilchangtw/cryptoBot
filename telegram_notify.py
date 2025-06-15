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
            f"🚨 <b>交易訊號通知</b>",
            f"{color_emoji} <b>動作：{signal.upper()}</b>",
            f"📈 幣種：{symbol}",
            f"💰 價格：{price}",
        ]
        if strategy:
            msg_lines.append(f"📊 策略：{strategy}")
        if interval:
            msg_lines.append(f"⏰ 週期：{interval}")
        if stop_loss is not None:
            msg_lines.append(f"🛑 停損：{stop_loss}")
        if take_profit is not None:
            msg_lines.append(f"🎯 停利：{take_profit}")
        msg_lines.append(f"📅 時間：{timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        message = "\n".join(msg_lines)

    elif not message:
        print("❌ 無訊息內容，未發送通知")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"  # 使用 HTML 解析模式，更穩定
    }

    try:
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("✅ Telegram 通知已發送")
        else:
            print(f"⚠️ Telegram 傳送失敗，狀態碼: {response.status_code}, 回應: {response.text}")

    except Exception as e:
        print(f"❌ 發送 Telegram 通知失敗：{e}")