import os
import time
from openpyxl import Workbook, load_workbook
from dotenv import load_dotenv
from datetime import datetime, date
from bybit import HTTP
from telegram_notify import send_telegram_message

# === 載入 .env 配置 ===
load_dotenv()

# === API 與風控全域參數 ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")
testnet = os.getenv("BYBIT_BASE_URL", "").startswith("https://api-testnet")
usd_amount = float(os.getenv("ORDER_USD_AMOUNT", "100"))

# --- 風控參數全部從 .env 讀取 ---
max_loss_per_order = float(os.getenv("MAX_LOSS_PER_ORDER", "30"))      # 單筆最大可承受虧損(USDT)
max_loss_per_day   = float(os.getenv("MAX_LOSS_PER_DAY", "100"))       # 單日最大可承受虧損
max_qty_per_order  = float(os.getenv("MAX_QTY_PER_ORDER", "0.5"))      # 單次最大下單數量（依標的自訂）
tick_size_default  = 0.01

# --- 冷卻與狀態記錄 ---
cooldown_seconds = 600   # 10分鐘冷卻
last_trade_time = {}     # 各幣種上次下單時間
last_trade_price = {}
last_trade_side = {}
pnl_today = 0
last_pnl_date = None
trade_halted_today = False # 單日最大虧損暫停交易

# === 建立 Bybit session ===
def new_session():
    return HTTP(
        api_key=api_key,
        api_secret=api_secret,
        testnet=testnet,
        recv_window=10000
    )

session = new_session()

# === 查詢該幣種 tick size（避免掛價異常）===
def get_tick_size(symbol):
    try:
        response = session.get_instruments_info(category="linear", symbol=symbol)
        info = response["result"]["list"][0]
        tick_size = float(info.get("priceFilter", {}).get("tickSize", tick_size_default))
        return tick_size
    except Exception as e:
        print("❌ 查詢 tick size 失敗，預設 0.01：", e)
        send_telegram_message(message=f"❗查詢 {symbol} tick size 失敗: {e}")
        return tick_size_default

# === 將價格四捨五入至合約 tick 單位 ===
def round_to_tick(price, symbol):
    tick = get_tick_size(symbol)
    return round(round(price / tick) * tick, 8)

# === 查詢目前倉位（多空皆支援）===
def get_current_position(symbol: str):
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
        # API異常時自動重連
        global session
        session = new_session()
        return []

# === 強制平倉邏輯（多空雙向，依現有倉位數量）===
def close_position(symbol: str, side: str, size: float):
    try:
        print(f"🔁 嘗試平倉 {side}，數量：{size}")
        session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side == "Sell" else "Sell",
            orderType="Market",   # 強制用市價單
            qty=str(size),
            timeInForce="IOC",
            reduceOnly=True
        )
        print("✅ 平倉成功")
    except Exception as e:
        print("❌ 平倉失敗：", e)
        send_telegram_message(message=f"❗平倉失敗：{e}")
        global session
        session = new_session()

# === 查詢最近平倉損益 ===
def get_latest_closed_pnl(symbol: str):
    try:
        result = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        pnl = float(result["result"]["list"][0]["closedPnl"])
        return pnl
    except Exception as e:
        print("❌ 無法查詢平倉 PnL：", e)
        send_telegram_message(message=f"❗查詢平倉 PnL 失敗：{e}")
        return None

# === 平倉/反手紀錄損益到 Excel ===
def log_pnl_to_xlsx(symbol: str, pnl: float, strategy: str = None, interval: str = None):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    filename = "trade_pnl_log.xlsx"
    try:
        if os.path.exists(filename):
            wb = load_workbook(filename)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(["時間", "幣種", "損益", "策略", "K線週期"])
        ws.append([now, symbol, pnl, strategy or "", interval or ""])
        wb.save(filename)
        print(f"📗 PnL 記錄寫入 XLSX 成功: {pnl}")
    except Exception as e:
        print("❌ 寫入 XLSX 失敗：", e)
        send_telegram_message(message=f"❗寫入損益 XLSX 失敗：{e}")

# === 下單數量與價格合理性檢查 ===
def check_price_qty_valid(price, qty, symbol):
    if price <= 0 or qty <= 0:
        return False, "價格或數量異常"
    if qty > max_qty_per_order:
        return False, f"下單數量過大：{qty}>{max_qty_per_order}"
    # 可進一步加入合理價格波動防爆判斷
    return True, None

# === 主下單邏輯 ===
def place_order(symbol: str, side: str, price: float,
                stop_loss: float = None, take_profit: float = None,
                strategy: str = None, interval: str = None):
    """
    自動下單：支援冷卻、最大風控、合理價格、最大單量保護、市價單成交、API失敗自動重連與TG通知
    """
    global session, last_trade_time, last_trade_price, last_trade_side
    global pnl_today, last_pnl_date, trade_halted_today

    # === 單日最大虧損保護 ===
    today_str = date.today().strftime('%Y-%m-%d')
    if last_pnl_date != today_str:
        pnl_today = 0
        last_pnl_date = today_str
        trade_halted_today = False

    if trade_halted_today:
        send_telegram_message(message="⚠️ 今日已達最大虧損，暫停交易")
        print("🚨 交易已暫停（單日最大虧損）")
        return

    # === 冷卻時間檢查 ===
    now = time.time()
    if symbol in last_trade_time and now - last_trade_time[symbol] < cooldown_seconds:
        send_telegram_message(message=f"⏳ {symbol} 反手冷卻中，請勿頻繁下單！")
        print(f"⏳ {symbol} 冷卻未過")
        return

    # === 取得 tick size，自動合規化止損/停利價格 ===
    price = round_to_tick(price, symbol)
    stopLoss_price = round_to_tick(float(stop_loss) if stop_loss else (price * 0.95 if side.upper() == "BUY" else price * 1.05), symbol)
    takeProfit_price = round_to_tick(float(take_profit) if take_profit else (price * 1.03 if side.upper() == "BUY" else price * 0.97), symbol)

    # === 下單數量計算（限制最大單量）===
    qty = round(usd_amount / price, 3)
    is_valid, reason = check_price_qty_valid(price, qty, symbol)
    if not is_valid:
        send_telegram_message(message=f"❌ 不下單：{reason}")
        print(f"❌ 不下單：{reason}")
        return

    # === 檢查現有持倉，強制反手平倉 ===
    positions = get_current_position(symbol)
    for pos in positions:
        pos_side = pos['side']
        pos_size = float(pos['size'])
        if pos_side.lower() != side.lower():
            close_position(symbol, pos_side, pos_size)
            # 防止API爆單，sleep 1s
            time.sleep(1)
            pnl = get_latest_closed_pnl(symbol)
            if pnl is not None:
                log_pnl_to_xlsx(symbol, pnl, strategy, interval)
                pnl_today += pnl
                # 超過單日最大虧損即自動暫停
                if abs(pnl_today) > max_loss_per_day and pnl_today < 0:
                    trade_halted_today = True
                    send_telegram_message(message="⚠️ 觸發單日最大虧損，已自動暫停下單")
                    print("🚨 交易已暫停（單日最大虧損）")
                    return

    # === 下市價單（避免價格失真無法成交） ===
    retry = 3
    for i in range(retry):
        try:
            result = session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",     # 市價單！不掛特定價格
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
            # 更新冷卻、記錄資訊
            last_trade_time[symbol] = now
            last_trade_price[symbol] = price
            last_trade_side[symbol] = side
            return
        except Exception as e:
            print(f"❌ 第{i+1}次下單失敗：{e}")
            send_telegram_message(message=f"❌ 第{i+1}次下單失敗: {e}")
            session = new_session()
            time.sleep(2)

    # 三次皆失敗
    send_telegram_message(message=f"❌ {symbol} {side} 連續3次下單失敗，請檢查系統狀態")

