import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pybit.unified_trading import HTTP
from telegram_notify import send_telegram_message

# === 讀取 .env 環境變數 ===
load_dotenv()

# === API Key 配置 ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# === 倉位模型配置 ===
fixed_amount = float(os.getenv("FIXED_AMOUNT", 100))  # 固定部分
percent_amount = float(os.getenv("PERCENT_AMOUNT", 0.3))  # 浮動百分比
max_order_amount = float(os.getenv("MAX_ORDER_AMOUNT", 300))  # 單次安全上限

# === 風控參數 ===
cooldown_seconds = 600  # 反手冷卻時間

# === 建立 Bybit Session ===
def new_session():
    return HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=False,
        demo=True,
        recv_window=10000
    )

session = new_session()

# === 記錄最近交易狀態 (冷卻用) ===
last_trade_time = {}

# === 查詢帳戶餘額 (USDT 可用餘額) ===
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

# === 查詢交易規格（tick size, qty step 等）===
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
        print("❌ 查詢交易規格失敗:", e)
        send_telegram_message(f"❗查詢交易規格失敗: {e}")
        session = new_session()
        return 0.01, 0.001, 0.001

# === 四捨五入價格與數量 ===
def round_to_tick(price, tick_size):
    return round(round(price / tick_size) * tick_size, 8)

def round_to_lot(qty, qty_step, min_qty):
    qty = round(round(qty / qty_step) * qty_step, 8)
    return max(qty, min_qty)

# === 下單核心邏輯 ===
def place_order(symbol, side, price, strategy=None):
    global session, last_trade_time

    now = time.time()
    tick_size, qty_step, min_qty = get_symbol_info(symbol)

    # 反手冷卻邏輯
    if symbol in last_trade_time and now - last_trade_time[symbol] < cooldown_seconds:
        print("⏳ 冷卻中，避免過度頻繁下單")
        return

    price = round_to_tick(price, tick_size)

    # === 倉位計算邏輯 ===
    balance = get_available_balance()
    dynamic_amount = fixed_amount + (max(balance - fixed_amount, 0) * percent_amount)
    total_usd = min(dynamic_amount, max_order_amount)  # 安全上限

    qty = total_usd / price
    qty = round_to_lot(qty, qty_step, min_qty)

    if qty < min_qty:
        send_telegram_message(f"❌ 下單失敗：數量 {qty} 低於最小下單量 {min_qty}")
        return

    try:
        res = session.place_order(
            category="linear",
            symbol=symbol,
            side=side.capitalize(),
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC"
        )
        print(f"✅ {side} 成功下單: {res}")
        send_telegram_message(f"✅ 已市價 {side} {symbol} qty={qty} (總倉位約: {total_usd} USDT)")
        last_trade_time[symbol] = now

        # 執行後續紀錄損益
        record_trade(symbol)

    except Exception as e:
        print("❌ 下單失敗:", e)
        send_telegram_message(f"❌ 下單失敗: {e}")
        session = new_session()

# === 交易紀錄寫入 Excel ===
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

# === 損益撈取邏輯 (下單後寫入最近1小時平倉單) ===
def record_trade(symbol):
    global session
    try:
        now_ts = int(datetime.utcnow().timestamp() * 1000)
        one_hour_ago_ts = now_ts - 1 * 60 * 60 * 1000

        result = session.get_closed_pnl(
            category="linear",
            symbol=symbol,
            limit=100
        )

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

# === Webhook 觸發入口 (供 TradingView 使用) ===
def webhook_execute(data):
    try:
        symbol = data["symbol"]
        action = data["action"]
        price = float(data["price"])
        place_order(symbol, action, price)
    except Exception as e:
        print("❌ webhook 處理失敗:", e)
        send_telegram_message(f"❗webhook 錯誤: {e}")