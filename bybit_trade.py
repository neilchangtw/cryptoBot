import os
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pybit.unified_trading import HTTP

from telegram_notify import send_telegram_message

# === 載入 .env 配置 ===
load_dotenv()

# === API 與風控全域參數 ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
usd_amount = float(os.getenv("ORDER_USD_AMOUNT", "100"))

# --- 風控參數 ---
max_loss_per_order = float(os.getenv("MAX_LOSS_PER_ORDER", "30"))
max_loss_per_day   = float(os.getenv("MAX_LOSS_PER_DAY", "100"))
max_qty_per_order  = float(os.getenv("MAX_QTY_PER_ORDER", "0.5"))
tick_size_default  = 0.01

# --- 冷卻與狀態記錄 ---
cooldown_seconds = 600
last_trade_time = {}
last_trade_price = {}
last_trade_side = {}
trade_halted_today = False

# === 建立 Bybit session (主網 DEMO) ===
def new_session():
    return HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=False,
        demo=True,
        recv_window=10000
    )

session = new_session()

# === 查詢 tick size ===
def get_tick_size(symbol):
    global session
    try:
        response = session.get_instruments_info(category="linear", symbol=symbol)
        info = response["result"]["list"][0]
        tick_size = float(info.get("priceFilter", {}).get("tickSize", tick_size_default))
        return tick_size
    except Exception as e:
        print("❌ 查詢 tick size 失敗，預設 0.01：", e)
        send_telegram_message(message=f"❗查詢 {symbol} tick size 失敗: {e}")
        session = new_session()
        return tick_size_default

# === 查詢 lot size ===
def get_lot_size(symbol):
    global session
    try:
        response = session.get_instruments_info(category="linear", symbol=symbol)
        info = response["result"]["list"][0]
        min_qty = float(info.get("lotSizeFilter", {}).get("minOrderQty", 0.01))
        qty_step = float(info.get("lotSizeFilter", {}).get("qtyStep", 0.01))
        return min_qty, qty_step
    except Exception as e:
        print("❌ 查詢 lot size 失敗，預設 0.01：", e)
        send_telegram_message(message=f"❗查詢 {symbol} lot size 失敗: {e}")
        session = new_session()
        return 0.01, 0.01

# === 價格與數量四捨五入 ===
def round_to_tick(price, symbol):
    tick = get_tick_size(symbol)
    return round(round(price / tick) * tick, 8)

def round_to_lot(qty, symbol):
    min_qty, qty_step = get_lot_size(symbol)
    rounded_qty = round(round(qty / qty_step) * qty_step, 8)
    if rounded_qty < min_qty:
        rounded_qty = min_qty
    return rounded_qty

# === 查詢目前倉位 ===
def get_current_position(symbol: str):
    global session
    try:
        response = session.get_positions(category="linear", symbol=symbol)
        pos_list = response["result"]["list"]
        positions = []
        for pos in pos_list:
            side = pos["side"]
            size = float(pos["size"])
            if size > 0:
                positions.append({"side": side, "size": size})
        return positions
    except Exception as e:
        print("❌ 查詢倉位失敗：", e)
        send_telegram_message(message=f"❗查詢倉位失敗: {e}")
        session = new_session()
        return []

# === 強制平倉 ===
def close_position(symbol: str, side: str, size: float):
    global session
    try:
        print(f"🔁 嘗試平倉 {side}，數量：{size}")
        session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side == "Sell" else "Sell",
            orderType="Market",
            qty=str(size),
            timeInForce="IOC",
            reduceOnly=True
        )
        print("✅ 平倉成功")
    except Exception as e:
        print("❌ 平倉失敗：", e)
        send_telegram_message(message=f"❗平倉失敗：{e}")
        session = new_session()

# === EXIT: 全部平倉 ===
def close_all_position(symbol: str):
    global session
    positions = get_current_position(symbol)
    for pos in positions:
        pos_side = pos['side']
        pos_size = float(pos['size'])
        if pos_size > 0:
            close_position(symbol, pos_side, pos_size)
            send_telegram_message(message=f"⏹️ {symbol} {pos_side} 市價全平 {pos_size}")
            time.sleep(1)

# === 下單 ===
def place_order(symbol: str, side: str, price: float,
                stop_loss: float = None, take_profit: float = None,
                strategy: str = None, interval: str = None):
    global session, last_trade_time, last_trade_price, last_trade_side
    global trade_halted_today

    now = time.time()

    if trade_halted_today:
        send_telegram_message(message="⚠️ 今日已達最大虧損，暫停交易")
        print("🚨 交易已暫停")
        return

    if symbol in last_trade_time and now - last_trade_time[symbol] < cooldown_seconds:
        send_telegram_message(message=f"⏳ {symbol} 反手冷卻中，請勿頻繁下單！")
        print(f"⏳ {symbol} 冷卻未過")
        return

    price = round_to_tick(price, symbol)
    stopLoss_price = round_to_tick(float(stop_loss) if stop_loss else (price * 0.95 if side.upper() == "BUY" else price * 1.05), symbol)
    takeProfit_price = round_to_tick(float(take_profit) if take_profit else (price * 1.03 if side.upper() == "BUY" else price * 0.97), symbol)

    qty = round(usd_amount / price, 8)
    qty = round_to_lot(qty, symbol)

    min_qty, qty_step = get_lot_size(symbol)
    if qty > max_qty_per_order or qty < min_qty:
        send_telegram_message(message=f"❌ 不下單：數量不合法 qty={qty}")
        return

    positions = get_current_position(symbol)
    for pos in positions:
        pos_side = pos['side']
        pos_size = float(pos['size'])
        if pos_side.lower() != side.lower():
            close_position(symbol, pos_side, pos_size)
            time.sleep(1)

    retry = 3
    for i in range(retry):
        try:
            result = session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",
                qty=str(qty),
                stopLoss=str(stopLoss_price),
                takeProfit=str(takeProfit_price),
                timeInForce="IOC",
                reduceOnly=False
            )
            print(f"✅ 第{i+1}次嘗試下單成功：{result}")
            send_telegram_message(
                message=f"✅ 已於 Bybit {side.upper()} {symbol}\n市價成交\nqty:{qty}\nSL:{stopLoss_price}\nTP:{takeProfit_price}\n策略:{strategy}\n週期:{interval}"
            )
            last_trade_time[symbol] = now
            last_trade_price[symbol] = price
            last_trade_side[symbol] = side
            return
        except Exception as e:
            print(f"❌ 第{i+1}次下單失敗：{e}")
            send_telegram_message(message=f"❌ 第{i+1}次下單失敗: {e}")
            session = new_session()
            time.sleep(2)

    send_telegram_message(message=f"❌ {symbol} {side} 連續3次下單失敗，請檢查系統狀態")

# === Excel 寫入 ===
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
            try:
                wb = load_workbook(filename)
                ws = wb.active
            except InvalidFileException:
                wb = Workbook()
                ws = wb.active
                ws.append(headers)

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
        send_telegram_message(message=f"❗寫入交易紀錄 XLSX 失敗：{e}")


def get_pnl_last_1_hour(symbol: str):
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

        total_pnl = sum([r["pnl"] for r in trade_records])
        print(f"📊 {symbol} 最近1小時總損益: {total_pnl}")
        log_pnl_to_xlsx_trade_record(trade_records)
        send_telegram_message(f"📊 {symbol} 最近1小時總損益：{total_pnl} USDT")
        return total_pnl

    except Exception as e:
        print("❌ 查詢1小時損益失敗：", e)
        send_telegram_message(message=f"❗查詢1小時損益失敗：{e}")
        session = new_session()
        return 0.0
