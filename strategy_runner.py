"""
v4 策略信號引擎 — 5m 均值回歸 (Strategy H: RSI + BB 雙重確認)
每 5 分鐘執行一次，偵測進場信號後呼叫 binance_trade 下單。

驗證結果：
  做多：RSI<30 + Close<BB_Lower + 結構止損 + 自適應Trail
  做空：RSI>70 + Close>BB_Upper + 結構止損 + 自適應Trail
  OOS +$6,841 | WR 87.4% | PF 35.29 | 4/4 WF folds profitable
"""
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv

import pandas as pd
import numpy as np

# 確保能 import 同目錄及 backtest/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))

from data_fetcher import fetch_latest_klines
from binance_trade import (
    place_order, get_positions, set_leverage, get_symbol_info,
    get_available_balance, round_to_tick, SYMBOL, LEVERAGE, MARGIN_PER_TRADE
)
from telegram_notify import send_telegram_message
from trade_journal import log_entry as journal_log_entry

load_dotenv()

# ── 心跳 & 統計 ─────────────────────────────────────────────────
_heartbeat_last = 0
HEARTBEAT_INTERVAL = 3600  # 每小時發一次心跳
_scan_count = 0
_signal_count = 0

# ── 策略參數（v4 Strategy H: 5m 雙重確認均值回歸）───────────────
TP1_ATR_MULT = 1.0       # TP1 = entry ± 1.0×ATR(5m)
SL_BUFFER_MULT = 0.3     # 結構止損緩衝 = 0.3×ATR
SWING_WINDOW = 5         # Swing High/Low 確認窗口

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))   # 5 分鐘
MAX_SAME_DIRECTION = int(os.getenv("MAX_SAME_DIRECTION", 3))
TP1_PCT = 0.10  # TP1 平 10% 倉位

# 紀錄上次信號的 5m bar 時間，避免同根 K 線重複觸發
_last_signal_time = {"long": None, "short": None}
# (已移除 _entry_count 計數器，改用 get_positions 即時查詢持倉數量，與回測一致)


# ══════════════════════════════════════════════════════════════
#  5m 指標計算（v4：RSI, BB, ATR, Swing, ATR Percentile）
# ══════════════════════════════════════════════════════════════

def compute_5m_indicators(df):
    """計算 5m K 線指標：RSI(14), BB(20,2), ATR(14), ATR Pctile, Swing H/L(5)"""
    df = df.copy()

    # ATR(14)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # RSI(14) — Wilder's smoothing（與回測一致）
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + g / l)

    # Bollinger Bands(20, 2)
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    # ATR Percentile（最近 100 根 K 線的百分位排名）
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50, raw=False
    )

    # Swing High/Low（window=5，ffill 確保只用已確認的擺點）
    w = SWING_WINDOW
    sh = pd.Series(np.nan, index=df.index)
    sl_s = pd.Series(np.nan, index=df.index)
    for i in range(w, len(df) - w):
        seg = df.iloc[i - w:i + w + 1]
        if df["high"].iloc[i] == seg["high"].max():
            sh.iloc[i] = df["high"].iloc[i]
        if df["low"].iloc[i] == seg["low"].min():
            sl_s.iloc[i] = df["low"].iloc[i]
    df["swing_high"] = sh.ffill()
    df["swing_low"] = sl_s.ffill()

    return df


# ══════════════════════════════════════════════════════════════
#  進場信號檢查（v4 均值回歸）
# ══════════════════════════════════════════════════════════════

def check_entry_signal(row, side):
    """
    v4 均值回歸進場條件（雙重確認）：
    做多：RSI(14) < 30 AND Close < BB_Lower(20,2)
    做空：RSI(14) > 70 AND Close > BB_Upper(20,2)
    """
    rsi = row.get("rsi")
    if rsi is None or (isinstance(rsi, float) and np.isnan(rsi)):
        return False

    if side == "long":
        bb_lower = row.get("bb_lower")
        if bb_lower is None or (isinstance(bb_lower, float) and np.isnan(bb_lower)):
            return False
        return rsi < 30 and row["close"] < bb_lower
    else:
        bb_upper = row.get("bb_upper")
        if bb_upper is None or (isinstance(bb_upper, float) and np.isnan(bb_upper)):
            return False
        return rsi > 70 and row["close"] > bb_upper


def calc_structural_sl(row, side):
    """
    結構止損（± 0.3×ATR 緩衝防掃針）：
    做多 SL = Swing Low(5) - 0.3 × ATR(14)
    做空 SL = Swing High(5) + 0.3 × ATR(14)
    """
    atr = row.get("atr")
    if atr is None or (isinstance(atr, float) and np.isnan(atr)):
        return None

    if side == "long":
        swing_low = row.get("swing_low")
        if swing_low is None or (isinstance(swing_low, float) and np.isnan(swing_low)):
            return None
        return swing_low - SL_BUFFER_MULT * atr
    else:
        swing_high = row.get("swing_high")
        if swing_high is None or (isinstance(swing_high, float) and np.isnan(swing_high)):
            return None
        return swing_high + SL_BUFFER_MULT * atr


# ══════════════════════════════════════════════════════════════
#  主要信號偵測
# ══════════════════════════════════════════════════════════════

def fetch_and_prepare_data(symbol):
    """抓取最新 5m K 線並計算所有指標"""
    df_5m = fetch_latest_klines(symbol, "5m", limit=200)
    df_5m = compute_5m_indicators(df_5m)
    return df_5m


def check_signals(df_5m, symbol):
    """
    檢查最近完成的 5m K 線是否觸發進場信號。
    使用 iloc[-2]（最近完成的 bar），避免使用未完成的當前 bar。
    回傳 list of signal dicts
    """
    if len(df_5m) < 3:
        return []

    # 使用最近完成的 bar（倒數第二根）
    curr = df_5m.iloc[-2].to_dict()
    curr_time = df_5m.index[-2]

    atr = curr.get("atr")
    if atr is None or np.isnan(atr):
        return []

    # 查詢現有持倉，用實際持倉數量判斷（不用計數器，與回測一致）
    positions = get_positions(symbol)
    long_count = sum(1 for p in positions if p["side"] == "long")
    short_count = sum(1 for p in positions if p["side"] == "short")

    signals = []
    tick_size, _, _ = get_symbol_info(symbol)

    # ── 做多檢查 ────────────────────────────────────────────
    if long_count < MAX_SAME_DIRECTION:
        if _last_signal_time["long"] != curr_time:
            if check_entry_signal(curr, "long"):
                sl = calc_structural_sl(curr, "long")
                if sl is not None:
                    entry = curr["close"]
                    tp1 = entry + TP1_ATR_MULT * atr
                    sl = round_to_tick(sl, tick_size)
                    signals.append({
                        "side": "long",
                        "order_side": "BUY",
                        "entry_price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "atr": atr,
                        "rsi": curr["rsi"],
                        "atr_pctile": curr.get("atr_pctile", 50),
                        "bb_lower": curr.get("bb_lower", 0),
                        "bb_upper": curr.get("bb_upper", 0),
                        "swing_level": curr.get("swing_low", 0),
                    })
                    _last_signal_time["long"] = curr_time

    # ── 做空檢查 ────────────────────────────────────────────
    if short_count < MAX_SAME_DIRECTION:
        if _last_signal_time["short"] != curr_time:
            if check_entry_signal(curr, "short"):
                sl = calc_structural_sl(curr, "short")
                if sl is not None:
                    entry = curr["close"]
                    tp1 = entry - TP1_ATR_MULT * atr
                    sl = round_to_tick(sl, tick_size)
                    signals.append({
                        "side": "short",
                        "order_side": "SELL",
                        "entry_price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "atr": atr,
                        "rsi": curr["rsi"],
                        "atr_pctile": curr.get("atr_pctile", 50),
                        "bb_lower": curr.get("bb_lower", 0),
                        "bb_upper": curr.get("bb_upper", 0),
                        "swing_level": curr.get("swing_high", 0),
                    })
                    _last_signal_time["short"] = curr_time

    return signals


# ══════════════════════════════════════════════════════════════
#  執行下單
# ══════════════════════════════════════════════════════════════

def execute_signal(signal, symbol):
    """
    執行進場信號：市價開倉 + SL。
    TP1 由 monitor 管理（部分平倉 10%），不在交易所掛 TP 單。
    """
    global _signal_count

    side = signal["order_side"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    entry = signal["entry_price"]
    atr = signal["atr"]

    # 餘額檢查
    balance = get_available_balance()
    if balance < MARGIN_PER_TRADE:
        msg = f"<b>[餘額不足]</b> {balance:.2f} USDT < {MARGIN_PER_TRADE} USDT，跳過開倉"
        print(msg)
        send_telegram_message(msg)
        return None

    # 風報比計算
    risk = abs(entry - sl)
    reward = abs(tp1 - entry)
    rr = reward / risk if risk > 0 else 0

    direction = "LONG" if signal["side"] == "long" else "SHORT"
    msg = (f"<b>[v4 {direction}] {symbol}</b>\n"
           f"價格: {entry:.2f}  RSI: {signal['rsi']:.1f}\n"
           f"SL: {sl:.2f} (結構止損, 距 {risk:.2f})\n"
           f"TP1: {tp1:.2f} (距 {reward:.2f})\n"
           f"R:R = 1:{rr:.1f}  ATR(5m): {atr:.2f}\n"
           f"餘額: {balance:.2f} USDT\n"
           f"Trail: 自適應 ATR+RSI")
    print(msg)
    send_telegram_message(msg)

    # 市價開倉 + SL（不掛 TP，由 monitor 管理 TP1 部分平倉）
    res = place_order(
        symbol=symbol,
        side=side,
        stop_loss=sl,
        strategy_id="v4",
    )

    if res:
        _signal_count += 1
        print(f"  Order placed successfully: {res.get('orderId', '?')}")

        # 查詢實際成交資訊（Testnet 的 avgPrice/executedQty 可能為 0）
        time.sleep(0.5)
        try:
            pos_list = get_positions(symbol)
            matched = [p for p in pos_list if p["side"] == signal["side"]]
            if matched:
                avg_price = matched[0]["entry_price"]
                qty = matched[0]["size"]
            else:
                avg_price = entry
                qty = MARGIN_PER_TRADE * LEVERAGE / entry
        except Exception:
            avg_price = float(res.get("avgPrice", 0)) or entry
            qty = float(res.get("executedQty", 0)) or (MARGIN_PER_TRADE * LEVERAGE / entry)

        # 寫入交易日誌
        trade_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{signal['side']}"
        try:
            journal_log_entry(
                trade_id=trade_id,
                side=signal["side"],
                entry_price=avg_price,
                qty=qty,
                margin=MARGIN_PER_TRADE,
                rsi=signal["rsi"],
                atr=signal["atr"],
                atr_pctile=signal.get("atr_pctile", 50),
                bb_lower=signal.get("bb_lower", 0),
                bb_upper=signal.get("bb_upper", 0),
                structural_sl=signal["sl"],
                tp1_target=signal["tp1"],
                swing_level=signal.get("swing_level", 0),
            )
        except Exception as e:
            print(f"  Journal log error: {e}")
    else:
        print(f"  Order failed")

    return res


# ══════════════════════════════════════════════════════════════
#  主迴圈
# ══════════════════════════════════════════════════════════════

def send_heartbeat(symbol):
    """每小時印一次狀態"""
    global _heartbeat_last
    now = time.time()
    if now - _heartbeat_last < HEARTBEAT_INTERVAL:
        return
    _heartbeat_last = now

    try:
        balance = get_available_balance()
        positions = get_positions(symbol)
        long_pos = [p for p in positions if p["side"] == "long"]
        short_pos = [p for p in positions if p["side"] == "short"]
        total_pnl = sum(p["unrealized_pnl"] for p in positions)

        print(f"\n--- Heartbeat ---")
        print(f"  Balance: {balance:.2f} USDT")
        print(f"  Positions: {len(long_pos)}L / {len(short_pos)}S  PnL: {total_pnl:+.2f}")
        print(f"  Scans: {_scan_count}  Signals: {_signal_count}")
        print(f"  Positions: L={len(long_pos)}/{MAX_SAME_DIRECTION} S={len(short_pos)}/{MAX_SAME_DIRECTION}")
    except Exception as e:
        print(f"heartbeat error: {e}")


def run_once(symbol):
    """執行一次信號偵測 + 下單"""
    global _scan_count
    _scan_count += 1

    try:
        df_5m = fetch_and_prepare_data(symbol)

        # 顯示最新指標（使用最近完成的 bar）
        latest = df_5m.iloc[-2]
        rsi = latest.get("rsi", 0)
        bb_upper = latest.get("bb_upper", 0)
        bb_lower = latest.get("bb_lower", 0)
        atr = latest.get("atr", 0)
        close_price = latest["close"]

        # 接近進場條件提示
        long_near = rsi < 35 if not np.isnan(rsi) else False
        short_near = rsi > 65 if not np.isnan(rsi) else False

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] #{_scan_count} {symbol} "
              f"Price: {close_price:.2f}  RSI: {rsi:.1f}  "
              f"BB: [{bb_lower:.2f}, {bb_upper:.2f}]  ATR: {atr:.2f}  "
              f"Near: {'L!' if long_near else '-'}/{'S!' if short_near else '-'}")

        signals = check_signals(df_5m, symbol)

        if signals:
            for sig in signals:
                execute_signal(sig, symbol)
        else:
            print("  No signal")

        # 心跳
        send_heartbeat(symbol)

    except Exception as e:
        err_msg = f"strategy_runner error: {e}\n{traceback.format_exc()}"
        print(err_msg)
        send_telegram_message(f"<b>[Runner 異常]</b>\n{e}")


def main():
    symbol = SYMBOL
    env = os.getenv("BINANCE_TESTNET", "true").lower()
    mode = "TESTNET" if env == "true" else "PRODUCTION"

    print("=" * 60)
    print(f"  v4 Strategy Runner (Strategy H: RSI+BB Mean Reversion)")
    print(f"  Mode: {mode}  Symbol: {symbol}")
    print(f"  Leverage: {LEVERAGE}x  Margin: {MARGIN_PER_TRADE} USDT")
    print(f"  Check Interval: {CHECK_INTERVAL}s")
    print(f"  Max Same Direction: {MAX_SAME_DIRECTION}")
    print(f"  Entry: Long RSI<30+BB_Lower / Short RSI>70+BB_Upper")
    print(f"  SL: Structural (Swing ± 0.3×ATR)")
    print(f"  Exit: TP1 10% at 1.0×ATR + Adaptive ATR+RSI Trail")
    print("=" * 60)

    # 設定槓桿
    set_leverage(symbol, LEVERAGE)

    balance = get_available_balance()
    positions = get_positions(symbol)
    long_n = sum(1 for p in positions if p["side"] == "long")
    short_n = sum(1 for p in positions if p["side"] == "short")

    print(f"  Balance: {balance:.2f} USDT")
    print(f"  Positions: {long_n}L / {short_n}S")

    # 啟動時檢查孤兒持倉（程式重啟後恢復追蹤）
    positions = get_positions(symbol)
    if positions:
        long_n = sum(1 for p in positions if p["side"] == "long")
        short_n = sum(1 for p in positions if p["side"] == "short")
        msg = (f"<b>[啟動偵測]</b> 發現 {len(positions)} 個既有持倉 "
               f"({long_n}L/{short_n}S)，Monitor 會自動接管管理")
        print(msg)
        send_telegram_message(msg)

    while True:
        run_once(symbol)
        print(f"  Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
