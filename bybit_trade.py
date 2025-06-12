import os
import time
from datetime import datetime
from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from pybit.unified_trading import HTTP
from telegram_notify import send_telegram_message

# 讀取 .env 環境變數
load_dotenv()

# === 讀取 API 金鑰設定 ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")

# === 倉位參數設定 ===
fixed_amount = float(os.getenv("FIXED_AMOUNT", 100))
percent_amount = float(os.getenv("PERCENT_AMOUNT", 0.3))
max_order_amount = float(os.getenv("MAX_ORDER_AMOUNT", 300))

# === 防呆風控設定 ===
cooldown_seconds = int(os.getenv("COOLDOWN_SECONDS", 600))

# 建立 Bybit Session
def new_session():
    return HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=False,
        demo=True,
        recv_window=10000
    )

session = new_session()

# 記錄最近下單時間 (避免反手頻繁)
last_trade_time = {}

# 查詢帳戶可用資金
def get_available_balance():
    global session
    try:
        result = session.get_wallet_balance(accountType="UNIFIED")
        usdt_balance = float(result["result"]["list"][0]["totalEquity"])
        return usdt_balance
    except Exception as e:
        print("❌ 查詢帳戶餘額失敗:", e)
        send_telegram_message(f"❗查詢帳戶餘額失敗: {e}")
        session = new_session()
        return 0.0

# 查詢商品 tickSize 與 lotSize
def get_symbol_info(symbol):
    global session
    try:
        res = session.get_instruments_info(category="linear", symbol=symbol)
        info = res["result"]["list"][0]
        tick_size = float(info["priceFilter"]["tickSize"])
        qty_step = float(info["lotSizeFilter"]["qtyStep"])
        min_qty = float(info["lotSizeFilter"]["minOrderQty"])
        return tick_size, qty_step, min_qty
    except Exception as e:
        print("❌ 查詢商品資訊失敗:", e)
        send_telegram_message(f"❗查詢商品資訊失敗: {e}")
        session = new_session()
        return 0.01, 0.001, 0.001

# 四捨五入價格與數量
def round_to_tick(price, tick_size):
    return round(round(price / tick_size) * tick_size, 8)

def round_to_lot(qty, qty_step, min_qty):
    qty = round(round(qty / qty_step) * qty_step, 8)
    return max(qty, min_qty)

# 核心下單邏輯
def place_order(symbol, side, price, stop_loss=None, take_profit=None):
    global session, last_trade_time

    now = time.time()
    tick_size, qty_step, min_qty = get_symbol_info(symbol)

    # 冷卻判斷
    if symbol in last_trade_time and now - last_trade_time[symbol] < cooldown_seconds:
        print("⏳ 冷卻中，避免過度頻繁下單")
        return

    price = round_to_tick(price, tick_size)
    balance = get_available_balance()

    # 計算倉位金額
    dynamic_amount = fixed_amount + (max(balance - fixed_amount, 0) * percent_amount)
    total_usd = min(dynamic_amount, max_order_amount)
    qty = total_usd / price
    qty = round_to_lot(qty, qty_step, min_qty)

    if qty < min_qty:
        send_telegram_message(f"❌ 下單失敗：數量 {qty} 低於最小下單量 {min_qty}")
        return

    # 計算止損止盈價格 (有傳入則使用，否則不帶)
    sl_price = round_to_tick(float(stop_loss), tick_size) if stop_loss else None
    tp_price = round_to_tick(float(take_profit), tick_size) if take_profit else None

    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC"
        }
        if sl_price:
            params["stopLoss"] = str(sl_price)
        if tp_price:
            params["takeProfit"] = str(tp_price)

        res = session.place_order(**params)

        print(f"✅ {side} 成功下單: {res}")
        send_telegram_message(
            f"✅ 已市價 {side} {symbol} qty={qty} SL={sl_price} TP={tp_price} (總倉位約: {total_usd} USDT)"
        )
        last_trade_time[symbol] = now

        # 下單後觸發紀錄
        record_trade(symbol)

    except Exception as e:
        print("❌ 下單失敗:", e)
        send_telegram_message(f"❌ 下單失敗: {e}")
        session = new_session()

# Excel 寫入
def log_pnl_to_xlsx_trade_record(records: list):
    filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_pnl_log.xlsx")
    headers = ["交易對", "工具", "平倉價格", "訂單數量", "交易類型", "已結盈虧", "成交時間"]

    try:
        file_exists = os.path.exists(filename)
        if not file_exists:
            wb = Workbook()
            ws = wb.active
            ws.append(headers)
        else:
            wb = load_workbook(filename)
            ws = wb.active

        for record in records:
            ws.append([
                record["symbol"],
                "USDT 永續",
                record["exit_price"],
                record["qty"],
                record["side"],
                record["pnl"],
                record["close_time"]
            ])

        wb.save(filename)
        wb.close()
        print(f"📗 交易紀錄成功寫入 {len(records)} 筆")

    except Exception as e:
        print("❌ 寫入 XLSX 失敗：", e)
        send_telegram_message(f"❗寫入交易紀錄 XLSX 失敗：{e}")

# 撈取平倉損益寫入紀錄
def record_trade(symbol):
    global session
    try:
        now_ts = int(datetime.utcnow().timestamp() * 1000)
        one_hour_ago_ts = now_ts - 1 * 60 * 60 * 1000

        result = session.get_closed_pnl(category="linear", symbol=symbol, limit=100)
        closed_records = result["result"]["list"]
        trade_records = []

        for record in closed_records:
            updated_time = int(record["updatedTime"])
            if updated_time < one_hour_ago_ts:
                continue

            pnl = float(record["closedPnl"])
            qty = float(record["qty"])
            exit_price = float(record["avgExitPrice"])
            side = record["side"]
            close_time = datetime.fromtimestamp(updated_time / 1000).strftime("%Y-%m-%d %H:%M:%S")

            trade_records.append({
                "symbol": symbol,
                "exit_price": exit_price,
                "qty": qty,
                "side": side,
                "pnl": pnl,
                "close_time": close_time
            })

        if trade_records:
            log_pnl_to_xlsx_trade_record(trade_records)

    except Exception as e:
        print("❌ 撈取平倉紀錄失敗：", e)
        send_telegram_message(f"❗平倉紀錄失敗: {e}")
        session = new_session()