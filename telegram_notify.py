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
        print("[ERROR] 請確認 .env 已正確設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")
        return

    # 當未提供完整 message 內容時，嘗試自動組裝訊號格式
    if not message and signal and symbol and price:
        direction = "BUY" if signal.upper() == "BUY" else "SELL"
        msg_lines = [
            f"<b>[Signal] {direction} {symbol}</b>",
            f"Price: {price}",
        ]
        if strategy:
            msg_lines.append(f"Strategy: {strategy}")
        if interval:
            msg_lines.append(f"Interval: {interval}")
        if stop_loss is not None:
            msg_lines.append(f"SL: {stop_loss}")
        if take_profit is not None:
            msg_lines.append(f"TP: {take_profit}")
        msg_lines.append(f"Time: {timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        message = "\n".join(msg_lines)

    elif not message:
        print("[ERROR] 無訊息內容，未發送通知")
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
            print("[TG] sent OK")
        else:
            print(f"[TG] failed: {response.status_code} {response.text}")

    except Exception as e:
        print(f"[TG] error: {e}")