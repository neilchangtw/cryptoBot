from datetime import datetime
from flask import Flask, request, jsonify
from bybit_trade import place_order
from telegram_notify import send_telegram_message
import os
from dotenv import load_dotenv
import json
# 讀取.env檔案
load_dotenv()

app = Flask(__name__)

last_trade_price = {}
min_price_diff = 10  # 最小價格差異 (USDT)

def safe_float(val):
    try:
        f = float(val)
        return round(f) if f > 0 else None
    except:
        return None

def get_bool_env(key, default=False):
    val = os.getenv(key, str(default))
    return val.lower() in ("1", "true", "yes", "on")

STRICT_RAISE_ON_DIRECTION_ERROR = get_bool_env("STRICT_RAISE_ON_DIRECTION_ERROR", False)

@app.route('/webhook', methods=['POST'])
def webhook():
    global last_trade_price
    try:
        # 支援 text/plain 或 application/json
        data = request.get_json(silent=True)
        if data is None:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except Exception as e:
                print("❌ JSON decode failed:", e)
                data = {}
        print("Webhook Received:", data)

        action = data.get('action', 'UNKNOWN').upper()
        symbol = data.get('symbol', 'UNKNOWN')
        price = safe_float(data.get('price'))
        stop_loss = safe_float(data.get('sl'))
        take_profit = safe_float(data.get('tp'))
        strategy = data.get('strategy', 'default')
        interval = data.get('interval', 'UNKNOWN')
        note = data.get('note', '').lower()

        # === 處理exit平倉邏輯 ===
        if "模擬平" in note or "exit" in note or "close" in note:
            msg = f"🔚 模擬平倉訊號接收: {symbol} ({action})"
            print(msg)
            send_telegram_message(msg)
            # **移除 close_request 參數**
            place_order(symbol=symbol, side=action, price=price, strategy_id=strategy)
            return jsonify({"status": "simulated_exit_sent", "symbol": symbol, "side": action}), 200

        if action not in ["BUY", "SELL"]:
            msg = f"❌ 不支援的下單方向: {action}"
            print(msg)
            send_telegram_message(msg)
            return jsonify({"error": "invalid_action"}), 400

        if price is None or price <= 0:
            msg = f"❌ 價格錯誤: {price}"
            print(msg)
            send_telegram_message(msg)
            return jsonify({"error": "invalid_price"}), 400

        # ======= 方向防呆，行為可用 .env 控制 =======
        if action == "BUY":
            # 多單止盈應大於開倉價
            if take_profit is not None and take_profit < price:
                msg = f"❌ 多單止盈價格({take_profit}) 必須大於開倉價({price})，請檢查策略設定！"
                print(msg)
                send_telegram_message(msg)
                if STRICT_RAISE_ON_DIRECTION_ERROR:
                    return jsonify({"error": msg}), 400
                take_profit = None
            # 多單止損應小於開倉價
            if stop_loss is not None and stop_loss > price:
                msg = f"❌ 多單止損價格({stop_loss}) 必須小於開倉價({price})，請檢查策略設定！"
                print(msg)
                send_telegram_message(msg)
                if STRICT_RAISE_ON_DIRECTION_ERROR:
                    return jsonify({"error": msg}), 400
                stop_loss = None

        elif action == "SELL":
            # 空單止盈應小於開倉價
            if take_profit is not None and take_profit > price:
                msg = f"❌ 空單止盈價格({take_profit}) 必須小於開倉價({price})，請檢查策略設定！"
                print(msg)
                send_telegram_message(msg)
                if STRICT_RAISE_ON_DIRECTION_ERROR:
                    return jsonify({"error": msg}), 400
                take_profit = None
            if stop_loss is not None and stop_loss < price:
                msg = f"❌ 空單止損價格({stop_loss}) 必須大於開倉價({price})，請檢查策略設定！"
                print(msg)
                send_telegram_message(msg)
                if STRICT_RAISE_ON_DIRECTION_ERROR:
                    return jsonify({"error": msg}), 400
                stop_loss = None

        # ===僅針對相同策略+幣種執行價格跳動過濾===
        last_key = (strategy, symbol)
        if last_key in last_trade_price and abs(price - last_trade_price[last_key]) < min_price_diff:
            diff = abs(price - last_trade_price[last_key])
            skip_msg = f"⚠️ 跳過下單（價格變化 {diff:.2f} < {min_price_diff}）"
            print(skip_msg)
            send_telegram_message(skip_msg)
            return jsonify({"status": "price_skipped"}), 200

        last_trade_price[last_key] = price  # 更新策略+幣種的價格

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg_lines = [
            f"🚨 交易訊號通知",
            f"{'🟢' if action == 'BUY' else '🔴'} {action}",
            f"幣種: {symbol}",
            f"價格: {price}",
            f"止損: {stop_loss if stop_loss is not None else '無'}",
            f"止盈: {take_profit if take_profit is not None else '無'}",
            f"策略: {strategy}",
            f"週期: {interval}",
            f"時間: {timestamp}"
        ]
        send_telegram_message("\n".join(msg_lines))
        print("✅ 收到訊號並執行下單")

        place_order(
            symbol=symbol,
            side=action,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_id=strategy
        )

        return jsonify({"status": "order_sent"}), 200

    except Exception as e:
        print("❌ 錯誤：", e)
        try:
            send_telegram_message(f"❌ Webhook處理錯誤: {e}")
        except:
            pass
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)