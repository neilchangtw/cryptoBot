import os
import pickle
import requests
from dotenv import load_dotenv
from pybit.unified_trading import HTTP
from telegram_notify import send_telegram_message

SYMBOLS = ["ETHUSDT"]
STATE_FILE = "trailing_state.pkl"
ATR_LENGTH = 14
ATR_MULTIPLIER_STOP = 1.5    # 保本止損移動距離倍數
ATR_MULTIPLIER_TRAIL = 1.5   # trailing stop 距離倍數
THRESHOLD_PROFIT_STOP = 30   # 浮盈多少U移保本
THRESHOLD_PROFIT_TRAIL = 60  # 浮盈多少U啟用trailing stop

load_dotenv()
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False, demo=True)

try:
    with open(STATE_FILE, "rb") as f:
        state = pickle.load(f)
except:
    state = {}

def save_state():
    with open(STATE_FILE, "wb") as f:
        pickle.dump(state, f)

def get_atr(symbol, interval="60", length=ATR_LENGTH):
    url = f"https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": length + 1
    }
    resp = requests.get(url, params=params)
    data = resp.json()
    if not data.get("result", {}).get("list"):
        return 10  # fallback
    klines = data["result"]["list"]
    klines = [list(map(float, k)) for k in klines][::-1]
    trs = []
    for i in range(1, len(klines)):
        high = klines[i][2]
        low = klines[i][3]
        prev_close = klines[i-1][4]
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 10

def get_tp_sl_from_open_orders(symbol, side, entry_price):
    """自動從 open orders 抓TP/SL價格（同一倉位僅抓最遠的）"""
    tp_price, sl_price = None, None
    try:
        open_orders = session.get_open_orders(category="linear", symbol=symbol)
        order_list = open_orders.get("result", {}).get("list", [])
        for order in order_list:
            if not order.get("reduceOnly", False):
                continue
            price = float(order["price"])
            if side == "Buy":
                if price > entry_price and (tp_price is None or price > tp_price):
                    tp_price = price
                if price < entry_price and (sl_price is None or price < sl_price):
                    sl_price = price
            else:
                if price < entry_price and (tp_price is None or price < tp_price):
                    tp_price = price
                if price > entry_price and (sl_price is None or price > sl_price):
                    sl_price = price
    except Exception as e:
        print(f"抓委託單TP/SL時異常: {e}")
    return tp_price, sl_price

def is_bad_request_exception(e):
    # pybit HTTPException 內容有 response，如果是 requests 物件也有 status_code
    if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
        return e.response.status_code == 400
    msg = str(e)
    return "400" in msg or "Bad request" in msg

def monitor_positions():
    try:
        # send_telegram_message("✅ 監控腳本排程已啟動，心跳測試通知")

        all_positions = []
        for symbol in SYMBOLS:
            try:
                result = session.get_positions(category="linear", symbol=symbol)
                poslist = result.get("result", {}).get("list", [])
                all_positions += [p for p in poslist if float(p.get("size", 0)) != 0]
            except Exception as sub_e:
                if is_bad_request_exception(sub_e):
                    print(f"⚠️ 略過 {symbol}：持倉查詢400 Bad Request（無此倉位或Demo站bug）")
                    continue
                send_telegram_message(f"⚠️ 查詢 {symbol} 持倉異常: {sub_e}")

        for pos in all_positions:
            symbol = pos.get("symbol")
            size = float(pos.get("size", 0))
            if size == 0:
                continue
            # 必要欄位檢查
            if "avgEntryPrice" not in pos or "markPrice" not in pos or "side" not in pos:
                continue

            side = pos["side"]
            entry_price = float(pos["avgEntryPrice"])
            mark_price = float(pos["markPrice"])
            pos_key = f"{symbol}_{side}"

            # 抓 open order 的 TP/SL
            tp_price, sl_price = get_tp_sl_from_open_orders(symbol, side, entry_price)

            # 讀取/初始化倉位狀態與最大浮盈
            st = state.get(
                pos_key,
                {
                    "保本": False,
                    "trailing": False,
                    "max_profit": 0
                }
            )

            # 每分鐘用mark_price更新最大浮盈
            if side == "Buy":
                cur_profit = (mark_price - entry_price) * size
            else:
                cur_profit = (entry_price - mark_price) * size

            if cur_profit > st.get("max_profit", 0):
                st["max_profit"] = cur_profit

            atr = get_atr(symbol)

            # 保本觸發：最大浮盈>THRESHOLD_PROFIT_STOP，止損移到進場價+1.5ATR（多單）且不得高於TP；空單反向
            if not st["保本"] and st["max_profit"] > THRESHOLD_PROFIT_STOP:
                if side == "Buy":
                    new_sl = entry_price + ATR_MULTIPLIER_STOP * atr
                    if tp_price is not None:
                        new_sl = min(new_sl, tp_price)
                else:
                    new_sl = entry_price - ATR_MULTIPLIER_STOP * atr
                    if tp_price is not None:
                        new_sl = max(new_sl, tp_price)
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=str(new_sl))
                # 保本觸發通知
                msg = (f"🔔 {symbol} {side} 保本止損觸發\n"
                       f"進場價: {entry_price}\n"
                       f"現價: {mark_price}\n"
                       f"最大浮盈: {st['max_profit']:.2f} U\n"
                       f"新止損: {new_sl}\n"
                       f"TP(委託): {tp_price}\n"
                       f"ATR: {atr:.2f}")
                send_telegram_message(msg)
                st["保本"] = True

            # 浮盈>THRESHOLD_PROFIT_TRAIL啟動trailing stop，距離1.5ATR
            if st["保本"] and not st["trailing"] and st["max_profit"] > THRESHOLD_PROFIT_TRAIL:
                ts_dist = ATR_MULTIPLIER_TRAIL * atr
                session.set_trading_stop(
                    category="linear", symbol=symbol, trailingStop=str(ts_dist),
                    triggerDirection=1 if side == "Buy" else 2
                )
                # trailing stop 觸發通知
                msg = (f"🔁 {symbol} {side} 啟動Trailing Stop\n"
                       f"進場價: {entry_price}\n"
                       f"現價: {mark_price}\n"
                       f"最大浮盈: {st['max_profit']:.2f} U\n"
                       f"Trailing距離: {ts_dist:.2f}\n"
                       f"TP(委託): {tp_price}\n"
                       f"ATR: {atr:.2f}")
                send_telegram_message(msg)
                st["trailing"] = True

            state[pos_key] = st

        save_state()
    except Exception as e:
        send_telegram_message(f"❌ 監控腳本異常：{e}")

if __name__ == "__main__":
    monitor_positions()