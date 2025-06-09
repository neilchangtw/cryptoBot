from flask import Flask, request, jsonify
from telegram_notify import send_telegram_message
from bybit_trade import place_order
from datetime import datetime
import os

app = Flask(__name__)

# 記錄最近下單資訊
last_trade_price = None
last_trade_time = None
min_price_diff = 10      # 最小價格差異（單位 USDT）
cooldown_seconds = 600   # 冷卻時間：10 分鐘

@app.route('/webhook', methods=['POST'])
def webhook():
    global last_trade_price, last_trade_time

    try:
        # 解析參數（支援 JSON 與 QueryString）
        if request.is_json:
            data = request.get_json()
            action     = data.get('action', 'UNKNOWN')
            symbol     = data.get('symbol', 'UNKNOWN')
            price      = float(data.get('price', '0'))
            strategy   = data.get('strategy', 'UNKNOWN')
            interval   = data.get('interval', 'UNKNOWN')
            stop_loss  = data.get('stop_loss')  # 可為 None
            take_profit= data.get('take_profit')
        else:
            action     = request.args.get('action', 'UNKNOWN')
            symbol     = request.args.get('symbol', 'UNKNOWN')
            price      = float(request.args.get('price', '0'))
            strategy   = request.args.get('strategy', 'UNKNOWN')
            interval   = request.args.get('interval', 'UNKNOWN')
            stop_loss  = request.args.get('stop_loss')
            take_profit= request.args.get('take_profit')

        now = datetime.now()

        # 冷卻時間判斷
        if last_trade_time and (now - last_trade_time).total_seconds() < cooldown_seconds:
            remaining = cooldown_seconds - int((now - last_trade_time).total_seconds())
            cooldown_msg = (
                f"⏳ *跳過下單（冷卻中）*\n"
                f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*\n"
                f"📈 幣種：{symbol}\n"
                f"📊 策略：{strategy}\n"
                f"⏰ 時間框架：{interval}\n"
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
                f"📊 策略：{strategy}\n"
                f"⏰ 時間框架：{interval}\n"
                f"📉 當前價格：{price}\n"
                f"💹 前次價格：{last_trade_price}\n"
                f"🔍 價格差：{diff:.2f} < 最小差 {min_price_diff} USDT"
            )
            print("🔕", skip_msg)
            send_telegram_message(message=skip_msg)
            return jsonify({"status": "skipped_due_to_price"}), 200

        # 更新紀錄
        last_trade_price = price
        last_trade_time = now

        # 組合通知訊息
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        msg_lines = [
            f"🚨 *交易訊號通知*",
            f"{'🟢' if action.upper() == 'BUY' else '🔴'} *動作：{action.upper()}*",
            f"📈 幣種：{symbol}",
            f"💰 價格：{price}",
            f"📊 策略：{strategy}",
            f"⏰ 時間框架：{interval}",
            f"🕒 時間：{timestamp}",
        ]
        if stop_loss:   msg_lines.append(f"🛑 停損：{stop_loss}")
        if take_profit: msg_lines.append(f"🎯 停利：{take_profit}")
        message = "\n".join(msg_lines)

        print("📥 收到訊號 ✅", message)
        send_telegram_message(
            message=message
        )

        # 自動下單（傳入所有參數，未用可忽略）
        place_order(
            symbol=symbol,
            side=action,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy=strategy,
            interval=interval
        )

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("❌ 錯誤：", str(e))
        send_telegram_message(message=f"❌ Webhook處理錯誤：{e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
