from pybit.unified_trading import HTTP
import os
from openpyxl import Workbook, load_workbook
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# === API 環境變數 ===
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

print(f"🔐 api_key: {api_key}")
print(f"🔐 api_secret: {api_secret}")

# 建立 Bybit session（主網模擬交易）
session = HTTP(
    api_key=api_key,
    api_secret=api_secret,
    testnet=False,
    demo=True,
    recv_window=10000
)

# === 查詢目前倉位 ===
def get_current_position(symbol: str):
    try:
        response = session.get_positions(category="linear", symbol=symbol)
        pos_list = response["result"]["list"]
        for pos in pos_list:
            side = pos["side"]
            size = float(pos["size"])
            if size > 0:
                return side, size
        return None, 0.0
    except Exception as e:
        print("❌ 查詢倉位失敗：", e)
        return None, 0.0

# === 平倉邏輯 ===
def close_position(symbol: str, side: str):
    try:
        print(f"🔁 嘗試平倉 {side}")
        session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side == "Sell" else "Sell",
            orderType="Market",
            qty="999",
            timeInForce="IOC",
            reduceOnly=True
        )
        print("✅ 平倉成功")
    except Exception as e:
        print("❌ 平倉失敗：", e)

# === 查詢最近平倉損益 ===
def get_latest_closed_pnl(symbol: str):
    try:
        result = session.get_closed_pnl(category="linear", symbol=symbol, limit=1)
        pnl = float(result["result"]["list"][0]["closedPnl"])
        return pnl
    except Exception as e:
        print("❌ 無法查詢平倉 PnL：", e)
        return None

# === 寫入 XLSX ===
def log_pnl_to_xlsx(symbol: str, pnl: float):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    filename = "trade_pnl_log.xlsx"

    try:
        if os.path.exists(filename):
            wb = load_workbook(filename)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(["時間", "幣種", "損益"])

        ws.append([now, symbol, pnl])
        wb.save(filename)
        print(f"📗 PnL 記錄寫入 XLSX 成功: {pnl}")
    except Exception as e:
        print("❌ 寫入 XLSX 失敗：", e)

# === 下單邏輯（自動平倉 & 反手） ===
def place_order(symbol: str, side: str, price: float):
    usd_amount = float(os.getenv("ORDER_USD_AMOUNT", "100"))
    qty = round(usd_amount / price, 3)

    # 🧐 動態止捛價格
    stop_loss_price = round(price * 0.95, 2) if side.upper() == "BUY" else round(price * 1.05, 2)

    current_side, position_size = get_current_position(symbol)
    print(f"📊 當前倉位: {current_side}, 量: {position_size}")

    if current_side and current_side.lower() != side.lower():
        close_position(symbol, current_side)

        try:
            result = session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",
                qty=str(qty),
                stopLoss=str(stop_loss_price),
                timeInForce="IOC",
                reduceOnly=False
            )
            print("✅ 反手下單成功：", result)

            pnl = get_latest_closed_pnl(symbol)
            if pnl is not None:
                log_pnl_to_xlsx(symbol, pnl)
        except Exception as e:
            print("❌ 反手下單失敗：", str(e))
            return

    else:
        try:
            result = session.place_order(
                category="linear",
                symbol=symbol,
                side=side.capitalize(),
                orderType="Market",
                qty=str(qty),
                stopLoss=str(stop_loss_price),
                timeInForce="IOC",
                reduceOnly=False
            )
            print("✅ 下單成功：", result)
        except Exception as e:
            print("❌ 下單失敗：", str(e))
