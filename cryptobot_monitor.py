import os
import pickle

from dotenv import load_dotenv
from pybit.unified_trading import HTTP

from telegram_notify import send_telegram_message

# 初始化
load_dotenv()
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False, demo=True)

STATE_FILE = "trailing_state.pkl"

try:
    with open(STATE_FILE, "rb") as f:
        state = pickle.load(f)
except:
    state = {}

def save_state():
    with open(STATE_FILE, "wb") as f:
        pickle.dump(state, f)

def monitor_positions():
    try:
        all_positions = session.get_positions(category="linear")["result"]["list"]
        for pos in all_positions:
            symbol = pos["symbol"]
            size = float(pos["size"])
            if size == 0:
                continue

            side = pos["side"] # "Buy" or "Sell"
            entry_price = float(pos["avgEntryPrice"])
            mark_price = float(pos["markPrice"])
            pos_key = f"{symbol}_{side}"

            # 計算浮盈
            floating_pnl = (mark_price - entry_price) if side == "Buy" else (entry_price - mark_price)

            # 狀態追蹤
            st = state.get(pos_key, {"保本": False, "trailing10U": False, "trailing8U": False})

            # 浮盈 > 10U，止損移到進場價+3U
            if not st["保本"] and floating_pnl > 10:
                new_sl = entry_price + 3 if side == "Buy" else entry_price - 3
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=str(new_sl))
                st["保本"] = True

            # 浮盈 > 20U，止損再移到進場價+10U
            if st["保本"] and not st["trailing10U"] and floating_pnl > 20:
                new_sl = entry_price + 10 if side == "Buy" else entry_price - 10
                session.set_trading_stop(category="linear", symbol=symbol, stopLoss=str(new_sl))
                st["trailing10U"] = True

            # 浮盈 > 30U，啟用trailing stop，距離10U
            if st["trailing10U"] and not st["trailing8U"] and floating_pnl > 30:
                ts_dist = 10
                session.set_trading_stop(
                    category="linear", symbol=symbol, trailingStop=str(ts_dist),
                    triggerDirection=1 if side == "Buy" else 2
                )
                st["trailing8U"] = True

            # 浮盈 > 40U，trailing stop 距離收緊到8U
            if st["trailing8U"] and floating_pnl > 40:
                ts_dist = 8
                session.set_trading_stop(
                    category="linear", symbol=symbol, trailingStop=str(ts_dist),
                    triggerDirection=1 if side == "Buy" else 2
                )

            state[pos_key] = st

        save_state()
    except Exception as e:
        send_telegram_message(f"❌ 監控腳本異常：{e}")

if __name__ == "__main__":
    monitor_positions()