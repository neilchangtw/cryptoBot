import os
from dotenv import load_dotenv

from bybit_trade import record_trade

# 讀取 .env 設定
load_dotenv()

# 你要統計的幣種列表（可擴充）
SYMBOL_LIST = ["ETHUSDT","BTCUSDT"]

def main():
    for symbol in SYMBOL_LIST:
        print(f"📊 開始撈取 {symbol} 最近 1 小時平倉損益紀錄")
        record_trade(symbol)

if __name__ == "__main__":
    main()
