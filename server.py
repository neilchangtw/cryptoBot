from flask import Flask, request
from telegram_notify import send_telegram_message
import json

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("📥 收到訊息：", data)

    action = data.get("action", "").upper()

    if action == "BUY":
        send_telegram_message("🚀 [TradingView] Buy 訊號觸發！")
        # call_bybit_api("BUY")
    elif action == "SELL":
        send_telegram_message("🔻 [TradingView] Sell 訊號觸發！")
        # call_bybit_api("SELL")
    else:
        send_telegram_message(f"⚠️ 未知訊號: {data}")

    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
