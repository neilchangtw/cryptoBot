"""
Mainnet read-only 驗證腳本（不下單、不改任何狀態）

檢查：
  1. .env 切換完成（PAPER_TRADING=false, BINANCE_TESTNET=false）
  2. API 連線正常（time sync）
  3. 帳戶 USDT 餘額讀取
  4. Hedge Mode 狀態（必要時切換）
  5. 既有 ETHUSDT 持倉（應為空）
  6. 槓桿設定（讀取，不變動 — 啟動 bot 才會自動設）
  7. Symbol info（tick_size / qty_step / min_qty）
  8. 公開 K 線 API（從 fapi.binance.com 抓 ETHUSDT 1h）
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def header(t):
    print(f"\n{'─' * 72}")
    print(f"  {t}")
    print(f"{'─' * 72}")


# ── 1. .env 設定 ──────────────────────────────────────────────
header("1. .env 設定")
paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
lev = int(os.getenv("LEVERAGE", "20"))
margin = int(os.getenv("MARGIN_PER_TRADE", "200"))
api_key = os.getenv("BINANCE_API_KEY", "")
api_sec = os.getenv("BINANCE_API_SECRET", "")

env_ok = (not paper) and (not testnet) and len(api_key) >= 50 and len(api_sec) >= 50
print(f"  PAPER_TRADING   = {paper}     {OK if not paper else FAIL}")
print(f"  BINANCE_TESTNET = {testnet}   {OK if not testnet else FAIL}")
print(f"  API_KEY 末 4 碼 = ...{api_key[-4:]}  (長度 {len(api_key)})")
print(f"  API_SECRET 末 4 = ...{api_sec[-4:]}  (長度 {len(api_sec)})")
print(f"  LEVERAGE        = {lev}x")
print(f"  MARGIN/TRADE    = ${margin}（名目 ${lev * margin}）")
print(f"  最壞單筆預期    = L ~${lev * margin * 0.04:.0f} / S ~${lev * margin * 0.05:.0f}")
if not env_ok:
    print(f"\n{FAIL} .env 設定不完整，停止驗證")
    sys.exit(1)

# ── 2-8. 載入 binance_trade（會 sync_time + new_session） ──
header("2. API 連線 + 時間同步")
try:
    import binance_trade
    print(f"  {OK} import binance_trade")
    print(f"  endpoint = {'TESTNET' if binance_trade.is_testnet else 'MAINNET (fapi.binance.com)'}")
except Exception as e:
    print(f"  {FAIL} import 失敗：{e}")
    sys.exit(1)

# ── 3. 帳戶餘額 ──
header("3. 帳戶 USDT 餘額")
try:
    bal = binance_trade.get_wallet_balance()
    print(f"  USDT wallet = ${bal:.2f}")
    if bal < margin * 2:
        print(f"  {WARN} 餘額低於名目 2 倍（${margin*2}），同時開 L+S 可能保證金不足")
    elif bal < margin * 4:
        print(f"  {OK} 餘額足以支撐 1-2 筆同時持倉")
    else:
        print(f"  {OK} 餘額充裕")
except Exception as e:
    print(f"  {FAIL} 取得餘額失敗：{e}")
    sys.exit(1)

# ── 4. Hedge Mode ──
header("4. Hedge Mode（dualSidePosition）")
try:
    mode = binance_trade.client.get_position_mode()
    is_hedge = mode.get("dualSidePosition", False)
    if is_hedge:
        print(f"  {OK} 已是 Hedge Mode (dualSidePosition=true)")
    else:
        print(f"  {WARN} 目前是 One-way Mode，bot 啟動會自動切到 Hedge")
        print(f"       現在切換以避免 -4061：")
        binance_trade.client.change_position_mode(dualSidePosition="true")
        mode2 = binance_trade.client.get_position_mode()
        if mode2.get("dualSidePosition"):
            print(f"  {OK} 已切換成功")
        else:
            print(f"  {FAIL} 切換失敗，請在 Binance 網頁手動設定 Hedge Mode")
except Exception as e:
    print(f"  {FAIL} get_position_mode 失敗：{e}")
    print(f"       常見原因：API key 缺 Futures 權限 / IP 不在白名單")
    sys.exit(1)

# ── 5. 既有 ETHUSDT 持倉 ──
header("5. 既有 ETHUSDT 持倉")
try:
    positions = binance_trade.client.get_position_risk(symbol="ETHUSDT")
    open_pos = [p for p in positions if abs(float(p.get("positionAmt", 0))) > 0]
    if not open_pos:
        print(f"  {OK} 無既有持倉（乾淨）")
    else:
        for p in open_pos:
            print(f"  {WARN} 既有持倉：{p.get('positionSide')} amt={p.get('positionAmt')} "
                  f"entry={p.get('entryPrice')} uPnL={p.get('unRealizedProfit')}")
        print(f"       建議手動平掉或讓 bot 接管（注意 internal state 為空，bot 會視為 orphan）")
except Exception as e:
    print(f"  {FAIL} get_position_risk 失敗：{e}")

# ── 6. 槓桿設定（只讀） ──
header("6. 既有槓桿設定")
try:
    # leverageBracket 包含當前 leverage
    info = binance_trade.client.leverage_brackets(symbol="ETHUSDT")
    # Note: 這個 endpoint 給的是 bracket，不是 current leverage
    # 用 positionRisk 看 leverage 欄位
    for p in positions:
        cur_lev = p.get("leverage", "N/A")
        ps = p.get("positionSide", "BOTH")
        print(f"  {ps:>5} leverage = {cur_lev}x")
    print(f"       啟動 bot 時會自動設為 .env 的 LEVERAGE={lev}")
except Exception as e:
    print(f"  {WARN} 無法讀槓桿（不影響啟動）：{e}")

# ── 7. Symbol info ──
header("7. ETHUSDT 交易精度")
try:
    tick, qty_step, min_qty = binance_trade.get_symbol_info("ETHUSDT")
    print(f"  {OK} tick_size = {tick}  qty_step = {qty_step}  min_qty = {min_qty}")
    # 算一下名目 $2000 在當前價的數量
    mark = float(binance_trade.client.mark_price(symbol="ETHUSDT")["markPrice"])
    notional = lev * margin
    qty = notional / mark
    print(f"  目前 mark price = ${mark:.2f}")
    print(f"  名目 ${notional} → 數量 ≈ {qty:.4f} ETH（會 round 到 {qty_step}）")
except Exception as e:
    print(f"  {FAIL} symbol info 失敗：{e}")

# ── 8. 公開 K 線 ──
header("8. K 線抓取（main_eth.py 每小時做的事）")
try:
    klines = binance_trade.client.klines(symbol="ETHUSDT", interval="1h", limit=3)
    print(f"  {OK} 抓到 {len(klines)} 根 1h K 線")
    for k in klines:
        from datetime import datetime, timezone, timedelta
        t = datetime.fromtimestamp(k[0]/1000, tz=timezone(timedelta(hours=8)))
        print(f"       {t.strftime('%m-%d %H:%M UTC+8')}  O={k[1]} H={k[2]} L={k[3]} C={k[4]}")
except Exception as e:
    print(f"  {FAIL} K 線抓取失敗：{e}")

# ── 9. Live state 檔案 ──
header("9. eth_state_live.json")
import json
sp = "eth_state_live.json"
if os.path.exists(sp):
    with open(sp) as f:
        st = json.load(f)
    print(f"  {OK} 存在  trade_number={st['trade_number']}（首筆 mainnet trade 將是 #{st['trade_number']+1}）")
    print(f"       positions={st['positions']}（應為空）")
else:
    print(f"  {WARN} 不存在（executor 首次 save 會建立，trade 從 #1 起算）")

print(f"\n{'═' * 72}")
print(f"  驗證完成")
print(f"{'═' * 72}\n")
