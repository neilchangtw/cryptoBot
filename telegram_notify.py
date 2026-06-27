"""
Telegram 通知模組（v6）

功能：
  - 發送通知（進場/出場/告警/心跳）
  - 接收指令（/cleanup /status /help）
"""
import os
import logging
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

logger = logging.getLogger("telegram")

# 記錄已處理的最新 update_id，避免重複處理
_last_update_id = 0

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

    for attempt in range(3):
        try:
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                return
            # 429 = rate limit，等一下再試
            if response.status_code == 429:
                import time; time.sleep(2)
                continue
            print(f"[TG] failed: {response.status_code} {response.text}")
            return
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(1)
            else:
                print(f"[TG] error after 3 attempts: {e}")


# ══════════════════════════════════════════════════════════════
#  Telegram 指令接收
# ══════════════════════════════════════════════════════════════

def get_pending_commands():
    """輪詢 Telegram getUpdates，回傳新指令列表。

    Returns:
        list[str]: 指令文字列表，例如 ["/cleanup", "/status"]
    """
    global _last_update_id
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return []

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": _last_update_id + 1, "timeout": 0, "limit": 10}
    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("ok"):
            return []

        commands = []
        for update in data.get("result", []):
            uid = update["update_id"]
            _last_update_id = max(_last_update_id, uid)
            msg = update.get("message", {})
            # 只處理來自授權 chat_id 的訊息
            if str(msg.get("chat", {}).get("id")) != str(chat_id):
                continue
            text = msg.get("text", "").strip()
            if text.startswith("/"):
                commands.append(text)
        return commands
    except Exception as e:
        logger.debug(f"getUpdates error: {e}")
        return []


def skip_old_updates():
    """啟動時跳過所有舊訊息，避免執行歷史指令。"""
    global _last_update_id
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": -1, "limit": 1}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("result", [])
            if results:
                _last_update_id = results[-1]["update_id"]
                logger.info(f"Skipped old Telegram updates, last_id={_last_update_id}")
    except Exception:
        pass