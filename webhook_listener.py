from flask import Flask, request, jsonify
from telegram_notify import send_telegram_message
from bybit_trade import place_order
from datetime import datetime

app = Flask(__name__)

# 紀錄最近下單資訊
last_trade_price = None
last_trade_time = None
min_price_diff = 5       # 最小價格差異（單位 USDT）
cooldown_seconds = 600   # 冷卻時間：10 分鐘

@app.route('/webhook', methods=['POST'])
def webhook():
    global last_trade_price, last_trade_time

    try:
        # 支援 JSON 或 QueryString 格式
        if request.is_json:
            data = request.get_json()
            action = data.get('action', 'UNKNOWN')
            symbol = data.get('symbol', 'UNKNOWN')
            price = float(data.get('price', '0'))
        else:
            action = request.args.get('action', 'UNKNOWN')
            symbol = request.args.get('symbol', 'UNKNOWN')
            price = float(request.args.get('price', '0'))

        now = datetime.now()

        # 冷卻時間判斷
        if last_trade_time and (now - last_trade_time).total_seconds() < cooldown_seconds:
            remaining = cooldown_seconds - int((now - last_trade_time).total_seconds())
            cooldown_msg = (
                f"⏳ *跳過下單（冷卻中）*\n"
                f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*\n"
                f"📈 幣種：{symbol}\n"
                f"🕒 剩餘秒數：{remaining}"
            )
            print(cooldown_msg)
            send_telegram_message(message=cooldown_msg)
            return jsonify({"status": "skipped_due_to_time"}), 200

        # 價格變動過濾
        if last_trade_price and abs(price - last_trade_price) < min_price_diff:
            diff = abs(price - last_trade_price)
            skip_msg = (
                f"⚠️ *跳過下單（價格變化不足）*\n"
                f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*\n"
                f"📈 幣種：{symbol}\n"
                f"📉 當前價格：{price}\n"
                f"💹 前次價格：{last_trade_price}\n"
                f"🔍 價格差：{diff:.2f} < 最小差 {min_price_diff} USDT"
            )
            print("🔕", skip_msg)
            send_telegram_message(message=skip_msg)
            return jsonify({"status": "skipped_due_to_price"}), 200

        # 更新記錄
        last_trade_price = price
        last_trade_time = now

        # 組合通知訊息
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        message = (
            f"🚨 *交易訊號通知*\n"
            f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*\n"
            f"📈 幣種：{symbol}\n"
            f"💰 價格：{price}\n"
            f"🕒 時間：{timestamp}"
        )
        print("📥 收到訊號 ✅", message)

        send_telegram_message(signal=action, symbol=symbol, price=price)

        # 自動下單
        place_order(symbol=symbol, side=action, price=price)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("❌ 錯誤：", str(e))
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
