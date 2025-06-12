from datetime import datetime

from flask import Flask, request, jsonify

from bybit_trade import place_order
from telegram_notify import send_telegram_message

app = Flask(__name__)

# === 記錄最近下單資訊 ===
last_trade_price = None
last_trade_time = None
min_price_diff = 10      # 最小價格差異（單位 USDT）
cooldown_seconds = 600   # 冷卻時間：10 分鐘

@app.route('/webhook', methods=['POST'])
def webhook():
    global last_trade_price, last_trade_time

    try:
        # 解析 TradingView Webhook 傳入的資料
        data = request.get_json()
        action   = data.get('action', 'UNKNOWN')
        symbol   = data.get('symbol', 'UNKNOWN')
        price    = float(data.get('price', '0'))
        strategy = data.get('strategy', 'UNKNOWN')
        interval = data.get('interval', 'UNKNOWN')

        now = datetime.now()

        # === 冷卻判斷 ===
        if last_trade_time and (now - last_trade_time).total_seconds() < cooldown_seconds:
            remaining = cooldown_seconds - int((now - last_trade_time).total_seconds())
            cooldown_msg = (
                f"⏳ *跳過下單（冷卻中）*\n"
                f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*\n"
                f"📈 幣種：{symbol}\n"
                f"📊 策略：{strategy}\n"
                f"⏰ 時間框架：{interval}\n"
                f"🕒 剩餘冷卻秒數：{remaining}"
            )
            print(cooldown_msg)
            send_telegram_message(message=cooldown_msg)
            return jsonify({"status": "cooldown_skipped"}), 200

        # === 價格變動過濾 ===
        if last_trade_price and abs(price - last_trade_price) < min_price_diff:
            diff = abs(price - last_trade_price)
            skip_msg = (
                f"⚠️ *跳過下單（價格變化不足）*\n"
                f"變化：{diff:.2f} USDT < 門檻 {min_price_diff} USDT"
            )
            print(skip_msg)
            send_telegram_message(message=skip_msg)
            return jsonify({"status": "price_skipped"}), 200

        # 更新交易紀錄
        last_trade_price = price
        last_trade_time = now

        # 發送通知
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        msg_lines = [
            f"🚨 *交易訊號通知*",
            f"{'🟢' if action.upper() == 'BUY' else '🔴'} 動作: {action.upper()}",
            f"幣種: {symbol}",
            f"價格: {price}",
            f"策略: {strategy}",
            f"週期: {interval}",
            f"時間: {timestamp}"
        ]
        send_telegram_message(message="\n".join(msg_lines))
        print("✅ 收到訊號並執行下單")

        # === 執行下單 (V6 Pro版 place_order已自帶倉位計算) ===
        place_order(symbol=symbol, side=action, price=price)

        return jsonify({"status": "order_sent"}), 200

    except Exception as e:
        print("❌ 錯誤：", e)
        send_telegram_message(message=f"❌ Webhook處理錯誤: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)