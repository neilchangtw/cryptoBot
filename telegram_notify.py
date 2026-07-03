"""
Telegram 通知模組（v7）

功能：
  - 發送通知（進場/出場/告警/心跳）— 支援多個 chat_id（逗號分隔，私聊/群組皆可）
  - 接收指令（/cleanup /status /help）— 來源限 TELEGRAM_CHAT_ID 清單內的聊天室
  - 管理員白名單（TELEGRAM_ADMIN_IDS）— 控制類指令的把關資料由此提供，主程式執行檢查
  - 指令回覆導向：回覆只發回「下指令的那個聊天室」，不廣播（用 thread-local，
    與主循環的廣播通知互不干擾）
"""
import os
import logging
import threading
import requests
from dotenv import load_dotenv
from datetime import datetime

import paths  # 多實例：訊息前綴顯示實例名

load_dotenv()

logger = logging.getLogger("telegram")

# 記錄已處理的最新 update_id，避免重複處理
_last_update_id = 0

# 指令回覆導向（thread-local）：指令監聽執行緒設定後，該執行緒發的訊息只回原聊天室；
# 主循環執行緒從未設定 → 通知照常廣播到所有 chat_id
_reply_local = threading.local()


def get_chat_ids():
    """TELEGRAM_CHAT_ID 支援逗號分隔多值（私聊 id / 群組負數 id 混用皆可）。"""
    raw = os.getenv("TELEGRAM_CHAT_ID", "") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def get_admin_ids():
    """TELEGRAM_ADMIN_IDS：管理員 user id 集合（逗號分隔）。
    空集合 = 未設定 → 主程式維持舊行為（授權聊天室內任何人可用全部指令）。"""
    raw = os.getenv("TELEGRAM_ADMIN_IDS", "") or ""
    return {c.strip() for c in raw.split(",") if c.strip()}


def set_reply_target(chat_id):
    """設定本執行緒後續 send 的目標聊天室（None = 恢復廣播）。"""
    _reply_local.chat_id = chat_id

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
    chat_ids = get_chat_ids()

    if not token or not chat_ids:
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

    # 多實例：訊息開頭標上實例名，讓多人各自確認是自己的（單人時 label 為空、不加）
    label = paths.instance_name()
    if label:
        import html as _html
        message = f"👤 <b>{_html.escape(label)}</b>\n{message}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    # 指令回覆導向：本執行緒有設定目標 → 只發回原聊天室；否則廣播到全部 chat_id
    reply_to = getattr(_reply_local, "chat_id", None)
    targets = [reply_to] if reply_to else chat_ids

    for cid in targets:
        data = {
            "chat_id": cid,
            "text": message,
            "parse_mode": "HTML"  # 使用 HTML 解析模式，更穩定
        }
        for attempt in range(3):
            try:
                response = requests.post(url, data=data, timeout=10)
                if response.status_code == 200:
                    break
                # 429 = rate limit，等一下再試
                if response.status_code == 429:
                    import time; time.sleep(2)
                    continue
                print(f"[TG] failed ({cid}): {response.status_code} {response.text}")
                break
            except Exception as e:
                if attempt < 2:
                    import time; time.sleep(1)
                else:
                    print(f"[TG] error after 3 attempts ({cid}): {e}")


# ══════════════════════════════════════════════════════════════
#  Telegram 指令接收
# ══════════════════════════════════════════════════════════════

def get_pending_commands():
    """輪詢 Telegram getUpdates，回傳新指令列表。

    Returns:
        list[tuple[str, str, str]]: (指令文字, 發訊人 user id, 來源 chat id)。
        來源限 TELEGRAM_CHAT_ID 清單內的聊天室；發訊人 id 供主程式做管理員白名單檢查。
    """
    global _last_update_id
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed = set(get_chat_ids())
    if not token or not allowed:
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
            # 只處理來自授權 chat_id 清單的訊息
            chat_id = str(msg.get("chat", {}).get("id"))
            if chat_id not in allowed:
                continue
            text = msg.get("text", "").strip()
            if text.startswith("/"):
                from_id = str(msg.get("from", {}).get("id", ""))
                commands.append((text, from_id, chat_id))
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