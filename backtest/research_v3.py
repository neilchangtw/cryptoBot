"""
BTC 策略研究 v3 — 方案 C：1h 趨勢過濾 + 5m 進場
  - 1h 判斷方向（趨勢過濾器），不等收線
  - 5m 做實際進場（突破 + 量能確認）
  - ATR 止損（方向保證正確）

執行：python backtest/research_v3.py
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from itertools import product
from typing import Optional, Dict, List, Callable

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
from strategy_engine import compute_indicators_v2

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

RISK = {
    "margin_per_trade": 100,
    "leverage": 20,
    "max_same_direction": 3,
    "fee_rate": 0.0004,
    "tp1_pct": 0.10,
    "tp1_atr_mult": 1.0,
}


# ══════════════════════════════════════════════════════════════
#  5m 指標計算
# ══════════════════════════════════════════════════════════════

def compute_5m_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # EMA9, EMA21
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    # RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + gain / (loss + 1e-10)))

    # Donchian(20) — shift(1) 避免 look-ahead
    df["donchian_high_5m"] = df["high"].rolling(20).max().shift(1)
    df["donchian_low_5m"] = df["low"].rolling(20).min().shift(1)

    # Volume SMA(20)
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # ATR(14) on 5m
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr_5m"] = tr.rolling(14).mean()

    return df


def spread_1h_to_5m(df_1h: pd.DataFrame, df_5m: pd.DataFrame) -> pd.DataFrame:
    """把 1h 指標 forward-fill 到 5m"""
    cols_1h = ["ema9", "ema21", "atr", "macd_line", "macd_signal",
               "rsi", "bb_upper", "bb_lower", "bb_mid", "donchian_high", "donchian_low"]
    rename = {c: f"{c}_1h" for c in cols_1h}

    h = df_1h[cols_1h].rename(columns=rename)
    merged = df_5m.join(h, how="left")
    for c in rename.values():
        merged[c] = merged[c].ffill()
    return merged


# ══════════════════════════════════════════════════════════════
#  1h 趨勢過濾器（方向判斷，不是進場信號）
# ══════════════════════════════════════════════════════════════

def filt_ema_trend(row, side):
    """EMA9 vs EMA21 趨勢方向"""
    if side == "long":
        return row["ema9_1h"] > row["ema21_1h"]
    return row["ema9_1h"] < row["ema21_1h"]

def filt_macd_trend(row, side):
    """MACD > Signal = 多頭趨勢"""
    if side == "long":
        return row["macd_line_1h"] > row["macd_signal_1h"]
    return row["macd_line_1h"] < row["macd_signal_1h"]

def filt_price_ema(row, side):
    """價格在 EMA21 上方/下方"""
    if side == "long":
        return row["close"] > row["ema21_1h"]
    return row["close"] < row["ema21_1h"]

def filt_ema_macd(row, side):
    """EMA 趨勢 + MACD 確認（雙重過濾）"""
    return filt_ema_trend(row, side) and filt_macd_trend(row, side)

def filt_none(row, side):
    """無過濾（比較用）"""
    return True


FILTERS: Dict[str, Callable] = {
    "ema_trend":   filt_ema_trend,
    "macd_trend":  filt_macd_trend,
    "price_ema":   filt_price_ema,
    "ema_macd":    filt_ema_macd,
    "no_filter":   filt_none,
}


# ══════════════════════════════════════════════════════════════
#  5m 進場信號
# ══════════════════════════════════════════════════════════════

def _isnan(v):
    return v is None or v != v

def entry_donchian(row, prev, side):
    """5m Donchian 突破"""
    if side == "long":
        v = row.get("donchian_high_5m")
        if _isnan(v): return False
        return row["close"] > v
    v = row.get("donchian_low_5m")
    if _isnan(v): return False
    return row["close"] < v

def entry_vol_spike(row, prev, side):
    """量能放大 + 方向性 K線"""
    if row["volume"] <= 1.5 * row["vol_sma20"]:
        return False
    if side == "long":
        return row["close"] > row["open"]
    return row["close"] < row["open"]

def entry_donchian_vol(row, prev, side):
    """Donchian 突破 + 量能確認"""
    return entry_donchian(row, prev, side) and row["volume"] > 1.5 * row["vol_sma20"]

def entry_ema_cross(row, prev, side):
    """5m EMA9 穿越 EMA21"""
    if prev is None: return False
    if side == "long":
        return prev["ema9"] <= prev["ema21"] and row["ema9"] > row["ema21"]
    return prev["ema9"] >= prev["ema21"] and row["ema9"] < row["ema21"]

def entry_momentum(row, prev, side):
    """價格穿越 5m EMA21 + 量能"""
    if prev is None: return False
    if side == "long":
        return prev["close"] <= prev["ema21"] and row["close"] > row["ema21"] and row["volume"] > 1.5 * row["vol_sma20"]
    return prev["close"] >= prev["ema21"] and row["close"] < row["ema21"] and row["volume"] > 1.5 * row["vol_sma20"]

def entry_donchian_ema(row, prev, side):
    """Donchian 突破 + 5m EMA 方向一致"""
    if not entry_donchian(row, prev, side): return False
    if side == "long":
        return row["ema9"] > row["ema21"]
    return row["ema9"] < row["ema21"]


ENTRIES: Dict[str, Callable] = {
    "donchian":      entry_donchian,
    "vol_spike":     entry_vol_spike,
    "donchian_vol":  entry_donchian_vol,
    "ema_cross":     entry_ema_cross,
    "momentum":      entry_momentum,
    "donchian_ema":  entry_donchian_ema,
}


# ══════════════════════════════════════════════════════════════
#  止損 & 出場
# ══════════════════════════════════════════════════════════════

SL_METHODS = ["atr_1.5", "atr_2.0", "atr_2.5"]
EXIT_METHODS = ["atr_trail_1.5", "atr_trail_2.5", "ema9_trail", "fixed_2atr", "fixed_3atr"]

def calc_sl(entry: float, atr: float, side: str, method: str) -> float:
    mult = {"atr_1.5": 1.5, "atr_2.0": 2.0, "atr_2.5": 2.5}
    if method in mult:
        return entry - mult[method] * atr if side == "long" else entry + mult[method] * atr
    raise ValueError(f"Unknown SL: {method}")


# ══════════════════════════════════════════════════════════════
#  PnL 計算
# ══════════════════════════════════════════════════════════════

def _calc_partial_pnl(entry, exit_p, notional, pct, fee_rate, side):
    d = 1 if side == "long" else -1
    partial = notional * pct
    gross = d * (exit_p - entry) / entry * partial
    return gross - partial * fee_rate * 2

def _calc_pnl(pos, exit_p, fee_rate, tp1_pct):
    if pos["tp1_done"]:
        remaining = 1.0 - tp1_pct
        return pos["tp1_pnl"] + _calc_partial_pnl(
            pos["entry_price"], exit_p, pos["notional"], remaining, fee_rate, pos["side"])
    return _calc_partial_pnl(
        pos["entry_price"], exit_p, pos["notional"], 1.0, fee_rate, pos["side"])


# ══════════════════════════════════════════════════════════════
#  持倉管理（5m 逐根檢查）
# ══════════════════════════════════════════════════════════════

def check_position(pos, row, config):
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]
    side = pos["side"]

    if side == "long":
        # 止損
        if row["low"] <= pos["current_sl"]:
            return {"price": min(pos["current_sl"], row["open"]), "reason": "stop_loss"}

        # TP1
        if not pos["tp1_done"] and row["high"] >= pos["tp1_price"]:
            pos["tp1_pnl"] = _calc_partial_pnl(
                pos["entry_price"], pos["tp1_price"], pos["notional"], tp1_pct, fee_rate, "long")
            pos["tp1_done"] = True
            pos["phase"] = "trailing"
            pos["current_sl"] = pos["entry_price"]
            pos["trail_hi"] = max(pos["trail_hi"], row["high"])
            return None

        # Trailing
        if pos["phase"] == "trailing":
            pos["trail_hi"] = max(pos["trail_hi"], row["high"])
            em = pos["exit_method"]

            if em.startswith("atr_trail"):
                mult = float(em.split("_")[-1])
                trail_sl = pos["trail_hi"] - mult * pos["atr_1h"]
                if row["low"] <= trail_sl:
                    return {"price": max(trail_sl, row["open"]), "reason": em}

            elif em == "ema9_trail":
                if row["close"] < row["ema9"]:
                    return {"price": row["close"], "reason": "ema9_trail"}

            elif em.startswith("fixed_"):
                mult = float(em.replace("fixed_", "").replace("atr", ""))
                target = pos["entry_price"] + mult * pos["atr_1h"]
                if row["high"] >= target:
                    return {"price": target, "reason": em}

    else:  # short
        if row["high"] >= pos["current_sl"]:
            return {"price": max(pos["current_sl"], row["open"]), "reason": "stop_loss"}

        if not pos["tp1_done"] and row["low"] <= pos["tp1_price"]:
            pos["tp1_pnl"] = _calc_partial_pnl(
                pos["entry_price"], pos["tp1_price"], pos["notional"], tp1_pct, fee_rate, "short")
            pos["tp1_done"] = True
            pos["phase"] = "trailing"
            pos["current_sl"] = pos["entry_price"]
            pos["trail_lo"] = min(pos["trail_lo"], row["low"])
            return None

        if pos["phase"] == "trailing":
            pos["trail_lo"] = min(pos["trail_lo"], row["low"])
            em = pos["exit_method"]

            if em.startswith("atr_trail"):
                mult = float(em.split("_")[-1])
                trail_sl = pos["trail_lo"] + mult * pos["atr_1h"]
                if row["high"] >= trail_sl:
                    return {"price": min(trail_sl, row["open"]), "reason": em}

            elif em == "ema9_trail":
                if row["close"] > row["ema9"]:
                    return {"price": row["close"], "reason": "ema9_trail"}

            elif em.startswith("fixed_"):
                mult = float(em.replace("fixed_", "").replace("atr", ""))
                target = pos["entry_price"] - mult * pos["atr_1h"]
                if row["low"] <= target:
                    return {"price": target, "reason": em}

    return None


# ══════════════════════════════════════════════════════════════
#  回測引擎（1h 過濾 + 5m 進場）
# ══════════════════════════════════════════════════════════════

def run_backtest(
    df_5m: pd.DataFrame,
    side: str,              # "long" or "short"
    filter_name: str,
    entry_name: str,
    sl_method: str = "atr_2.0",
    exit_method: str = "atr_trail_1.5",
    config: dict = None,
) -> dict:
    config = config or RISK
    notional = config["margin_per_trade"] * config["leverage"]
    max_same = config["max_same_direction"]
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]

    filt_fn = FILTERS[filter_name]
    entry_fn = ENTRIES[entry_name]

    # 預轉為 list of dict（比 iloc 快 10 倍以上）
    records = df_5m.to_dict("records")
    idx = df_5m.index

    active: List[dict] = []
    trades = []
    cumulative_pnl = 0.0

    for i in range(50, len(records)):
        row = records[i]
        prev = records[i - 1]

        atr_1h = row.get("atr_1h")
        if atr_1h is None or atr_1h != atr_1h:  # NaN check
            continue

        # ── 1. 更新持倉 ──────────────────────────────────────
        closed = []
        for pos in active:
            result = check_position(pos, row, config)
            if result:
                pnl = _calc_pnl(pos, result["price"], fee_rate, tp1_pct)
                cumulative_pnl += pnl
                trades.append({
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "entry_time": pos["entry_time"],
                    "exit_price": result["price"],
                    "exit_time": idx[i],
                    "exit_reason": result["reason"],
                    "pnl": pnl,
                    "tp1_done": pos["tp1_done"],
                })
                closed.append(pos)
        for p in closed:
            active.remove(p)

        # ── 2. 新進場 ────────────────────────────────────────
        count = len(active)
        if count < max_same:
            if filt_fn(row, side):
                if entry_fn(row, prev, side):
                    ep = row["close"]
                    sl = calc_sl(ep, atr_1h, side, sl_method)

                    if side == "long":
                        tp1 = ep + config["tp1_atr_mult"] * atr_1h
                    else:
                        tp1 = ep - config["tp1_atr_mult"] * atr_1h

                    active.append({
                        "side": side, "entry_price": ep, "entry_time": idx[i],
                        "current_sl": sl, "tp1_price": tp1, "notional": notional,
                        "atr_1h": atr_1h, "exit_method": exit_method,
                        "tp1_done": False, "phase": "initial", "tp1_pnl": 0.0,
                        "trail_hi": ep, "trail_lo": ep,
                    })

    # 強制平倉
    last = records[-1]
    for pos in active:
        pnl = _calc_pnl(pos, last["close"], fee_rate, tp1_pct)
        trades.append({
            "side": pos["side"], "entry_price": pos["entry_price"],
            "entry_time": pos["entry_time"], "exit_price": last["close"],
            "exit_time": idx[-1], "exit_reason": "end_of_data",
            "pnl": pnl, "tp1_done": pos["tp1_done"],
        })

    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    return {"trades": trades_df, "metrics": _calc_metrics(trades_df)}


def _calc_metrics(trades_df):
    if trades_df.empty:
        return {"total_pnl": 0, "trades": 0, "win_rate": 0, "profit_factor": 0,
                "max_drawdown": 0, "avg_pnl": 0}
    pnl = trades_df["pnl"]
    n = len(pnl)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gp = wins.sum() if len(wins) else 0
    gl = abs(losses.sum()) if len(losses) else 1e-10
    pf = min(gp / gl, 99.99)
    eq = pnl.cumsum()
    max_dd = (eq - eq.cummax()).min()
    return {
        "total_pnl": round(pnl.sum(), 2),
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_pnl": round(pnl.mean(), 2),
    }


# ══════════════════════════════════════════════════════════════
#  Phase 1: Filter x Entry 篩選
# ══════════════════════════════════════════════════════════════

def run_phase1(df_5m, default_sl="atr_2.0", default_exit="atr_trail_1.5"):
    print("\n" + "=" * 70)
    print(f"  Phase 1: Filter x Entry Screen (SL={default_sl}, Exit={default_exit})")
    print("=" * 70)

    rows = []
    total = len(FILTERS) * len(ENTRIES) * 2
    done = 0

    for side in ["long", "short"]:
        for filt_name in FILTERS:
            for entry_name in ENTRIES:
                r = run_backtest(df_5m, side, filt_name, entry_name,
                                 sl_method=default_sl, exit_method=default_exit)
                m = r["metrics"]
                rows.append({"side": side, "filter": filt_name, "entry": entry_name, **m})
                done += 1
                if done % 10 == 0:
                    print(f"    [{done}/{total}] ...", flush=True)

    result = pd.DataFrame(rows)
    result = result.sort_values("profit_factor", ascending=False)

    for side in ["long", "short"]:
        subset = result[result["side"] == side].head(15)
        print(f"\n  >> Top 15 {side.upper()} combos:")
        print(f"  {'Filter':<12} {'Entry':<14} {'PnL':>10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'MaxDD':>10}")
        print("  " + "-" * 70)
        for _, r in subset.iterrows():
            print(f"  {r['filter']:<12} {r['entry']:<14} ${r['total_pnl']:>9,.2f} "
                  f"{r['trades']:>7} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>9,.2f}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 2: SL x Exit 優化
# ══════════════════════════════════════════════════════════════

def run_phase2(df_5m, phase1, top_n=5):
    print("\n" + "=" * 70)
    print(f"  Phase 2: SL x Exit Optimization (top {top_n} per side)")
    print("=" * 70)

    rows = []

    for side in ["long", "short"]:
        side_df = phase1[(phase1["side"] == side) & (phase1["trades"] >= 20)]
        # 每個 filter+entry 組合取最佳，去重
        top = side_df.sort_values("profit_factor", ascending=False).head(top_n)

        combos = [(r["filter"], r["entry"]) for _, r in top.iterrows()]
        print(f"\n  Top {side.upper()}: {combos}")

        for filt_name, entry_name in combos:
            for sl, ex in product(SL_METHODS, EXIT_METHODS):
                r = run_backtest(df_5m, side, filt_name, entry_name,
                                 sl_method=sl, exit_method=ex)
                m = r["metrics"]
                rows.append({"side": side, "filter": filt_name, "entry": entry_name,
                              "sl": sl, "exit": ex, **m})

    result = pd.DataFrame(rows)
    result = result.sort_values("profit_factor", ascending=False)

    for side in ["long", "short"]:
        subset = result[result["side"] == side].head(10)
        print(f"\n  >> Top 10 {side.upper()} combos:")
        print(f"  {'Filter':<12} {'Entry':<14} {'SL':<8} {'Exit':<14} "
              f"{'PnL':>10} {'Tr':>5} {'WR%':>6} {'PF':>6} {'MaxDD':>10}")
        print("  " + "-" * 90)
        for _, r in subset.iterrows():
            print(f"  {r['filter']:<12} {r['entry']:<14} {r['sl']:<8} {r['exit']:<14} "
                  f"${r['total_pnl']:>9,.2f} {r['trades']:>5} {r['win_rate']:>5.1f}% "
                  f"{r['profit_factor']:>5.2f} ${r['max_drawdown']:>9,.2f}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 3: Rolling Walk-Forward
# ══════════════════════════════════════════════════════════════

def run_phase3(df_5m, phase2, top_n=5):
    print("\n" + "=" * 70)
    print(f"  Phase 3: Rolling Walk-Forward")
    print("=" * 70)

    windows = [
        ("2025-09-01", "2025-12-31", "2026-01-01", "2026-01-31"),
        ("2025-10-01", "2026-01-31", "2026-02-01", "2026-02-28"),
        ("2025-11-01", "2026-02-28", "2026-03-01", "2026-03-28"),
    ]

    rows = []

    for side in ["long", "short"]:
        # 每個 filter+entry 組合取 PF 最高的一組 SL+Exit
        side_df = phase2[(phase2["side"] == side) & (phase2["trades"] >= 20)]
        best_per_combo = side_df.sort_values("profit_factor", ascending=False) \
            .drop_duplicates(["filter", "entry"]).head(top_n)

        for _, combo in best_per_combo.iterrows():
            oos_pnls, oos_pfs, oos_wrs, oos_dds = [], [], [], []
            is_pnl_total, is_pf_total = 0, 0

            for tr_s, tr_e, te_s, te_e in windows:
                train = df_5m[tr_s:tr_e]
                test = df_5m[te_s:te_e]
                if len(train) < 500 or len(test) < 200:
                    continue

                is_r = run_backtest(train, side, combo["filter"], combo["entry"],
                                     combo["sl"], combo["exit"])
                oos_r = run_backtest(test, side, combo["filter"], combo["entry"],
                                      combo["sl"], combo["exit"])

                is_m, oos_m = is_r["metrics"], oos_r["metrics"]
                is_pnl_total += is_m["total_pnl"]
                is_pf_total += is_m["profit_factor"]
                oos_pnls.append(oos_m["total_pnl"])
                oos_pfs.append(oos_m["profit_factor"])
                oos_wrs.append(oos_m["win_rate"])
                oos_dds.append(oos_m["max_drawdown"])

            if not oos_pnls:
                continue

            nw = len(oos_pnls)
            all_pos = all(p > 0 for p in oos_pnls)

            rows.append({
                "side": side, "filter": combo["filter"], "entry": combo["entry"],
                "sl": combo["sl"], "exit": combo["exit"],
                "is_pnl": round(is_pnl_total, 2),
                "is_pf": round(is_pf_total / nw, 2),
                "oos_pnl_avg": round(sum(oos_pnls) / nw, 2),
                "oos_pf_avg": round(sum(oos_pfs) / nw, 2),
                "oos_wr_avg": round(sum(oos_wrs) / nw, 1),
                "oos_max_dd": round(min(oos_dds), 2),
                "all_oos_positive": all_pos,
                "w1": round(oos_pnls[0], 2) if len(oos_pnls) > 0 else 0,
                "w2": round(oos_pnls[1], 2) if len(oos_pnls) > 1 else 0,
                "w3": round(oos_pnls[2], 2) if len(oos_pnls) > 2 else 0,
            })

    result = pd.DataFrame(rows)
    if result.empty:
        print("  No valid results.")
        return result

    result = result.sort_values("oos_pf_avg", ascending=False)

    print(f"\n  {'Side':<6} {'Filter':<12} {'Entry':<14} {'SL':<8} {'Exit':<14} "
          f"{'IS_PnL':>9} {'OOS_PnL':>9} {'OOS_PF':>7} {'WR':>6} {'DD':>9} {'All+':>5}")
    print("  " + "-" * 110)
    for _, r in result.iterrows():
        mk = " <<" if r["all_oos_positive"] and r["oos_pf_avg"] > 1.2 else ""
        print(f"  {r['side']:<6} {r['filter']:<12} {r['entry']:<14} {r['sl']:<8} {r['exit']:<14} "
              f"${r['is_pnl']:>8,.2f} ${r['oos_pnl_avg']:>8,.2f} {r['oos_pf_avg']:>6.2f} "
              f"{r['oos_wr_avg']:>5.1f}% ${r['oos_max_dd']:>8,.2f} "
              f"{'Y' if r['all_oos_positive'] else 'N':>4}{mk}")
        print(f"         W1=${r['w1']:>8,.2f}  W2=${r['w2']:>8,.2f}  W3=${r['w3']:>8,.2f}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 4: 多空合併
# ══════════════════════════════════════════════════════════════

def run_phase4(df_5m, phase3):
    print("\n" + "=" * 70)
    print("  Phase 4: Best Long + Short Combined")
    print("=" * 70)

    if phase3.empty:
        print("  No viable strategies.")
        return None

    windows = [
        ("2025-09-01", "2025-12-31", "2026-01-01", "2026-01-31"),
        ("2025-10-01", "2026-01-31", "2026-02-01", "2026-02-28"),
        ("2025-11-01", "2026-02-28", "2026-03-01", "2026-03-28"),
    ]

    best = {}
    for side in ["long", "short"]:
        cands = phase3[phase3["side"] == side]
        if cands.empty:
            print(f"  No viable {side.upper()} strategy.")
            continue
        top = cands.sort_values(["all_oos_positive", "oos_pf_avg"], ascending=[False, False]).iloc[0]
        best[side] = top
        print(f"\n  Best {side.upper()}: {top['filter']} + {top['entry']} + {top['sl']} + {top['exit']}")
        print(f"    OOS avg: ${top['oos_pnl_avg']:.2f}  PF: {top['oos_pf_avg']:.2f}  "
              f"WR: {top['oos_wr_avg']:.1f}%  DD: ${top['oos_max_dd']:.2f}")

    if not best:
        return None

    # Walk-Forward 合併
    print("\n  Combined Walk-Forward:")
    combined_oos = []
    combined_is = []

    for tr_s, tr_e, te_s, te_e in windows:
        train = df_5m[tr_s:tr_e]
        test = df_5m[te_s:te_e]
        is_pnl, oos_pnl = 0, 0

        for side, b in best.items():
            is_r = run_backtest(train, side, b["filter"], b["entry"], b["sl"], b["exit"])
            oos_r = run_backtest(test, side, b["filter"], b["entry"], b["sl"], b["exit"])
            is_pnl += is_r["metrics"]["total_pnl"]
            oos_pnl += oos_r["metrics"]["total_pnl"]

        combined_is.append(is_pnl)
        combined_oos.append(oos_pnl)
        print(f"    Window: IS ${is_pnl:>8,.2f}  OOS ${oos_pnl:>8,.2f}")

    # 全區間
    all_trades = pd.DataFrame()
    for side, b in best.items():
        r = run_backtest(df_5m, side, b["filter"], b["entry"], b["sl"], b["exit"])
        if not r["trades"].empty:
            all_trades = pd.concat([all_trades, r["trades"]])

    full_m = _calc_metrics(all_trades)

    avg_oos = sum(combined_oos) / len(combined_oos)
    all_pos = all(p > 0 for p in combined_oos)

    print(f"\n  ========== FINAL RESULTS ==========")
    print(f"  Full Period: PnL ${full_m['total_pnl']:,.2f}  "
          f"Trades {full_m['trades']}  WR {full_m['win_rate']}%  "
          f"PF {full_m['profit_factor']}  DD ${full_m['max_drawdown']:,.2f}")
    print(f"  OOS Avg:     ${avg_oos:,.2f}/month  All windows +: {'YES' if all_pos else 'NO'}")

    return {"metrics": full_m, "trades": all_trades, "best": best,
            "oos_avg": avg_oos, "all_positive": all_pos}


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  BTC Strategy Research v3 — Option C")
    print("  1h Trend Filter + 5m Entry")
    print("=" * 70)

    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 28, tzinfo=timezone.utc)

    print("\n  Loading data...")
    df_1h = fetch_klines("BTCUSDT", "1h", start, end)
    df_5m = fetch_klines("BTCUSDT", "5m", start, end)
    print(f"  1h: {len(df_1h)} bars  |  5m: {len(df_5m)} bars")

    print("  Computing indicators...")
    df_1h = compute_indicators_v2(df_1h)
    df_5m = compute_5m_indicators(df_5m)
    df_5m = spread_1h_to_5m(df_1h, df_5m)
    print(f"  5m columns: {len(df_5m.columns)}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Phase 1
    p1 = run_phase1(df_5m)
    p1.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v3_phase1.csv"), index=False)

    # Phase 2
    p2 = run_phase2(df_5m, p1, top_n=5)
    p2.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v3_phase2.csv"), index=False)

    # Phase 3
    p3 = run_phase3(df_5m, p2, top_n=5)
    if not p3.empty:
        p3.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v3_phase3.csv"), index=False)

    # Phase 4
    p4 = run_phase4(df_5m, p3)
    if p4 and not p4["trades"].empty:
        p4["trades"].to_csv(os.path.join(RESULTS_DIR, f"{ts}_v3_phase4_trades.csv"), index=False)

    print("\n" + "=" * 70)
    print("  Research v3 complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
