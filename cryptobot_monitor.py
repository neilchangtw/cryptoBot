"""
v4 持倉監控 — Binance Futures
每 5 分鐘檢查持倉，管理 TP1 部分平倉 + 自適應移動止損。

出場邏輯：
  Phase 1：浮盈 >= 1×ATR → 平 10% + SL 移至保本
  Phase 2：自適應 ATR+RSI 移動止損
    - base_mult = 1.0 + (atr_pctile/100) × 1.5
    - RSI 加速：做多 RSI>65 / 做空 RSI<35 → ×0.6 收緊
    - trail_sl = trail_extreme ± ATR × mult（最低保本）
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime
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
    log_tp1 as journal_log_tp1,
    log_exit as journal_log_exit,
    find_trade_id_by_position,
)

load_dotenv()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
TP1_PCT = 0.10           # TP1 平倉比例 10%
TP1_ATR_MULT = 1.0       # TP1 觸發距離

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
#  5m 指標（v4：ATR, RSI, ATR Percentile）
# ══════════════════════════════════════════════════════════════

def get_5m_indicators(symbol):
    """取最新 5m ATR(14), RSI(14), ATR Percentile"""
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
#  自適應移動止損計算
# ══════════════════════════════════════════════════════════════

def calc_adaptive_trail_sl(side, trail_extreme, entry, atr, rsi, atr_pctile):
    """
    自適應 ATR+RSI 移動止損：
    base_mult = 1.0 + (atr_pctile/100) × 1.5
      低波動(0): 1.0x（收緊） → 高波動(100): 2.5x（放鬆）
    RSI 加速：做多 RSI>65 → ×0.6 / 做空 RSI<35 → ×0.6
    最低為保本（entry price）
    """
    base_mult = 1.0 + (atr_pctile / 100) * 1.5

    if side == "long":
        mult = base_mult * 0.6 if rsi > 65 else base_mult
        trail_sl = trail_extreme - atr * mult
        return max(trail_sl, entry)  # 不低於保本
    else:
        mult = base_mult * 0.6 if rsi < 35 else base_mult
        trail_sl = trail_extreme + atr * mult
        return min(trail_sl, entry)  # 不高於保本


# ══════════════════════════════════════════════════════════════
#  持倉監控邏輯
# ══════════════════════════════════════════════════════════════

def monitor_position(pos, state, symbol):
    """
    監控單一持倉：Phase 1 (TP1) → Phase 2 (自適應 Trail)
    """
    side = pos["side"]
    entry = pos["entry_price"]
    mark = pos["mark_price"]
    size = pos["size"]
    pos_key = f"{symbol}_{side}"

    # 取 5m 指標
    indicators = get_5m_indicators(symbol)
    if indicators is None or indicators["atr"] is None:
        print(f"  [{pos_key}] Cannot get 5m indicators, skip")
        return

    atr = indicators["atr"]
    rsi = indicators["rsi"] if indicators["rsi"] is not None else 50.0
    atr_pctile = indicators["atr_pctile"]

    # 初始化狀態（首次偵測到持倉時）
    if "phase" not in state:
        state["phase"] = 1
        if side == "long":
            state["tp1_target"] = entry + TP1_ATR_MULT * atr
        else:
            state["tp1_target"] = entry - TP1_ATR_MULT * atr
        state["trail_hi"] = mark
        state["trail_lo"] = mark
        state["initial_atr"] = atr
        state["entry_price"] = entry
        # 從日誌找到對應的 trade_id
        state["trade_id"] = find_trade_id_by_position(side, entry) or ""
        print(f"  [{pos_key}] Init state: TP1={state['tp1_target']:.2f} ATR={atr:.2f} "
              f"trade_id={state['trade_id']}")

    # 更新極值與最新 mark price
    state["last_mark"] = mark
    if mark > state["trail_hi"]:
        state["trail_hi"] = mark
    if mark < state["trail_lo"]:
        state["trail_lo"] = mark

    d = 1 if side == "long" else -1
    unrealized = d * (mark - entry) * size

    print(f"  [{pos_key}] entry={entry:.2f} mark={mark:.2f} size={size} "
          f"unrealized={unrealized:.2f} phase={state['phase']} "
          f"RSI={rsi:.1f} ATR_pctile={atr_pctile:.0f}")

    # ── Phase 1：TP1 觸發 ────────────────────────────────────
    if state["phase"] == 1:
        tp1_target = state["tp1_target"]

        if side == "long":
            tp1_hit = mark >= tp1_target
        else:
            tp1_hit = mark <= tp1_target

        if tp1_hit:
            tick_size, qty_step, min_qty = get_symbol_info(symbol)
            tp1_qty = round_to_lot(size * TP1_PCT, qty_step, min_qty)
            close_side = "SELL" if side == "long" else "BUY"

            direction = "LONG" if side == "long" else "SHORT"
            profit_pct = d * (mark - entry) / entry * 100

            msg = (f"<b>[TP1] {direction} {symbol}</b>\n"
                   f"進場: {entry:.2f} → 現價: {mark:.2f} ({profit_pct:+.2f}%)\n"
                   f"平倉 {TP1_PCT * 100:.0f}%: {close_side} {tp1_qty}\n"
                   f"SL 移至保本: {entry:.2f}\n"
                   f"切換 Phase 2: 自適應 ATR+RSI Trail")
            print(f"  {msg}")
            send_telegram_message(msg)

            # 平 10%
            place_order(symbol, close_side, qty=tp1_qty, reduce_only=True,
                        strategy_id="v4_tp1")

            # SL 移到進場價（保本）— 重試 3 次確保成功
            sl_updated = False
            for attempt in range(3):
                try:
                    update_stop_loss(symbol, entry, side)
                    sl_updated = True
                    break
                except Exception as e:
                    print(f"  SL update attempt {attempt+1} failed: {e}")
                    time.sleep(1)

            if not sl_updated:
                err_msg = (f"<b>[WARNING] SL 保本更新失敗!</b>\n"
                           f"{pos_key} 進場價 {entry:.2f}\n"
                           f"請手動檢查止損位置")
                print(f"  {err_msg}")
                send_telegram_message(err_msg)

            state["phase"] = 2
            state["trail_hi"] = max(state["trail_hi"], mark)
            state["trail_lo"] = min(state["trail_lo"], mark)

            # 日誌記錄 TP1
            if state.get("trade_id"):
                try:
                    journal_log_tp1(state["trade_id"], mark)
                except Exception as e:
                    print(f"  Journal TP1 error: {e}")
        else:
            pct_to_tp1 = abs(tp1_target - mark) / entry * 100
            print(f"    TP1 target: {tp1_target:.2f} "
                  f"(差 {abs(tp1_target - mark):.2f} / {pct_to_tp1:.2f}%)")
        return

    # ── Phase 2：自適應移動止損 ──────────────────────────────
    tick_size, _, _ = get_symbol_info(symbol)

    base_mult = 1.0 + (atr_pctile / 100) * 1.5

    if side == "long":
        trail_sl = calc_adaptive_trail_sl(
            "long", state["trail_hi"], entry, atr, rsi, atr_pctile
        )
        trail_sl = round_to_tick(trail_sl, tick_size)
        mult_used = base_mult * 0.6 if rsi > 65 else base_mult
        last_sl = state.get("last_trail_sl", entry)

        # 只在新 SL 比上次更高（更好）且高於保本時才更新
        if trail_sl > entry and trail_sl > last_sl:
            print(f"    Adaptive Trail: hi={state['trail_hi']:.2f} "
                  f"trail_sl={trail_sl:.2f} (mult={mult_used:.2f} ATR={atr:.2f})")
            try:
                update_stop_loss(symbol, trail_sl, side)
                state["last_trail_sl"] = trail_sl
            except Exception as e:
                print(f"    SL update error: {e}")
        else:
            reason = "<=entry" if trail_sl <= entry else "<=last_sl"
            print(f"    Trail SL={trail_sl:.2f} {reason}, no update (last={last_sl:.2f})")

    else:  # short
        trail_sl = calc_adaptive_trail_sl(
            "short", state["trail_lo"], entry, atr, rsi, atr_pctile
        )
        trail_sl = round_to_tick(trail_sl, tick_size)
        mult_used = base_mult * 0.6 if rsi < 35 else base_mult
        last_sl = state.get("last_trail_sl", entry)

        # 只在新 SL 比上次更低（更好）且低於保本時才更新
        if trail_sl < entry and trail_sl < last_sl:
            print(f"    Adaptive Trail: lo={state['trail_lo']:.2f} "
                  f"trail_sl={trail_sl:.2f} (mult={mult_used:.2f} ATR={atr:.2f})")
            try:
                update_stop_loss(symbol, trail_sl, side)
                state["last_trail_sl"] = trail_sl
            except Exception as e:
                print(f"    SL update error: {e}")
        else:
            reason = ">=entry" if trail_sl >= entry else ">=last_sl"
            print(f"    Trail SL={trail_sl:.2f} {reason}, no update (last={last_sl:.2f})")


def _log_position_exit(old_state, pos_key):
    """持倉消失時記錄出場到日誌"""
    trade_id = old_state.get("trade_id")
    if not trade_id:
        return

    entry = old_state.get("entry_price", 0)
    last_mark = old_state.get("last_mark", entry)
    phase = old_state.get("phase", 1)
    # pos_key 格式: "BTCUSDT_long_12345.00" → 取 side
    parts = pos_key.split("_")
    side = parts[1] if len(parts) >= 2 else "long"

    # 判斷出場原因
    if phase == 1:
        exit_reason = "sl_hit"
    else:
        exit_reason = "adaptive_trail"

    # 計算 PnL（近似值，用最後看到的 mark price）
    d = 1 if side == "long" else -1
    # 出場價用最後的 mark price 近似
    exit_price = last_mark
    pnl_pct = d * (exit_price - entry) / entry * 100 if entry > 0 else 0

    # 取最新指標（可能為 None）
    rsi_exit = None
    atr_pctile_exit = None
    try:
        indicators = get_5m_indicators(SYMBOL)
        if indicators:
            rsi_exit = indicators.get("rsi")
            atr_pctile_exit = indicators.get("atr_pctile")
    except Exception:
        pass

    try:
        journal_log_exit(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_reason=exit_reason,
            realized_pnl=0,  # 實際 PnL 由交易所記錄，這裡用 0 佔位
            pnl_pct=pnl_pct,
            rsi_exit=rsi_exit,
            atr_pctile_exit=atr_pctile_exit,
        )

        direction = "LONG" if side == "long" else "SHORT"
        msg = (f"<b>[出場] {direction} {SYMBOL}</b>\n"
               f"原因: {exit_reason}\n"
               f"進場: {entry:.2f} → 出場: {exit_price:.2f} ({pnl_pct:+.2f}%)\n"
               f"Phase: {phase}")
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

        print(f"\n--- Position Summary ---")
        print(f"  Balance: {balance:.2f} USDT  Unrealized: {total_pnl:+.2f}")
        for p in positions:
            d = "L" if p["side"] == "long" else "S"
            ep_tag = f"{p['entry_price']:.2f}"
            pos_key = f"{symbol}_{p['side']}_{ep_tag}"
            st = state.get(pos_key, {})
            phase = st.get("phase", "?")
            print(f"  {d} {p['size']} @ {p['entry_price']:.2f} "
                  f"PnL {p['unrealized_pnl']:+.2f} [Phase {phase}]")
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
    print(f"  v4 Position Monitor (Adaptive ATR+RSI Trail)")
    print(f"  Mode: {mode}  Symbol: {SYMBOL}")
    print(f"  Check Interval: {CHECK_INTERVAL}s")
    print(f"  TP1: {TP1_PCT * 100:.0f}% at {TP1_ATR_MULT}x ATR → Phase 2")
    print(f"  Trail: Adaptive ATR(pctile) + RSI Acceleration")
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
