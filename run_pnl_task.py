import os
from dotenv import load_dotenv

from bybit_trade import get_pnl_last_1_hour

# 讀取 .env 設定
load_dotenv()

# 你要統計的幣種列表，直接在這邊加幣種即可
SYMBOL_LIST = ["ETHUSDT"]

def main():
    for symbol in SYMBOL_LIST:
        print(f"📊 開始統計 {symbol} 最近 1 小時損益")
        get_pnl_last_1_hour(symbol)

if __name__ == "__main__":
    main()
