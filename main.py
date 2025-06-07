from pybit.unified_trading import HTTP
import os
from dotenv import load_dotenv

# 載入 .env 檔案中的 API 金鑰
load_dotenv()
api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

print(f"🔐 api_key: {api_key}")
print(f"🔐 api_secret: {api_secret}")

# 建立 session（使用主網模擬交易帳戶）
session = HTTP(
    testnet=False,
    demo=True,
    api_key=api_key,
    api_secret=api_secret,
    recv_window=10000
)

# 下單函式（ETHUSDT 市價買入 0.05）
def place_demo_order():
    try:
        result = session.place_order(
            category="linear",
            symbol="ETHUSDT",
            side="Buy",
            orderType="Market",
            qty="0.05",
            timeInForce="IOC"
        )
        print("✅ 下單成功：", result)
    except Exception as e:
        print("❌ 下單失敗：", str(e))

# 執行下單
if __name__ == "__main__":
    place_demo_order()
