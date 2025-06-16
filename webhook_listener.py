from datetime import datetime
from flask import Flask, request, jsonify
from bybit_trade import place_order
from telegram_notify import send_telegram_message

app = Flask(__name__)

# === 記錄最近下單資訊 ===
last_trade_price = None
last_trade_time = None
min_price_diff = 10  # 最小價格差異 (USDT)
cooldown_seconds = 600  # 冷卻時間 (秒)

# 安全轉 float
def safe_float(val):
    try:
        f = float(val)
        return f if f > 0 else None
    except:
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    global last_trade_price, last_trade_time

    try:
        data = request.get_json()
        action = data.get('action', 'UNKNOWN').upper()
        symbol = data.get('symbol', 'UNKNOWN')
        price = safe_float(data.get('price'))
        stop_loss = safe_float(data.get('sl'))
        take_profit = safe_float(data.get('tp'))
        strategy = data.get('strategy', 'UNKNOWN')
        interval = data.get('interval', 'UNKNOWN')

        # 基本驗證
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

        now = datetime.now()

        # 冷卻時間判斷
        if last_trade_time and (now - last_trade_time).total_seconds() < cooldown_seconds:
            remaining = cooldown_seconds - int((now - last_trade_time).total_seconds())
            cooldown_msg = (
                f"⏳ 冷卻中，跳過下單\n"
                f"{'🟢' if action == 'BUY' else '🔴'} {symbol}\n"
                f"剩餘: {remaining} 秒"
            )
            print(cooldown_msg)
            send_telegram_message(cooldown_msg)
            return jsonify({"status": "cooldown_skipped"}), 200

        # 價差過濾
        if last_trade_price and abs(price - last_trade_price) < min_price_diff:
            diff = abs(price - last_trade_price)
            skip_msg = f"⚠️ 跳過下單（價格變化 {diff:.2f} < {min_price_diff})"
            print(skip_msg)
            send_telegram_message(skip_msg)
            return jsonify({"status": "price_skipped"}), 200

        # 更新下單紀錄
        last_trade_price = price
        last_trade_time = now

        # 發送 Telegram 紀錄
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        msg_lines = [
            f"🚨 交易訊號通知",
            f"{'🟢' if action == 'BUY' else '🔴'} {action}",
            f"幣種: {symbol}",
            f"價格: {price}",
            f"止損: {stop_loss or '無'}",
            f"止盈: {take_profit or '無'}",
            f"策略: {strategy}",
            f"週期: {interval}",
            f"時間: {timestamp}"
        ]
        send_telegram_message("\n".join(msg_lines))
        print("✅ 收到訊號並執行下單")

        # 執行下單
        place_order(
            symbol=symbol,
            side=action,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit
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