"""go_live.bat 的 pre-flight check（從 .env 讀關鍵設定 → 顯示 → 詢問 Y/N）"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

paper = os.getenv("PAPER_TRADING", "true")
testnet = os.getenv("BINANCE_TESTNET", "true")
key = os.getenv("BINANCE_API_KEY", "")
secret = os.getenv("BINANCE_API_SECRET", "")
lev = os.getenv("LEVERAGE", "20")
margin = os.getenv("MARGIN_PER_TRADE", "200")

try:
    notional = int(lev) * int(margin)
except ValueError:
    notional = 0

paper_warn = "⚠️  仍是 PAPER（模擬）" if paper.lower() == "true" else "✅ LIVE 實盤"
testnet_warn = "⚠️  仍是 TESTNET" if testnet.lower() == "true" else "✅ MAINNET"
lev_warn = "⚠️  首月建議降至 10x" if int(lev or 0) > 10 else f"✅ 槓桿降規模 {lev}x"

print()
print(f"  PAPER_TRADING   = {paper:<8}  {paper_warn}")
print(f"  BINANCE_TESTNET = {testnet:<8}  {testnet_warn}")
print(f"  API_KEY 末 4 碼 = ...{key[-4:] if len(key) >= 4 else '(empty)'}")
print(f"  API_SECRET 末 4 = ...{secret[-4:] if len(secret) >= 4 else '(empty)'}")
print(f"  LEVERAGE        = {lev}x  {lev_warn}")
print(f"  MARGIN/TRADE    = ${margin}")
print(f"  名目金額        = ${notional}")
print(f"  最壞單筆預期    = L ${notional * 0.04:.0f} / S ${notional * 0.05:.0f}")
print()
print("─" * 60)
print()
print("確認所有設定正確（特別是 API key 末 4 碼是 mainnet key）")

try:
    ans = input("要繼續啟動嗎? (Y/N): ").strip().lower()
except (EOFError, KeyboardInterrupt):
    print("\n已取消")
    sys.exit(1)

if ans != "y":
    print("已取消，未啟動。")
    sys.exit(1)

sys.exit(0)
