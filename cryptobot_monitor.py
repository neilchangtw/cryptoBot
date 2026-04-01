"""
v5 持倉監控 — Binance Futures
每分鐘檢查持倉，管理 TP1 全平 + 8h 時間止損。

出場邏輯（單階段，無 Phase 2）：
  TP1：mark_price 達 entry ± 1.5×ATR → 限價全平 100%
  TimeStop：持倉超過 8h（96 根 5m bar）→ 全平認錯
  SafeNet：交易所 STOP_MARKET ±3%（只防極端行情）
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))

from data_fetcher import fetch_latest_klines
from binance_trade import (
    get_positions, place_order, update_stop_loss, cancel_all_orders,
    get_symbol_info, get_available_balance, round_to_lot, round_to_tick,
    new_session, SYMBOL,
)
from telegram_notify import send_telegram_message
from trade_journal import (
    log_exit as journal_log_exit,
    find_trade_id_by_position,
    get_trade,
)

load_dotenv()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
TP1_ATR_MULT = 1.5           # TP1 = entry ± 1.5×ATR
TIME_STOP_MINUTES = 480      # 8h = 96 × 5min bars

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_state.json")

# 定時摘要
_summary_last = 0
SUMMARY_INTERVAL = 3600  # 每小時推送一次持倉摘要


# ══════════════════════════════════════════════════════════════
#  持倉狀態管理（JSON 持久化）
# ══════════════════════════════════════════════════════════════

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ══════════════════════════════════════════════════════════════
#  5m 指標（v5：ATR, RSI, ATR Percentile — 用於出場日誌）
# ══════════════════════════════════════════════════════════════

def get_5m_indicators(symbol):
    """取最新 5m ATR(14), RSI(14), ATR Percentile（用 iloc[-2] 已收盤 bar）"""
    try:
        df = fetch_latest_klines(symbol, "5m", limit=200, use_futures=True)

        # ATR(14)
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()

        # RSI(14) — Wilder's smoothing
        d = df["close"].diff()
        g = d.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        l_s = (-d.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        df["rsi"] = 100 - 100 / (1 + g / l_s)

        # ATR Percentile（最近 100 根 K 線）
        df["atr_pctile"] = df["atr"].rolling(100).apply(
            lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
            if x.max() != x.min() else 50, raw=False
        )

        # 使用 iloc[-2]（最近一根已收盤的 bar），與 strategy_runner 一致
        latest = df.iloc[-2]
        return {
            "atr": float(latest["atr"]) if not pd.isna(latest["atr"]) else None,
            "rsi": float(latest["rsi"]) if not pd.isna(latest["rsi"]) else None,
            "atr_pctile": float(latest["atr_pctile"]) if not pd.isna(latest["atr_pctile"]) else 50.0,
        }
    except Exception as e:
        print(f"get_5m_indicators error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  持倉監控邏輯（v5：TP1 全平 + 時間止損，無 Phase 2）
# ══════════════════════════════════════════════════════════════

def monitor_position(pos, state, symbol):
    """
    v5 監控單一持倉：
    1. TP1 = entry ± 1.5×ATR → 全平 100%
    2. TimeStop = 8h → 全平認錯
    3. SafeNet = 交易所 STOP_MARKET（被動觸發）
    """
    side = pos["side"]
    entry = pos["entry_price"]
    mark = pos["mark_price"]
    size = pos["size"]
    pos_key = f"{symbol}_{side}"

    # 取 5m 指標（用於初始化 TP1 target + 出場日誌）
    indicators = get_5m_indicators(symbol)
    if indicators is None or indicators["atr"] is None:
        print(f"  [{pos_key}] Cannot get 5m indicators, skip")
        return

    atr = indicators["atr"]

    # 初始化狀態（首次偵測到持倉時）
    if "tp1_target" not in state:
        # 從 journal 讀取 trade_id 和 entry_time
        trade_id = find_trade_id_by_position(side, entry)
        trade_data = get_trade(trade_id) if trade_id else None

        if side == "long":
            state["tp1_target"] = entry + TP1_ATR_MULT * atr
        else:
            state["tp1_target"] = entry - TP1_ATR_MULT * atr

        state["initial_atr"] = atr
        state["entry_price"] = entry
        state["trade_id"] = trade_id or ""
        state["closing_initiated"] = False

        # entry_time: 優先從 journal 讀取，fallback 用現在
        if trade_data and trade_data.get("entry_time"):
            state["entry_time"] = trade_data["entry_time"]
        else:
            state["entry_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # qty: 從 journal 讀取（用於精確平倉），fallback 用 position size
        if trade_data and trade_data.get("qty"):
            state["qty"] = trade_data["qty"]
        else:
            state["qty"] = size

        print(f"  [{pos_key}] Init: TP1={state['tp1_target']:.2f} ATR={atr:.2f} "
              f"trade_id={state['trade_id']} entry_time={state['entry_time']}")

    # 更新最新 mark price
    state["last_mark"] = mark

    d = 1 if side == "long" else -1
    unrealized = d * (mark - entry) * size

    # 計算時間止損倒數
    try:
        entry_dt = datetime.strptime(state["entry_time"], "%Y-%m-%d %H:%M:%S")
        elapsed_min = (datetime.now() - entry_dt).total_seconds() / 60
        remaining_min = TIME_STOP_MINUTES - elapsed_min
    except Exception:
        elapsed_min = 0
        remaining_min = TIME_STOP_MINUTES

    print(f"  [{pos_key}] entry={entry:.2f} mark={mark:.2f} size={size} "
          f"unrealized={unrealized:.2f} elapsed={elapsed_min:.0f}min "
          f"remaining={remaining_min:.0f}min")

    # ── 檢查 TP1 ────────────────────────────────────────────
    tp1_target = state["tp1_target"]
    if side == "long":
        tp1_hit = mark >= tp1_target
    else:
        tp1_hit = mark <= tp1_target

    if tp1_hit:
        _close_position_with_reason(pos, state, symbol, "tp1", indicators)
        return

    # ── 檢查時間止損 ────────────────────────────────────────
    if remaining_min <= 0:
        _close_position_with_reason(pos, state, symbol, "time_stop", indicators)
        return

    # ── 顯示進度 ────────────────────────────────────────────
    pct_to_tp1 = abs(tp1_target - mark) / entry * 100
    print(f"    TP1: {tp1_target:.2f} (差 {abs(tp1_target - mark):.2f} / {pct_to_tp1:.2f}%) "
          f"| TimeStop: {remaining_min:.0f}min")


def _close_position_with_reason(pos, state, symbol, reason, indicators):
    """TP1 或 TimeStop 觸發時全平倉位"""
    side = pos["side"]
    entry = state["entry_price"]
    mark = pos["mark_price"]
    trade_qty = state.get("qty", pos["size"])
    trade_id = state.get("trade_id", "")

    tick_size, qty_step, min_qty = get_symbol_info(symbol)
    close_qty = round_to_lot(trade_qty, qty_step, min_qty)
    close_side = "SELL" if side == "long" else "BUY"

    d = 1 if side == "long" else -1
    pnl_pct = d * (mark - entry) / entry * 100
    direction = "LONG" if side == "long" else "SHORT"

    # 標記為我方主動平倉（區別於安全網被動觸發）
    state["closing_initiated"] = True

    # 組裝 TG 通知
    try:
        elapsed_min = (datetime.now() - datetime.strptime(
            state["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    except Exception:
        elapsed_min = 0

    if reason == "tp1":
        msg = (f"<b>[TP1 全平] {direction} {symbol}</b>\n"
               f"進場: {entry:.2f} → 出場: {mark:.2f} ({pnl_pct:+.2f}%)\n"
               f"ATR: {state.get('initial_atr', 0):.2f}  持倉: {elapsed_min:.0f} min\n"
               f"已全平 100%: {close_side} {close_qty}")
    else:  # time_stop
        msg = (f"<b>[時間止損] {direction} {symbol}</b>\n"
               f"進場: {entry:.2f} → 出場: {mark:.2f} ({pnl_pct:+.2f}%)\n"
               f"持倉超過 {TIME_STOP_MINUTES // 60}h 未到 TP1，認錯出場\n"
               f"全平: {close_side} {close_qty}")

    print(f"  {msg}")
    send_telegram_message(msg)

    # 執行平倉
    place_order(symbol, close_side, qty=close_qty, reduce_only=True,
                strategy_id=f"v5_{reason}")

    # 記錄出場到日誌
    if trade_id:
        rsi_exit = indicators.get("rsi") if indicators else None
        atr_pctile_exit = indicators.get("atr_pctile") if indicators else None
        bars_held = elapsed_min / 5 if elapsed_min > 0 else None

        try:
            journal_log_exit(
                trade_id=trade_id,
                exit_price=mark,
                exit_reason=reason,
                realized_pnl=0,  # 實際 PnL 由交易所記錄
                pnl_pct=pnl_pct,
                rsi_exit=rsi_exit,
                atr_pctile_exit=atr_pctile_exit,
                bars_held=bars_held,
            )
        except Exception as e:
            print(f"  Journal exit log error: {e}")


def _log_position_exit(old_state, pos_key):
    """持倉消失時記錄出場到日誌"""
    # 如果是我方主動平倉（TP1 / TimeStop），已在上面處理過
    if old_state.get("closing_initiated"):
        return

    # 倉位消失且非我方操作 → 安全網觸發
    trade_id = old_state.get("trade_id")
    if not trade_id:
        return

    entry = old_state.get("entry_price", 0)
    last_mark = old_state.get("last_mark", entry)
    # pos_key 格式: "BTCUSDT_long_12345.00" → 取 side
    parts = pos_key.split("_")
    side = parts[1] if len(parts) >= 2 else "long"

    d = 1 if side == "long" else -1
    pnl_pct = d * (last_mark - entry) / entry * 100 if entry > 0 else 0

    # 取最新指標
    rsi_exit = None
    atr_pctile_exit = None
    try:
        indicators = get_5m_indicators(SYMBOL)
        if indicators:
            rsi_exit = indicators.get("rsi")
            atr_pctile_exit = indicators.get("atr_pctile")
    except Exception:
        pass

    # 計算持倉 bar 數
    bars_held = None
    try:
        entry_dt = datetime.strptime(old_state["entry_time"], "%Y-%m-%d %H:%M:%S")
        bars_held = (datetime.now() - entry_dt).total_seconds() / 300
    except Exception:
        pass

    try:
        journal_log_exit(
            trade_id=trade_id,
            exit_price=last_mark,
            exit_reason="safenet",
            realized_pnl=0,
            pnl_pct=pnl_pct,
            rsi_exit=rsi_exit,
            atr_pctile_exit=atr_pctile_exit,
            bars_held=bars_held,
        )

        direction = "LONG" if side == "long" else "SHORT"
        msg = (f"<b>[安全網觸發] {direction} {SYMBOL}</b>\n"
               f"進場: {entry:.2f} → SL 觸發 ≈ {last_mark:.2f} ({pnl_pct:+.2f}%)\n"
               f"⚠️ 極端行情觸發安全網止損")
        send_telegram_message(msg)
    except Exception as e:
        print(f"  Journal exit log error: {e}")


def send_position_summary(symbol, positions, state):
    """每小時印持倉摘要"""
    global _summary_last
    now = time.time()
    if now - _summary_last < SUMMARY_INTERVAL:
        return
    if not positions:
        return
    _summary_last = now

    try:
        balance = get_available_balance()
        total_pnl = sum(p["unrealized_pnl"] for p in positions)

        # 找最老持倉的 TimeStop 倒數
        oldest_remaining = None
        for p in positions:
            ep_tag = f"{p['entry_price']:.2f}"
            pk = f"{symbol}_{p['side']}_{ep_tag}"
            st = state.get(pk, {})
            if st.get("entry_time"):
                try:
                    entry_dt = datetime.strptime(st["entry_time"], "%Y-%m-%d %H:%M:%S")
                    remaining = TIME_STOP_MINUTES - (datetime.now() - entry_dt).total_seconds() / 60
                    if oldest_remaining is None or remaining < oldest_remaining:
                        oldest_remaining = remaining
                except Exception:
                    pass

        long_count = sum(1 for p in positions if p["side"] == "long")
        short_count = sum(1 for p in positions if p["side"] == "short")

        ts_text = f"  最近 TimeStop: {oldest_remaining:.0f}min" if oldest_remaining else ""

        msg = (f"<b>--- 每小時摘要 ---</b>\n"
               f"餘額: {balance:.2f} USDT  未實現: {total_pnl:+.2f}\n"
               f"持倉: {long_count}L / {short_count}S (上限 2/2)"
               f"{ts_text}")
        print(msg)
        send_telegram_message(msg)
    except Exception as e:
        print(f"summary error: {e}")


def monitor_all():
    """主監控函式：檢查所有持倉"""
    symbol = SYMBOL
    state = load_state()

    try:
        positions = get_positions(symbol)

        if not positions:
            print(f"  [{symbol}] No open positions")
            # 偵測已平倉 → 記錄出場
            keys_to_remove = [k for k in state if k.startswith(f"{symbol}_")]
            for k in keys_to_remove:
                _log_position_exit(state[k], k)
                del state[k]
            save_state(state)
            return

        print(f"  [{symbol}] {len(positions)} position(s)")

        # 偵測已不存在的持倉 → 記錄出場
        active_keys = {f"{symbol}_{p['side']}_{p['entry_price']:.2f}" for p in positions}
        keys_to_remove = [k for k in state if k.startswith(f"{symbol}_") and k not in active_keys]
        for k in keys_to_remove:
            _log_position_exit(state[k], k)
            print(f"  Removing stale state: {k}")
            del state[k]

        for pos in positions:
            # 用 side + entry_price 組合 key，支援同方向多倉
            ep_tag = f"{pos['entry_price']:.2f}"
            pos_key = f"{symbol}_{pos['side']}_{ep_tag}"
            pos_state = state.get(pos_key, {})
            monitor_position(pos, pos_state, symbol)
            state[pos_key] = pos_state

        save_state(state)

        # 定時推送摘要
        send_position_summary(symbol, positions, state)

    except Exception as e:
        err_msg = f"monitor error: {e}\n{traceback.format_exc()}"
        print(err_msg)
        send_telegram_message(f"<b>[Monitor 異常]</b>\n{e}")


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

def main():
    env = os.getenv("BINANCE_TESTNET", "true").lower()
    mode = "TESTNET" if env == "true" else "PRODUCTION"

    print("=" * 60)
    print(f"  v5 Position Monitor (TP1 Full Close + 8h TimeStop)")
    print(f"  Mode: {mode}  Symbol: {SYMBOL}")
    print(f"  Check Interval: {CHECK_INTERVAL}s")
    print(f"  TP1: 100% at {TP1_ATR_MULT}x ATR")
    print(f"  TimeStop: {TIME_STOP_MINUTES // 60}h ({TIME_STOP_MINUTES} min)")
    print(f"  SafeNet: ±3% (exchange STOP_MARKET)")
    print("=" * 60)

    balance = get_available_balance()
    print(f"  Balance: {balance:.2f} USDT")

    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Checking positions...")
        monitor_all()
        print(f"  Next check in {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
