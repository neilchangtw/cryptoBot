import os
from dotenv import load_dotenv
from bybit_trade import place_order, record_trade
from telegram_notify import send_telegram_message
import time

# 讀取 .env 設定
load_dotenv()

def test_place_order():
    symbol = "ETHUSDT"
    action = "SELL"
    price = 2750  # 測試用價格，可自行調整模擬
    print(f"📥 測試下單：{action} {symbol} @ {price}")
    place_order(symbol, action, price)
    print("✅ 測試下單完成\n")

def test_record_trade():
    symbol = "ETHUSDT"
    print(f"📥 測試撈取最近平倉紀錄 {symbol}")
    record_trade(symbol)
    print("✅ 測試紀錄完成\n")

def test_send_telegram():
    print("📥 測試發送 Telegram")
    send_telegram_message(message="✅ Telegram 測試訊息正常")
    print("✅ 測試發送完成\n")

if __name__ == "__main__":
    print("===== 系統整合測試開始 =====")
    test_send_telegram()
    test_place_order()

    # 模擬等待一段時間，模擬平倉
    print("⏳ 等待 5 秒模擬平倉...")
    time.sleep(5)

    test_record_trade()
    print("===== 系統整合測試完成 =====")
