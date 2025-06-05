from flask import Flask, request, jsonify
from telegram_notify import send_telegram_message
from datetime import datetime

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # ✅ 從 URL 查詢字串抓資料
        action = request.args.get('action', 'UNKNOWN')
        symbol = request.args.get('symbol', 'UNKNOWN')
        price = request.args.get('price', '0')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        message = (
            f"🚨 *交易訊號通知*\n"
            f"🎯 動作：{action}\n"
            f"📈 幣種：{symbol}\n"
            f"💰 價格：{price}\n"
            f"🕒 時間：{timestamp}"
        )

        print("📥 收到訊號 ✅", message)
        send_telegram_message(message)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print("❌ 錯誤：", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(port=5000)
