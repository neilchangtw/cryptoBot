"""
v5 策略信號引擎 — 5m 均值回歸 + 3 進場過濾
每 5 分鐘執行一次，偵測進場信號後呼叫 binance_trade 下單。

v5 改版：
  進場：RSI<30 + BB_Lower + ATR_pctile≤75 + EMA21偏離<2% + 1h RSI轉向
  SL：安全網 ±3%（取代結構止損）
  出場：TP1 全平 100% at 1.5×ATR + 8h 時間止損（由 monitor 管理）
  OOS +$249 | WR 90.1% | PF 2.03
"""
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
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
from trade_journal import log_entry as journal_log_entry, get_open_trades

load_dotenv()

# ── 心跳 & 統計 ─────────────────────────────────────────────────
_heartbeat_last = 0
HEARTBEAT_INTERVAL = 3600  # 每小時發一次心跳
_scan_count = 0
_signal_count = 0

# ── 策略參數（v5: 5m 均值回歸 + 3 進場過濾 + 安全網 SL）────────
TP1_ATR_MULT = 1.5       # TP1 = entry ± 1.5×ATR(5m)
SAFENET_PCT = 0.03       # 安全網 SL ±3%
MAX_ATR_PCTILE = 75      # 進場過濾：ATR 百分位上限
MAX_EMA21_DEVIATION = 2.0  # 進場過濾：偏離 EMA21 上限 (%)
TIME_STOP_BARS = 96      # 時間止損 = 96 bars × 5min = 8h

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))   # 5 分鐘
MAX_SAME_DIRECTION = int(os.getenv("MAX_SAME_DIRECTION", 2))

# 紀錄上次信號的 5m bar 時間，避免同根 K 線重複觸發
_last_signal_time = {"long": None, "short": None}


# ══════════════════════════════════════════════════════════════
#  5m 指標計算（v5：RSI, BB, ATR, ATR Percentile, EMA21）
# ══════════════════════════════════════════════════════════════

def compute_5m_indicators(df):
    """計算 5m K 線指標：RSI(14), BB(20,2), ATR(14), ATR Pctile, EMA21"""
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

    # EMA21（v5 進場過濾用）
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["price_vs_ema21"] = (df["close"] - df["ema21"]) / df["ema21"] * 100

    return df


# ══════════════════════════════════════════════════════════════
#  1h RSI 計算（v5 進場過濾：趨勢方向確認）
# ══════════════════════════════════════════════════════════════

def get_1h_rsi_turn(symbol):
    """
    取得 1h RSI 轉向資訊。
    用 iloc[-2]（最後已收盤 1h bar）和 iloc[-3]（前一根）比較。
    做多：rsi_1h 不再下降 (curr >= prev)
    做空：rsi_1h 不再上升 (curr <= prev)
    回傳 (rsi_1h_curr, rsi_1h_prev, is_long_ok, is_short_ok) 或 None
    """
    try:
        df_1h = fetch_latest_klines(symbol, "1h", limit=30, use_futures=True)
        if len(df_1h) < 16:
            return None

        d = df_1h["close"].diff()
        g = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        l_s = (-d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        rsi_1h = 100 - 100 / (1 + g / l_s)

        curr = float(rsi_1h.iloc[-2])
        prev = float(rsi_1h.iloc[-3])

        if pd.isna(curr) or pd.isna(prev):
            return None

        return (curr, prev, curr >= prev, curr <= prev)
    except Exception as e:
        print(f"  get_1h_rsi_turn error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  進場信號檢查（v5: RSI+BB + ATR pctile + EMA21 偏離）
# ══════════════════════════════════════════════════════════════

def check_entry_signal(row, side):
    """
    v5 進場條件（5m 指標，不含 1h RSI — 在 check_signals 中獨立檢查）：
    做多：RSI<30 + Close<BB_Lower + ATR_pctile<=75 + |EMA21偏離|<2%
    做空：RSI>70 + Close>BB_Upper + ATR_pctile<=75 + |EMA21偏離|<2%
    """
    rsi = row.get("rsi")
    if rsi is None or (isinstance(rsi, float) and np.isnan(rsi)):
        return False

    atr_pctile = row.get("atr_pctile")
    if atr_pctile is None or (isinstance(atr_pctile, float) and np.isnan(atr_pctile)):
        return False

    price_vs_ema21 = row.get("price_vs_ema21")
    if price_vs_ema21 is None or (isinstance(price_vs_ema21, float) and np.isnan(price_vs_ema21)):
        return False

    # 共同過濾
    if atr_pctile > MAX_ATR_PCTILE:
        return False
    if abs(price_vs_ema21) >= MAX_EMA21_DEVIATION:
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


def calc_safenet_sl(entry_price, side):
    """
    安全網 SL: ±3%，只防極端行情。
    做多 SL = entry × 0.97
    做空 SL = entry × 1.03
    """
    if side == "long":
        return entry_price * (1 - SAFENET_PCT)
    else:
        return entry_price * (1 + SAFENET_PCT)


# ══════════════════════════════════════════════════════════════
#  主要信號偵測
# ══════════════════════════════════════════════════════════════

def fetch_and_prepare_data(symbol):
    """抓取最新 5m K 線並計算所有指標（使用合約 API）"""
    df_5m = fetch_latest_klines(symbol, "5m", limit=200, use_futures=True)
    df_5m = compute_5m_indicators(df_5m)
    return df_5m


def check_signals(df_5m, symbol):
    """
    v5 信號偵測：5m 指標過濾 → 1h RSI 轉向確認 → 安全網 SL。
    使用 iloc[-2]（最近完成的 bar），避免使用未完成的當前 bar。
    """
    if len(df_5m) < 3:
        return []

    curr = df_5m.iloc[-2].to_dict()
    curr_time = df_5m.index[-2]

    atr = curr.get("atr")
    if atr is None or np.isnan(atr):
        return []

    # 用 journal 的 open trade 數量判斷（Binance 合併同方向持倉，get_positions 永遠 0 或 1）
    long_count = len(get_open_trades(side="long"))
    short_count = len(get_open_trades(side="short"))

    # 先檢查 5m 基本信號，再決定是否取 1h 資料（避免不必要的 API 呼叫）
    need_long = (long_count < MAX_SAME_DIRECTION
                 and _last_signal_time["long"] != curr_time
                 and check_entry_signal(curr, "long"))
    need_short = (short_count < MAX_SAME_DIRECTION
                  and _last_signal_time["short"] != curr_time
                  and check_entry_signal(curr, "short"))

    if not need_long and not need_short:
        return []

    # 取 1h RSI 轉向（有 60s 快取，不會重複 API）
    rsi_1h_data = get_1h_rsi_turn(symbol)
    if rsi_1h_data is None:
        print("  [1h RSI] Cannot fetch 1h data, skip signals")
        return []

    rsi_1h_curr, rsi_1h_prev, is_long_ok, is_short_ok = rsi_1h_data

    signals = []
    tick_size, _, _ = get_symbol_info(symbol)

    # ── 做多檢查 ────────────────────────────────────────────
    if need_long and is_long_ok:
        entry = curr["close"]
        sl = calc_safenet_sl(entry, "long")
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
            "ema21_deviation": curr.get("price_vs_ema21", 0),
            "rsi_1h": rsi_1h_curr,
            "rsi_1h_prev": rsi_1h_prev,
        })
        _last_signal_time["long"] = curr_time
    elif need_long and not is_long_ok:
        print(f"  [1h RSI] Long blocked: 1h RSI {rsi_1h_curr:.1f} < prev {rsi_1h_prev:.1f}")

    # ── 做空檢查 ────────────────────────────────────────────
    if need_short and is_short_ok:
        entry = curr["close"]
        sl = calc_safenet_sl(entry, "short")
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
            "ema21_deviation": curr.get("price_vs_ema21", 0),
            "rsi_1h": rsi_1h_curr,
            "rsi_1h_prev": rsi_1h_prev,
        })
        _last_signal_time["short"] = curr_time
    elif need_short and not is_short_ok:
        print(f"  [1h RSI] Short blocked: 1h RSI {rsi_1h_curr:.1f} > prev {rsi_1h_prev:.1f}")

    return signals


# ══════════════════════════════════════════════════════════════
#  執行下單
# ══════════════════════════════════════════════════════════════

def execute_signal(signal, symbol):
    """
    v5 執行進場：市價開倉 + 安全網 SL。
    TP1 全平 + 時間止損由 monitor 管理。
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

    # 時間止損到期
    ts_deadline = datetime.now() + timedelta(minutes=TIME_STOP_BARS * 5)
    ts_deadline_str = ts_deadline.strftime("%Y-%m-%d %H:%M:%S")

    direction = "LONG" if signal["side"] == "long" else "SHORT"
    sl_pct = SAFENET_PCT * 100
    msg = (f"<b>[v5 {direction}] {symbol}</b>\n"
           f"價格: {entry:.2f}  RSI: {signal['rsi']:.1f}\n"
           f"安全網 SL: {sl:.2f} (-{sl_pct:.0f}%)\n"
           f"TP1: {tp1:.2f} (+{TP1_ATR_MULT}x ATR)\n"
           f"ATR(5m): {atr:.2f} (pctile: {signal.get('atr_pctile', 50):.0f})\n"
           f"EMA21 偏離: {signal.get('ema21_deviation', 0):+.1f}%\n"
           f"1h RSI: {signal.get('rsi_1h', 0):.1f} (prev: {signal.get('rsi_1h_prev', 0):.1f})\n"
           f"時間止損: {ts_deadline_str}\n"
           f"餘額: {balance:.2f} USDT")
    print(msg)
    send_telegram_message(msg)

    # 市價開倉 + 安全網 SL
    res = place_order(
        symbol=symbol,
        side=side,
        stop_loss=sl,
        strategy_id="v5",
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

        # 用實際進場價重算安全網 SL 和 TP1
        actual_sl = calc_safenet_sl(avg_price, signal["side"])
        if signal["side"] == "long":
            actual_tp1 = avg_price + TP1_ATR_MULT * atr
        else:
            actual_tp1 = avg_price - TP1_ATR_MULT * atr

        # 更新交易所 SL（用實際進場價重算的安全網）
        tick_size, _, _ = get_symbol_info(symbol)
        actual_sl = round_to_tick(actual_sl, tick_size)
        if actual_sl != sl:
            try:
                from binance_trade import update_stop_loss
                update_stop_loss(symbol, actual_sl, signal["side"])
                print(f"  SL updated to actual entry-based: {actual_sl:.2f}")
            except Exception as e:
                print(f"  SL update for actual entry failed: {e}")

        # 寫入交易日誌（v5 欄位）
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
                safenet_sl=actual_sl,
                tp1_target=actual_tp1,
                ema21_deviation=signal.get("ema21_deviation", 0),
                rsi_1h_entry=signal.get("rsi_1h"),
                rsi_1h_prev=signal.get("rsi_1h_prev"),
                time_stop_deadline=ts_deadline_str,
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
        total_pnl = sum(p["unrealized_pnl"] for p in positions)
        # 用 journal 計算實際開倉數量（Binance 合併同方向為一個持倉）
        long_open = len(get_open_trades(side="long"))
        short_open = len(get_open_trades(side="short"))

        print(f"\n--- Heartbeat ---")
        print(f"  Balance: {balance:.2f} USDT")
        print(f"  Open trades: {long_open}L / {short_open}S  PnL: {total_pnl:+.2f}")
        print(f"  Scans: {_scan_count}  Signals: {_signal_count}")
        print(f"  Entries: L={long_open}/{MAX_SAME_DIRECTION} S={short_open}/{MAX_SAME_DIRECTION}")
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
        atr_pctile = latest.get("atr_pctile", 0)
        ema21_dev = latest.get("price_vs_ema21", 0)
        close_price = latest["close"]

        # 接近進場條件提示
        long_near = rsi < 35 if not np.isnan(rsi) else False
        short_near = rsi > 65 if not np.isnan(rsi) else False

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] #{_scan_count} {symbol} "
              f"Price: {close_price:.2f}  RSI: {rsi:.1f}  "
              f"BB: [{bb_lower:.2f}, {bb_upper:.2f}]  ATR: {atr:.2f}  "
              f"ATR%: {atr_pctile:.0f}  EMA21: {ema21_dev:+.1f}%  "
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
    print(f"  v5 Strategy Runner (RSI+BB + 3 Filters + TimeStop)")
    print(f"  Mode: {mode}  Symbol: {symbol}")
    print(f"  Leverage: {LEVERAGE}x  Margin: {MARGIN_PER_TRADE} USDT")
    print(f"  Check Interval: {CHECK_INTERVAL}s")
    print(f"  Max Same Direction: {MAX_SAME_DIRECTION}")
    print(f"  Entry: RSI+BB + ATR_pctile<={MAX_ATR_PCTILE} + "
          f"EMA21<{MAX_EMA21_DEVIATION}% + 1h_RSI_turn")
    print(f"  SL: Safety Net ±{SAFENET_PCT*100:.0f}%")
    print(f"  Exit: TP1 100% at {TP1_ATR_MULT}×ATR + "
          f"8h TimeStop ({TIME_STOP_BARS} bars)")
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
