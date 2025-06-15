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

# === 通用安全轉型函式 ===
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
        # 解析 TradingView Webhook 傳入的資料
        data = request.get_json()
        action   = data.get('action', 'UNKNOWN').upper()
        symbol   = data.get('symbol', 'UNKNOWN')
        price    = safe_float(data.get('price'))

        stop_loss = safe_float(data.get('sl'))
        take_profit = safe_float(data.get('tp'))

        strategy = data.get('strategy', 'UNKNOWN')
        interval = data.get('interval', 'UNKNOWN')

        # 檢查 action 合法性
        if action not in ["BUY", "SELL"]:
            err_msg = f"❌ 不支援的下單方向: {action}"
            print(err_msg)
            send_telegram_message(err_msg)
            return jsonify({"error": "invalid_action"}), 400

        # 檢查價格合法性
        if price is None or price <= 0:
            err_msg = f"❌ 價格數據異常: {price}"
            print(err_msg)
            send_telegram_message(err_msg)
            return jsonify({"error": "invalid_price"}), 400

        now = datetime.now()

        # === 冷卻判斷 ===
        if last_trade_time and (now - last_trade_time).total_seconds() < cooldown_seconds:
            remaining = cooldown_seconds - int((now - last_trade_time).total_seconds())
            cooldown_msg = (
                f"⏳ 跳過下單（冷卻中）\n"
                f"{'🟢' if action == 'BUY' else '🔴'} 動作：{action}\n"
                f"幣種：{symbol}\n"
                f"策略：{strategy}\n"
                f"週期：{interval}\n"
                f"剩餘冷卻秒數：{remaining}"
            )
            print(cooldown_msg)
            send_telegram_message(cooldown_msg)
            return jsonify({"status": "cooldown_skipped"}), 200

        # === 價格變動過濾 ===
        if last_trade_price and abs(price - last_trade_price) < min_price_diff:
            diff = abs(price - last_trade_price)
            skip_msg = (
                f"⚠️ 跳過下單（價格變化不足）\n"
                f"變化：{diff:.2f} USDT < 門檻 {min_price_diff} USDT"
            )
            print(skip_msg)
            send_telegram_message(skip_msg)
            return jsonify({"status": "price_skipped"}), 200

        # 更新交易紀錄
        last_trade_price = price
        last_trade_time = now

        # 發送通知
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')
        msg_lines = [
            f"🚨 交易訊號通知",
            f"{'🟢' if action == 'BUY' else '🔴'} 動作: {action}",
            f"幣種: {symbol}",
            f"價格: {price}",
        ]
        if stop_loss is not None:
            msg_lines.append(f"止損: {stop_loss}")
        if take_profit is not None:
            msg_lines.append(f"止盈: {take_profit}")
        msg_lines += [
            f"策略: {strategy}",
            f"週期: {interval}",
            f"時間: {timestamp}"
        ]
        message_text = "\n".join(msg_lines)
        send_telegram_message(message_text)
        print("✅ 收到訊號並執行下單")

        # === 執行下單 ===
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