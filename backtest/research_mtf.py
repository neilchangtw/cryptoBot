"""
多時間框架策略研究：
  1h 定方向（RSI<30 多 / BB上軌 空）
  + 5m 找進場時機（6種）
  × 出場方式（3種）
  = 36 多單組合 + 36 空單組合

架構：
  - pending 信號列表：1h 觸發後，在窗口內等待 5m 條件
  - active 持倉列表：已進場，逐根 5m 管理
  - 正確支援最多 3 個同方向並行持倉

執行：python backtest/research_mtf.py
結果：backtest/results/research_mtf_YYYYMMDD.csv
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from itertools import product

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
from strategy_engine import compute_indicators, get_signals

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

RISK = {
    "margin_per_trade": 100,
    "leverage": 20,
    "max_same_direction": 3,
    "fee_rate": 0.0004,
    "tp1_pct": 0.10,
    "tp1_atr_mult": 1.0,
    "sl_swing_buffer": 0.3,
    "trail_atr_mult": 1.5,
    "entry_window_bars": 12,   # 5m 窗口（12×5m = 1h）
    "sl_fallback_atr": 1.5,   # SL 方向錯誤時的 fallback（ATR 倍數），0=跳過交易
}

LONG_ENTRIES  = ["h1_close", "m5_next_open", "m5_first_reversal",
                 "m5_ema9_cross", "m5_rsi_recover", "m5_engulf"]
SHORT_ENTRIES = ["h1_close", "m5_next_open", "m5_first_reversal",
                 "m5_ema9_cross", "m5_rsi_recover", "m5_engulf"]
LONG_EXITS    = ["atr_trail", "ema9_1h", "fixed_2atr"]
SHORT_EXITS   = ["ema9_1h", "atr_trail", "fixed_2atr"]


# ── 指標計算 ──────────────────────────────────────────────────
def compute_5m_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))
    return df


def spread_1h_to_5m(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> pd.DataFrame:
    """把 1h ATR / EMA9 用 ffill 映射到 5m 時間軸。"""
    df_5m = df_5m.copy()
    df_5m["atr_1h"]  = df_1h["atr"].reindex(df_5m.index, method="ffill")
    df_5m["ema9_1h"] = df_1h["ema9"].reindex(df_5m.index, method="ffill")
    return df_5m


# ── 進場條件判斷 ──────────────────────────────────────────────
def entry_check(row, prev_row, side: str, method: str) -> bool:
    """
    當根 5m K 線是否滿足進場條件？
    回傳 True 表示可以進場。
    """
    if method in ("h1_close", "m5_next_open"):
        return True  # 信號一出現立刻進場

    if method == "m5_first_reversal":
        return row["close"] > row["open"] if side == "long" else row["close"] < row["open"]

    if method == "m5_ema9_cross":
        if prev_row is None:
            return False
        if side == "long":
            return prev_row["close"] <= prev_row["ema9"] and row["close"] > row["ema9"]
        else:
            return prev_row["close"] >= prev_row["ema9"] and row["close"] < row["ema9"]

    if method == "m5_rsi_recover":
        if prev_row is None or pd.isna(row.get("rsi", np.nan)):
            return False
        if side == "long":
            return prev_row.get("rsi", 50) < 35 and row["rsi"] >= 35
        else:
            return prev_row.get("rsi", 50) > 65 and row["rsi"] <= 65

    if method == "m5_engulf":
        if prev_row is None:
            return False
        return row["close"] > prev_row["high"] if side == "long" else row["close"] < prev_row["low"]

    return False


def entry_price_for(row, method: str) -> float:
    """進場價：m5_next_open 用開盤，其餘用收盤。"""
    return row["open"] if method == "m5_next_open" else row["close"]


# ── 持倉管理（單根 5m K 線）────────────────────────────────
def check_position(pos: dict, row, config: dict):
    """
    對一個 active 持倉檢查當根 5m K 線。
    回傳 None = 繼續持有；回傳 dict = 平倉。
    """
    fee   = config["fee_rate"]
    tp1p  = config["tp1_pct"]
    tmult = config["trail_atr_mult"]
    side  = pos["side"]
    ep    = pos["entry_price"]

    if side == "long":
        # 止損
        if row["low"] <= pos["current_sl"]:
            return _close(pos, pos["current_sl"], "stop_loss", fee, tp1p, row.name)

        # TP1
        if not pos["tp1_done"] and row["high"] >= pos["tp1_price"]:
            g = (pos["tp1_price"] - ep) / ep * pos["notional"] * tp1p
            pos["tp1_pnl"] = g - pos["notional"] * tp1p * fee * 2
            pos["tp1_done"]  = True
            pos["phase"]     = "trailing"
            pos["current_sl"] = ep          # 保本
            pos["trail_hi"]   = max(pos["trail_hi"], row["high"])

        # Trailing
        if pos["phase"] == "trailing":
            pos["trail_hi"] = max(pos["trail_hi"], row["high"])

            em = pos["exit_method"]
            if em == "atr_trail":
                atr = row.get("atr_1h") or pos["atr_entry"]
                ts = pos["trail_hi"] - tmult * atr
                if row["low"] <= ts:
                    xp = max(ts, row["open"])
                    pnl = pos["tp1_pnl"] + _ppnl("long", ep, xp, pos["notional"], fee, 1 - tp1p)
                    return _close(pos, xp, "atr_trail", fee, tp1p, row.name, override_pnl=pnl)

            elif em == "ema9_1h":
                e9 = row.get("ema9_1h") or row.get("ema9")
                if e9 and row["close"] < e9:
                    pnl = pos["tp1_pnl"] + _ppnl("long", ep, row["close"], pos["notional"], fee, 1 - tp1p)
                    return _close(pos, row["close"], "ema9_1h", fee, tp1p, row.name, override_pnl=pnl)

            elif em == "fixed_2atr":
                tp = ep + 2.0 * pos["atr_entry"]
                if row["high"] >= tp:
                    pnl = pos["tp1_pnl"] + _ppnl("long", ep, tp, pos["notional"], fee, 1 - tp1p)
                    return _close(pos, tp, "fixed_2atr", fee, tp1p, row.name, override_pnl=pnl)

    elif side == "short":
        # 止損
        if row["high"] >= pos["current_sl"]:
            return _close(pos, pos["current_sl"], "stop_loss", fee, tp1p, row.name)

        # TP1
        if not pos["tp1_done"] and row["low"] <= pos["tp1_price"]:
            g = (ep - pos["tp1_price"]) / ep * pos["notional"] * tp1p
            pos["tp1_pnl"] = g - pos["notional"] * tp1p * fee * 2
            pos["tp1_done"]  = True
            pos["phase"]     = "trailing"
            pos["current_sl"] = ep          # 保本
            pos["trail_lo"]   = min(pos["trail_lo"], row["low"])

        # Trailing
        if pos["phase"] == "trailing":
            pos["trail_lo"] = min(pos["trail_lo"], row["low"])

            em = pos["exit_method"]
            if em == "ema9_1h":
                e9 = row.get("ema9_1h") or row.get("ema9")
                if e9 and row["close"] > e9:
                    pnl = pos["tp1_pnl"] + _ppnl("short", ep, row["close"], pos["notional"], fee, 1 - tp1p)
                    return _close(pos, row["close"], "ema9_1h", fee, tp1p, row.name, override_pnl=pnl)

            elif em == "atr_trail":
                atr = row.get("atr_1h") or pos["atr_entry"]
                ts = pos["trail_lo"] + tmult * atr
                if row["high"] >= ts:
                    xp = min(ts, row["open"])
                    pnl = pos["tp1_pnl"] + _ppnl("short", ep, xp, pos["notional"], fee, 1 - tp1p)
                    return _close(pos, xp, "atr_trail", fee, tp1p, row.name, override_pnl=pnl)

            elif em == "fixed_2atr":
                tp = ep - 2.0 * pos["atr_entry"]
                if row["low"] <= tp:
                    pnl = pos["tp1_pnl"] + _ppnl("short", ep, tp, pos["notional"], fee, 1 - tp1p)
                    return _close(pos, tp, "fixed_2atr", fee, tp1p, row.name, override_pnl=pnl)

    return None


def _ppnl(side, ep, xp, notional, fee, pct):
    d = 1 if side == "long" else -1
    n = notional * pct
    return d * (xp - ep) / ep * n - n * fee


def _close(pos, exit_p, reason, fee, tp1p, t, override_pnl=None):
    if override_pnl is not None:
        pnl = override_pnl
    elif pos["tp1_done"]:
        pnl = pos["tp1_pnl"] + _ppnl(pos["side"], pos["entry_price"], exit_p,
                                       pos["notional"], fee, 1 - tp1p)
    else:
        d = 1 if pos["side"] == "long" else -1
        gross = d * (exit_p - pos["entry_price"]) / pos["entry_price"] * pos["notional"]
        pnl = gross - pos["notional"] * fee * 2
    return {
        "side": pos["side"], "entry_price": pos["entry_price"], "entry_time": pos["entry_time"],
        "exit_price": exit_p, "exit_time": t, "exit_reason": reason,
        "pnl": pnl, "tp1_done": pos["tp1_done"],
    }


# ── 主回測引擎（pending + active 架構）────────────────────────
def run_combo(df_1h: pd.DataFrame, df_5m: pd.DataFrame,
              long_entry: str, long_exit: str,
              short_entry: str, short_exit: str,
              config: dict = None) -> dict:
    if config is None:
        config = RISK

    notional = config["margin_per_trade"] * config["leverage"]
    window   = config["entry_window_bars"]
    max_same = config["max_same_direction"]

    pending_longs  = []   # 待進場的多頭信號
    pending_shorts = []   # 待進場的空頭信號
    active         = []   # 已進場的持倉
    trades         = []

    last_1h_processed = None

    for i in range(len(df_5m)):
        t5   = df_5m.index[i]
        row  = df_5m.iloc[i]
        prev = df_5m.iloc[i - 1] if i > 0 else None

        if pd.isna(row.get("ema9", np.nan)):
            continue

        # ── 整點：檢查新的 1h 信號 ──────────────────────────
        h_floor = t5.floor("h")
        if t5 == h_floor:
            new_1h_t = h_floor - pd.Timedelta(hours=1)
            if new_1h_t != last_1h_processed and new_1h_t in df_1h.index:
                last_1h_processed = new_1h_t
                r1h = df_1h.loc[new_1h_t]

                if not pd.isna(r1h.get("rsi", np.nan)):
                    lc = sum(1 for p in active if p["side"] == "long")
                    sc = sum(1 for p in active if p["side"] == "short")

                    if r1h.get("long_signal") and lc < max_same:
                        sl_l = r1h.get("swing_low", np.nan)
                        sl_l = (sl_l - config["sl_swing_buffer"] * r1h["atr"]
                                if not pd.isna(sl_l) else r1h["close"] - 2.0 * r1h["atr"])
                        pending_longs.append({
                            "sl": sl_l, "atr": r1h["atr"],
                            "signal_time": new_1h_t, "elapsed": 0,
                        })

                    if r1h.get("short_signal") and sc < max_same:
                        sl_s = r1h.get("swing_high", np.nan)
                        sl_s = (sl_s + config["sl_swing_buffer"] * r1h["atr"]
                                if not pd.isna(sl_s) else r1h["close"] + 2.0 * r1h["atr"])
                        pending_shorts.append({
                            "sl": sl_s, "atr": r1h["atr"],
                            "signal_time": new_1h_t, "elapsed": 0,
                        })

        # ── 管理 active 持倉 ────────────────────────────────
        closed = []
        for pos in active:
            result = check_position(pos, row, config)
            if result:
                trades.append(result)
                closed.append(pos)
        for p in closed:
            active.remove(p)

        # ── 嘗試進場 pending 信號 ───────────────────────────
        lc = sum(1 for p in active if p["side"] == "long")
        sc = sum(1 for p in active if p["side"] == "short")

        entered_l, entered_s = [], []
        for sig in pending_longs:
            if lc >= max_same:
                break
            if entry_check(row, prev, "long", long_entry):
                ep = entry_price_for(row, long_entry)
                sl_val = sig["sl"]
                # SL 方向驗證：做多 SL 必須 < 進場價
                if sl_val >= ep:
                    fallback = config.get("sl_fallback_atr", 1.5)
                    if fallback > 0:
                        sl_val = ep - fallback * sig["atr"]
                    else:
                        continue  # 跳過此交易
                active.append({
                    "side": "long", "entry_price": ep, "entry_time": t5,
                    "current_sl": sl_val, "tp1_price": ep + config["tp1_atr_mult"] * sig["atr"],
                    "notional": notional, "atr_entry": sig["atr"],
                    "tp1_done": False, "phase": "initial", "tp1_pnl": 0.0,
                    "trail_hi": ep, "trail_lo": ep, "exit_method": long_exit,
                })
                entered_l.append(sig)
                lc += 1
            else:
                sig["elapsed"] += 1
                if sig["elapsed"] >= window:
                    entered_l.append(sig)   # 超時移除
        for s in entered_l:
            pending_longs.remove(s)

        for sig in pending_shorts:
            if sc >= max_same:
                break
            if entry_check(row, prev, "short", short_entry):
                ep = entry_price_for(row, short_entry)
                sl_val = sig["sl"]
                # SL 方向驗證：做空 SL 必須 > 進場價
                if sl_val <= ep:
                    fallback = config.get("sl_fallback_atr", 1.5)
                    if fallback > 0:
                        sl_val = ep + fallback * sig["atr"]
                    else:
                        continue  # 跳過此交易
                active.append({
                    "side": "short", "entry_price": ep, "entry_time": t5,
                    "current_sl": sl_val, "tp1_price": ep - config["tp1_atr_mult"] * sig["atr"],
                    "notional": notional, "atr_entry": sig["atr"],
                    "tp1_done": False, "phase": "initial", "tp1_pnl": 0.0,
                    "trail_hi": ep, "trail_lo": ep, "exit_method": short_exit,
                })
                entered_s.append(sig)
                sc += 1
            else:
                sig["elapsed"] += 1
                if sig["elapsed"] >= window:
                    entered_s.append(sig)
        for s in entered_s:
            pending_shorts.remove(s)

    # 強制平倉
    last = df_5m.iloc[-1]
    for pos in active:
        trades.append(_close(pos, last["close"], "end_of_data",
                              config["fee_rate"], config["tp1_pct"], df_5m.index[-1]))

    df_t = pd.DataFrame(trades) if trades else pd.DataFrame()
    return _metrics(df_t, long_entry, long_exit, short_entry, short_exit)


# ── 績效計算 ──────────────────────────────────────────────────
def _metrics(df_t, long_entry, long_exit, short_entry, short_exit):
    base = dict(long_entry=long_entry, long_exit=long_exit,
                short_entry=short_entry, short_exit=short_exit)
    empty = {**base, "total_pnl": 0, "long_pnl": 0, "short_pnl": 0,
             "trades": 0, "win_rate": 0, "profit_factor": 0, "max_drawdown": 0,
             "long_trades": 0, "short_trades": 0}
    if df_t.empty:
        return empty

    wins   = df_t[df_t["pnl"] > 0]["pnl"].sum()
    losses = abs(df_t[df_t["pnl"] <= 0]["pnl"].sum())
    eq = df_t["pnl"].cumsum()

    return {**base,
            "total_pnl":     round(df_t["pnl"].sum(), 2),
            "long_pnl":      round(df_t[df_t["side"] == "long"]["pnl"].sum(), 2) if "side" in df_t else 0,
            "short_pnl":     round(df_t[df_t["side"] == "short"]["pnl"].sum(), 2) if "side" in df_t else 0,
            "trades":        len(df_t),
            "long_trades":   int((df_t["side"] == "long").sum()) if "side" in df_t else 0,
            "short_trades":  int((df_t["side"] == "short").sum()) if "side" in df_t else 0,
            "win_rate":      round(len(df_t[df_t["pnl"] > 0]) / len(df_t) * 100, 1),
            "profit_factor": round(wins / max(losses, 1e-10), 2),
            "max_drawdown":  round((eq - eq.cummax()).min(), 2)}


# ── 全組合搜尋 ────────────────────────────────────────────────
def run_all_combos(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> tuple:
    """
    Round 1：找最佳多單組合（固定空單 baseline）
    Round 2：用最佳多單，找最佳空單組合
    """
    total = len(LONG_ENTRIES) * len(LONG_EXITS) + len(SHORT_ENTRIES) * len(SHORT_EXITS)
    done  = 0

    # Round 1 — 多單
    print("\n[Round 1] 搜尋最佳多單組合 (共 %d 組)..." % (len(LONG_ENTRIES) * len(LONG_EXITS)))
    long_results = []
    for l_entry, l_exit in product(LONG_ENTRIES, LONG_EXITS):
        r = run_combo(df_1h, df_5m, long_entry=l_entry, long_exit=l_exit,
                      short_entry="h1_close", short_exit="ema9_1h")
        long_results.append(r)
        done += 1
        print(f"  [{done:>2}/{total}] 多:{l_entry:<22}+{l_exit:<12} "
              f"PnL=${r['long_pnl']:>9.2f}  筆:{r['long_trades']:>4}  "
              f"WR={r['win_rate']:>5.1f}%  PF={r['profit_factor']:>6.2f}", end="\r")

    long_df = pd.DataFrame(long_results).sort_values("long_pnl", ascending=False)
    best_l  = long_df.iloc[0]
    print(f"\n  >> 最佳多單: {best_l['long_entry']} + {best_l['long_exit']}"
          f"  PnL=${best_l['long_pnl']:.2f}  筆:{best_l['long_trades']}")

    # Round 2 — 空單
    print(f"\n[Round 2] 搜尋最佳空單組合 (共 {len(SHORT_ENTRIES) * len(SHORT_EXITS)} 組)...")
    short_results = []
    for s_entry, s_exit in product(SHORT_ENTRIES, SHORT_EXITS):
        r = run_combo(df_1h, df_5m,
                      long_entry=best_l["long_entry"], long_exit=best_l["long_exit"],
                      short_entry=s_entry, short_exit=s_exit)
        short_results.append(r)
        done += 1
        print(f"  [{done:>2}/{total}] 空:{s_entry:<22}+{s_exit:<12} "
              f"PnL=${r['short_pnl']:>9.2f}  筆:{r['short_trades']:>4}  "
              f"WR={r['win_rate']:>5.1f}%  PF={r['profit_factor']:>6.2f}", end="\r")

    short_df = pd.DataFrame(short_results).sort_values("short_pnl", ascending=False)
    best_s   = short_df.iloc[0]
    print(f"\n  >> 最佳空單: {best_s['short_entry']} + {best_s['short_exit']}"
          f"  PnL=${best_s['short_pnl']:.2f}  筆:{best_s['short_trades']}")

    # Final
    print("\n[Final] 最佳多空合體...")
    final = run_combo(df_1h, df_5m,
                      long_entry=best_l["long_entry"],  long_exit=best_l["long_exit"],
                      short_entry=best_s["short_entry"], short_exit=best_s["short_exit"])
    return long_df, short_df, final


# ── 輸出排行榜 ────────────────────────────────────────────────
def print_top(df: pd.DataFrame, side: str, n: int = 10):
    is_long = (side == "多")
    pnl_col   = "long_pnl"   if is_long else "short_pnl"
    entry_col = "long_entry"  if is_long else "short_entry"
    exit_col  = "long_exit"   if is_long else "short_exit"
    cnt_col   = "long_trades" if is_long else "short_trades"

    print(f"\n{'='*72}")
    print(f"  TOP {n} {side}單組合")
    print(f"  {'進場方式':<24} {'出場方式':<14} {'損益':>9} {'筆數':>5} {'勝率':>6} {'PF':>7} {'回撤':>9}")
    print(f"  {'-'*68}")
    for _, r in df.head(n).iterrows():
        print(f"  {r[entry_col]:<24} {r[exit_col]:<14}"
              f"  ${r[pnl_col]:>8.2f}  {r[cnt_col]:>4}  {r['win_rate']:>5.1f}%"
              f"  {r['profit_factor']:>6.2f}  ${r['max_drawdown']:>8.2f}")


# ── 主程式 ──────────────────────────────────────────────────
if __name__ == "__main__":
    START = datetime(2025, 9, 1, tzinfo=timezone.utc)
    END   = datetime(2026, 3, 28, tzinfo=timezone.utc)

    print("=" * 72)
    print("  多時間框架策略研究：1h 方向 + 5m 進場")
    print("=" * 72)

    # 1. 抓資料（快取自動命中）
    df_1h_raw = fetch_klines("BTCUSDT", "1h", START, END)
    df_5m_raw = fetch_klines("BTCUSDT", "5m", START, END)

    # 2. 計算指標
    print("\n計算指標...")
    df_1h = compute_indicators(df_1h_raw)
    df_1h = get_signals(df_1h)
    df_5m = compute_5m_indicators(df_5m_raw)
    df_5m = spread_1h_to_5m(df_1h, df_5m)
    print(f"1h: {len(df_1h)} 根  |  5m: {len(df_5m)} 根")
    print(f"1h 多頭信號 (RSI<30):   {int(df_1h['long_signal'].sum())}")
    print(f"1h 空頭信號 (>BB上軌):  {int(df_1h['short_signal'].sum())}")

    # 3. 跑全部組合
    long_df, short_df, final = run_all_combos(df_1h, df_5m)

    # 4. 輸出排行榜
    print_top(long_df, "多")
    print_top(short_df, "空")

    print(f"\n{'='*72}")
    print("  最終策略（最佳多 + 最佳空）")
    print(f"{'='*72}")
    print(f"  多單進場: {final['long_entry']}   出場: {final['long_exit']}")
    print(f"  空單進場: {final['short_entry']}   出場: {final['short_exit']}")
    print(f"  總損益:   ${final['total_pnl']:>9.2f}")
    print(f"  多單:     ${final['long_pnl']:>9.2f}  ({final['long_trades']} 筆)")
    print(f"  空單:     ${final['short_pnl']:>9.2f}  ({final['short_trades']} 筆)")
    print(f"  勝率:     {final['win_rate']:>5.1f}%")
    print(f"  PF:       {final['profit_factor']:>9.2f}")
    print(f"  回撤:     ${final['max_drawdown']:>9.2f}")

    # 5. 存結果
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    long_df.to_csv(os.path.join(RESULTS_DIR,  f"{ts}_mtf_long_combos.csv"),  index=False)
    short_df.to_csv(os.path.join(RESULTS_DIR, f"{ts}_mtf_short_combos.csv"), index=False)
    pd.DataFrame([final]).to_csv(os.path.join(RESULTS_DIR, f"{ts}_mtf_best.csv"), index=False)
    print(f"\n結果已存到 backtest/results/{ts}_mtf_*.csv")
