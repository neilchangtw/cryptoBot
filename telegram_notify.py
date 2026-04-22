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
#  v5 通知格式化 helpers
# ══════════════════════════════════════════════════════════════

def format_v5_entry(direction, symbol, entry, rsi, safenet_sl, tp1,
                    atr, atr_pctile, ema21_dev, rsi_1h, rsi_1h_prev,
                    ts_deadline, balance, safenet_pct=3):
    """v5 進場信號通知"""
    return (f"<b>[v5 {direction}] {symbol}</b>\n"
            f"價格: {entry:.2f}  RSI: {rsi:.1f}\n"
            f"安全網 SL: {safenet_sl:.2f} (-{safenet_pct:.0f}%)\n"
            f"TP1: {tp1:.2f} (+1.5x ATR)\n"
            f"ATR(5m): {atr:.2f} (pctile: {atr_pctile:.0f})\n"
            f"EMA21 偏離: {ema21_dev:+.1f}%\n"
            f"1h RSI: {rsi_1h:.1f} (prev: {rsi_1h_prev:.1f})\n"
            f"時間止損: {ts_deadline}\n"
            f"餘額: {balance:.2f} USDT")


def format_v5_tp1(direction, symbol, entry, exit_price, pnl_pct,
                  atr, duration_min, close_side, close_qty):
    """v5 TP1 全平通知"""
    return (f"<b>[TP1 全平] {direction} {symbol}</b>\n"
            f"進場: {entry:.2f} → 出場: {exit_price:.2f} ({pnl_pct:+.2f}%)\n"
            f"ATR: {atr:.2f}  持倉: {duration_min:.0f} min\n"
            f"已全平 100%: {close_side} {close_qty}")


def format_v5_time_stop(direction, symbol, entry, exit_price, pnl_pct,
                        hours, close_side, close_qty):
    """v5 時間止損通知"""
    return (f"<b>[時間止損] {direction} {symbol}</b>\n"
            f"進場: {entry:.2f} → 出場: {exit_price:.2f} ({pnl_pct:+.2f}%)\n"
            f"持倉超過 {hours}h 未到 TP1，認錯出場\n"
            f"全平: {close_side} {close_qty}")


def format_v5_safenet(direction, symbol, entry, approx_exit, pnl_pct):
    """v5 安全網觸發通知"""
    return (f"<b>[安全網觸發] {direction} {symbol}</b>\n"
            f"進場: {entry:.2f} → SL 觸發 ≈ {approx_exit:.2f} ({pnl_pct:+.2f}%)\n"
            f"⚠️ 極端行情觸發安全網止損")


def format_v5_heartbeat(balance, unrealized, long_count, short_count,
                        max_pos, oldest_remaining_min=None,
                        scan_count=0, signal_count=0):
    """v5 每小時心跳摘要"""
    ts_text = ""
    if oldest_remaining_min is not None:
        h = int(oldest_remaining_min // 60)
        m = int(oldest_remaining_min % 60)
        ts_text = f"\n最近 TimeStop: {h}h{m:02d}m"
    return (f"<b>--- 每小時摘要 ---</b>\n"
            f"餘額: {balance:.2f} USDT  未實現: {unrealized:+.2f}\n"
            f"持倉: {long_count}L / {short_count}S (上限 {max_pos}/{max_pos})\n"
            f"掃描: {scan_count} 次  信號: {signal_count} 次"
            f"{ts_text}")


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