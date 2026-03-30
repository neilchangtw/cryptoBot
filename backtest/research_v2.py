"""
BTC 策略全面研究 v2
  - 10 種做多信號 × 10 種做空信號
  - 4 種止損 × 5 種出場
  - 四階段：信號篩選 → SL/Exit 優化 → Rolling Walk-Forward → 多空合併
  - 全部使用 ATR/百分比止損（方向保證正確）

執行：python backtest/research_v2.py
結果：backtest/results/research_v2_*.csv
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, List, Tuple
from itertools import product

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
from strategy_engine import compute_indicators_v2

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ── 風控設定 ──────────────────────────────────────────────────
RISK = {
    "margin_per_trade": 100,
    "leverage": 20,
    "max_same_direction": 3,
    "fee_rate": 0.0004,
    "tp1_pct": 0.10,
    "tp1_atr_mult": 1.0,
}

WARM_UP = 50  # 指標暖機期

# ══════════════════════════════════════════════════════════════
#  信號定義
# ══════════════════════════════════════════════════════════════

def _prev_ok(prev):
    return prev is not None and not prev.empty if isinstance(prev, pd.Series) else prev is not None


# ── 做多信號 ──────────────────────────────────────────────────
def sig_long_rsi30(row, prev):
    return row["rsi"] < 30

def sig_long_bb_lower(row, prev):
    return row["close"] < row["bb_lower"]

def sig_long_stoch_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["stoch_k"] < prev["stoch_d"] and row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 25

def sig_long_macd_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["macd_line"] < prev["macd_signal"] and row["macd_line"] > row["macd_signal"]

def sig_long_donchian(row, prev):
    if pd.isna(row.get("donchian_high")): return False
    return row["close"] > row["donchian_high"]

def sig_long_ema_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["ema9"] <= prev["ema21"] and row["ema9"] > row["ema21"]

def sig_long_hammer(row, prev):
    body = abs(row["close"] - row["open"])
    lower_wick = min(row["open"], row["close"]) - row["low"]
    total_range = row["high"] - row["low"]
    if total_range < 1e-10: return False
    return lower_wick > 2 * max(body, 1e-10) and row["close"] > row["open"]

def sig_long_volume_spike(row, prev):
    return row["volume"] > 2 * row["vol_sma20"] and row["close"] > row["open"]

def sig_long_supertrend(row, prev):
    if not _prev_ok(prev): return False
    return prev["supertrend_dir"] <= 0 and row["supertrend_dir"] == 1

def sig_long_bb_squeeze(row, prev):
    return row["bb_width"] <= row["bb_width_min20"] * 1.05 and row["close"] > row["bb_upper"]

# ── 複合做多信號 ──────────────────────────────────────────────
def sig_long_rsi30_vol(row, prev):
    """RSI<35 + 量能放大 + 陽線"""
    return row["rsi"] < 35 and row["volume"] > 1.5 * row["vol_sma20"] and row["close"] > row["open"]

def sig_long_ema_macd(row, prev):
    """EMA9>EMA21 + MACD>Signal（趨勢確認）"""
    if not _prev_ok(prev): return False
    ema_up = row["ema9"] > row["ema21"]
    macd_cross = prev["macd_line"] < prev["macd_signal"] and row["macd_line"] > row["macd_signal"]
    return ema_up and macd_cross

def sig_long_stoch_bb(row, prev):
    """Stoch 超賣回升 + 收在 BB 下半部"""
    if not _prev_ok(prev): return False
    stoch_cross = prev["stoch_k"] < prev["stoch_d"] and row["stoch_k"] > row["stoch_d"] and row["stoch_k"] < 30
    bb_lower_half = row["close"] < row["bb_mid"]
    return stoch_cross and bb_lower_half

def sig_long_momentum(row, prev):
    """價格突破 + 量能確認：close > EMA21 且量 > 1.5x avg"""
    if not _prev_ok(prev): return False
    return prev["close"] <= prev["ema21"] and row["close"] > row["ema21"] and row["volume"] > 1.5 * row["vol_sma20"]


# ── 做空信號 ──────────────────────────────────────────────────
def sig_short_rsi70(row, prev):
    return row["rsi"] > 70

def sig_short_bb_upper(row, prev):
    return row["close"] > row["bb_upper"]

def sig_short_stoch_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["stoch_k"] > prev["stoch_d"] and row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 75

def sig_short_macd_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["macd_line"] > prev["macd_signal"] and row["macd_line"] < row["macd_signal"]

def sig_short_donchian(row, prev):
    if pd.isna(row.get("donchian_low")): return False
    return row["close"] < row["donchian_low"]

def sig_short_ema_cross(row, prev):
    if not _prev_ok(prev): return False
    return prev["ema9"] >= prev["ema21"] and row["ema9"] < row["ema21"]

def sig_short_shooting_star(row, prev):
    body = abs(row["close"] - row["open"])
    upper_wick = row["high"] - max(row["open"], row["close"])
    total_range = row["high"] - row["low"]
    if total_range < 1e-10: return False
    return upper_wick > 2 * max(body, 1e-10) and row["close"] < row["open"]

def sig_short_volume_spike(row, prev):
    return row["volume"] > 2 * row["vol_sma20"] and row["close"] < row["open"]

def sig_short_supertrend(row, prev):
    if not _prev_ok(prev): return False
    return prev["supertrend_dir"] >= 0 and row["supertrend_dir"] == -1

def sig_short_bb_squeeze(row, prev):
    return row["bb_width"] <= row["bb_width_min20"] * 1.05 and row["close"] < row["bb_lower"]

# ── 複合做空信號 ──────────────────────────────────────────────
def sig_short_rsi70_vol(row, prev):
    """RSI>65 + 量能放大 + 陰線"""
    return row["rsi"] > 65 and row["volume"] > 1.5 * row["vol_sma20"] and row["close"] < row["open"]

def sig_short_ema_macd(row, prev):
    """EMA9<EMA21 + MACD下穿Signal"""
    if not _prev_ok(prev): return False
    ema_dn = row["ema9"] < row["ema21"]
    macd_cross = prev["macd_line"] > prev["macd_signal"] and row["macd_line"] < row["macd_signal"]
    return ema_dn and macd_cross

def sig_short_stoch_bb(row, prev):
    """Stoch 超買回落 + 收在 BB 上半部"""
    if not _prev_ok(prev): return False
    stoch_cross = prev["stoch_k"] > prev["stoch_d"] and row["stoch_k"] < row["stoch_d"] and row["stoch_k"] > 70
    bb_upper_half = row["close"] > row["bb_mid"]
    return stoch_cross and bb_upper_half

def sig_short_momentum(row, prev):
    """價格跌破 EMA21 + 量能確認"""
    if not _prev_ok(prev): return False
    return prev["close"] >= prev["ema21"] and row["close"] < row["ema21"] and row["volume"] > 1.5 * row["vol_sma20"]


# ── 信號註冊表 ────────────────────────────────────────────────
LONG_SIGNALS: Dict[str, Callable] = {
    "rsi_30":           sig_long_rsi30,
    "bb_lower":         sig_long_bb_lower,
    "stoch_cross_up":   sig_long_stoch_cross,
    "macd_cross_up":    sig_long_macd_cross,
    "donchian_high":    sig_long_donchian,
    "ema_cross_up":     sig_long_ema_cross,
    "hammer":           sig_long_hammer,
    "vol_spike_up":     sig_long_volume_spike,
    "supertrend_up":    sig_long_supertrend,
    "bb_squeeze_up":    sig_long_bb_squeeze,
    "rsi30_vol":        sig_long_rsi30_vol,
    "ema_macd_up":      sig_long_ema_macd,
    "stoch_bb_up":      sig_long_stoch_bb,
    "momentum_up":      sig_long_momentum,
}

SHORT_SIGNALS: Dict[str, Callable] = {
    "rsi_70":           sig_short_rsi70,
    "bb_upper":         sig_short_bb_upper,
    "stoch_cross_dn":   sig_short_stoch_cross,
    "macd_cross_dn":    sig_short_macd_cross,
    "donchian_low":     sig_short_donchian,
    "ema_cross_dn":     sig_short_ema_cross,
    "shooting_star":    sig_short_shooting_star,
    "vol_spike_dn":     sig_short_volume_spike,
    "supertrend_dn":    sig_short_supertrend,
    "bb_squeeze_dn":    sig_short_bb_squeeze,
    "rsi70_vol":        sig_short_rsi70_vol,
    "ema_macd_dn":      sig_short_ema_macd,
    "stoch_bb_dn":      sig_short_stoch_bb,
    "momentum_dn":      sig_short_momentum,
}


# ══════════════════════════════════════════════════════════════
#  止損 & 出場
# ══════════════════════════════════════════════════════════════

def calc_sl(entry: float, atr: float, side: str, method: str) -> float:
    mult = {"atr_1.5": 1.5, "atr_2.0": 2.0, "atr_2.5": 2.5}
    if method in mult:
        return entry - mult[method] * atr if side == "long" else entry + mult[method] * atr
    if method == "pct_1.5":
        return entry * 0.985 if side == "long" else entry * 1.015
    raise ValueError(f"Unknown SL method: {method}")

SL_METHODS = ["atr_1.5", "atr_2.0", "atr_2.5", "pct_1.5"]
EXIT_METHODS = ["atr_trail_1.5", "atr_trail_2.5", "ema9_trail", "fixed_2atr", "fixed_3atr"]


# ══════════════════════════════════════════════════════════════
#  持倉資料結構
# ══════════════════════════════════════════════════════════════

@dataclass
class Position:
    side: str
    entry_price: float
    entry_bar: int
    stop_loss: float
    tp1_price: float
    notional: float
    atr_at_entry: float
    exit_method: str
    tp1_done: bool = False
    phase: str = "initial"
    trail_hi: float = 0.0
    trail_lo: float = 0.0
    tp1_pnl: float = 0.0


# ══════════════════════════════════════════════════════════════
#  PnL 計算
# ══════════════════════════════════════════════════════════════

def _calc_partial_pnl(pos: Position, exit_price: float, fee_rate: float, pct: float) -> float:
    d = 1 if pos.side == "long" else -1
    partial_notional = pos.notional * pct
    gross = d * (exit_price - pos.entry_price) / pos.entry_price * partial_notional
    fee = partial_notional * fee_rate * 2
    return gross - fee

def _calc_pnl(pos: Position, exit_price: float, fee_rate: float, tp1_pct: float) -> float:
    if pos.tp1_done:
        remaining = 1.0 - tp1_pct
        return pos.tp1_pnl + _calc_partial_pnl(pos, exit_price, fee_rate, remaining)
    return _calc_partial_pnl(pos, exit_price, fee_rate, 1.0)


# ══════════════════════════════════════════════════════════════
#  持倉管理
# ══════════════════════════════════════════════════════════════

def _check_position(pos: Position, row, config: dict) -> Optional[dict]:
    """逐根 K 線檢查持倉狀態，回傳出場資訊或 None"""
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]

    if pos.side == "long":
        # 止損
        if row["low"] <= pos.stop_loss:
            ep = min(pos.stop_loss, row["open"])
            return {"price": ep, "reason": "stop_loss"}

        # TP1
        if not pos.tp1_done and row["high"] >= pos.tp1_price:
            pos.tp1_pnl = _calc_partial_pnl(pos, pos.tp1_price, fee_rate, tp1_pct)
            pos.tp1_done = True
            pos.phase = "trailing"
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_hi = max(pos.trail_hi, row["high"])
            return None

        # Trailing
        if pos.phase == "trailing":
            pos.trail_hi = max(pos.trail_hi, row["high"])
            exit_method = pos.exit_method

            if exit_method == "atr_trail_1.5":
                trail_sl = pos.trail_hi - 1.5 * pos.atr_at_entry
                if row["low"] <= trail_sl:
                    return {"price": max(trail_sl, row["open"]), "reason": "atr_trail"}

            elif exit_method == "atr_trail_2.5":
                trail_sl = pos.trail_hi - 2.5 * pos.atr_at_entry
                if row["low"] <= trail_sl:
                    return {"price": max(trail_sl, row["open"]), "reason": "atr_trail"}

            elif exit_method == "ema9_trail":
                if row["close"] < row["ema9"]:
                    return {"price": row["close"], "reason": "ema9_trail"}

            elif exit_method == "fixed_2atr":
                target = pos.entry_price + 2.0 * pos.atr_at_entry
                if row["high"] >= target:
                    return {"price": target, "reason": "fixed_2atr"}

            elif exit_method == "fixed_3atr":
                target = pos.entry_price + 3.0 * pos.atr_at_entry
                if row["high"] >= target:
                    return {"price": target, "reason": "fixed_3atr"}

    else:  # short
        # 止損
        if row["high"] >= pos.stop_loss:
            ep = max(pos.stop_loss, row["open"])
            return {"price": ep, "reason": "stop_loss"}

        # TP1
        if not pos.tp1_done and row["low"] <= pos.tp1_price:
            pos.tp1_pnl = _calc_partial_pnl(pos, pos.tp1_price, fee_rate, tp1_pct)
            pos.tp1_done = True
            pos.phase = "trailing"
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_lo = min(pos.trail_lo, row["low"])
            return None

        # Trailing
        if pos.phase == "trailing":
            pos.trail_lo = min(pos.trail_lo, row["low"])
            exit_method = pos.exit_method

            if exit_method == "atr_trail_1.5":
                trail_sl = pos.trail_lo + 1.5 * pos.atr_at_entry
                if row["high"] >= trail_sl:
                    return {"price": min(trail_sl, row["open"]), "reason": "atr_trail"}

            elif exit_method == "atr_trail_2.5":
                trail_sl = pos.trail_lo + 2.5 * pos.atr_at_entry
                if row["high"] >= trail_sl:
                    return {"price": min(trail_sl, row["open"]), "reason": "atr_trail"}

            elif exit_method == "ema9_trail":
                if row["close"] > row["ema9"]:
                    return {"price": row["close"], "reason": "ema9_trail"}

            elif exit_method == "fixed_2atr":
                target = pos.entry_price - 2.0 * pos.atr_at_entry
                if row["low"] <= target:
                    return {"price": target, "reason": "fixed_2atr"}

            elif exit_method == "fixed_3atr":
                target = pos.entry_price - 3.0 * pos.atr_at_entry
                if row["low"] <= target:
                    return {"price": target, "reason": "fixed_3atr"}

    return None


# ══════════════════════════════════════════════════════════════
#  回測引擎
# ══════════════════════════════════════════════════════════════

def run_backtest(
    df: pd.DataFrame,
    long_signal: Optional[str] = None,
    short_signal: Optional[str] = None,
    sl_method: str = "atr_2.0",
    exit_method: str = "atr_trail_1.5",
    config: dict = None,
) -> dict:
    """
    執行 1h 回測。long_signal/short_signal 為 None 時該方向不交易。
    """
    config = config or RISK
    notional = config["margin_per_trade"] * config["leverage"]
    max_same = config["max_same_direction"]
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]

    long_fn = LONG_SIGNALS.get(long_signal) if long_signal else None
    short_fn = SHORT_SIGNALS.get(short_signal) if short_signal else None

    positions: List[Position] = []
    trades = []
    cumulative_pnl = 0.0
    equity = []

    for i in range(WARM_UP, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1] if i > 0 else None

        # 跳過指標 NaN
        if pd.isna(row.get("atr")) or pd.isna(row.get("rsi")):
            equity.append(cumulative_pnl)
            continue

        # ── 1. 更新持倉 ──────────────────────────────────────
        closed_this_bar = []
        for pos in positions:
            result = _check_position(pos, row, config)
            if result:
                pnl = _calc_pnl(pos, result["price"], fee_rate, tp1_pct)
                cumulative_pnl += pnl
                trades.append({
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "entry_time": df.index[pos.entry_bar],
                    "exit_price": result["price"],
                    "exit_time": df.index[i],
                    "exit_reason": result["reason"],
                    "pnl": pnl,
                    "tp1_done": pos.tp1_done,
                    "duration_bars": i - pos.entry_bar,
                })
                closed_this_bar.append(pos)

        for pos in closed_this_bar:
            positions.remove(pos)

        # ── 2. 計算目前多空數量 ───────────────────────────────
        long_count = sum(1 for p in positions if p.side == "long")
        short_count = sum(1 for p in positions if p.side == "short")

        # ── 3. 做多 ──────────────────────────────────────────
        if long_fn and long_count < max_same and long_fn(row, prev):
            sl = calc_sl(row["close"], row["atr"], "long", sl_method)
            tp1 = row["close"] + config["tp1_atr_mult"] * row["atr"]
            positions.append(Position(
                side="long", entry_price=row["close"], entry_bar=i,
                stop_loss=sl, tp1_price=tp1, notional=notional,
                atr_at_entry=row["atr"], exit_method=exit_method,
                trail_hi=row["close"], trail_lo=row["close"],
            ))

        # ── 4. 做空 ──────────────────────────────────────────
        if short_fn and short_count < max_same and short_fn(row, prev):
            sl = calc_sl(row["close"], row["atr"], "short", sl_method)
            tp1 = row["close"] - config["tp1_atr_mult"] * row["atr"]
            positions.append(Position(
                side="short", entry_price=row["close"], entry_bar=i,
                stop_loss=sl, tp1_price=tp1, notional=notional,
                atr_at_entry=row["atr"], exit_method=exit_method,
                trail_hi=row["close"], trail_lo=row["close"],
            ))

        equity.append(cumulative_pnl)

    # 強制平倉
    last_row = df.iloc[-1]
    for pos in positions:
        pnl = _calc_pnl(pos, last_row["close"], fee_rate, tp1_pct)
        cumulative_pnl += pnl
        trades.append({
            "side": pos.side,
            "entry_price": pos.entry_price,
            "entry_time": df.index[pos.entry_bar],
            "exit_price": last_row["close"],
            "exit_time": df.index[-1],
            "exit_reason": "end_of_data",
            "pnl": pnl,
            "tp1_done": pos.tp1_done,
            "duration_bars": len(df) - 1 - pos.entry_bar,
        })

    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    return {"trades": trades_df, "metrics": _calc_metrics(trades_df)}


def _calc_metrics(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"total_pnl": 0, "trades": 0, "win_rate": 0, "profit_factor": 0,
                "max_drawdown": 0, "avg_pnl": 0, "long_trades": 0, "short_trades": 0,
                "long_pnl": 0, "short_pnl": 0}
    pnl = trades_df["pnl"]
    n = len(pnl)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gp = wins.sum() if len(wins) else 0
    gl = abs(losses.sum()) if len(losses) else 1e-10
    pf_raw = gp / gl
    pf = min(pf_raw, 99.99)  # cap to avoid meaningless infinity
    eq = pnl.cumsum()
    max_dd = (eq - eq.cummax()).min()

    longs = trades_df[trades_df["side"] == "long"]
    shorts = trades_df[trades_df["side"] == "short"]

    return {
        "total_pnl": round(pnl.sum(), 2),
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_pnl": round(pnl.mean(), 2),
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_pnl": round(longs["pnl"].sum(), 2) if len(longs) else 0,
        "short_pnl": round(shorts["pnl"].sum(), 2) if len(shorts) else 0,
    }


# ══════════════════════════════════════════════════════════════
#  Phase 1: 信號篩選
# ══════════════════════════════════════════════════════════════

def run_phase1(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 70)
    print("  Phase 1: Signal Screening (SL=atr_2.0, Exit=atr_trail_1.5)")
    print("=" * 70)

    rows = []

    # 做多信號
    for name in LONG_SIGNALS:
        r = run_backtest(df, long_signal=name, short_signal=None)
        m = r["metrics"]
        rows.append({"side": "long", "signal": name, **m})

    # 做空信號
    for name in SHORT_SIGNALS:
        r = run_backtest(df, long_signal=None, short_signal=name)
        m = r["metrics"]
        rows.append({"side": "short", "signal": name, **m})

    result = pd.DataFrame(rows)
    result = result.sort_values("profit_factor", ascending=False)

    # 印出結果
    print(f"\n  {'Side':<6} {'Signal':<20} {'PnL':>10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'MaxDD':>10}")
    print("  " + "-" * 65)
    for _, r in result.iterrows():
        print(f"  {r['side']:<6} {r['signal']:<20} ${r['total_pnl']:>9,.2f} {r['trades']:>7} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>9,.2f}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 2: SL x Exit 優化
# ══════════════════════════════════════════════════════════════

def run_phase2(df: pd.DataFrame, phase1: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    print("\n" + "=" * 70)
    print(f"  Phase 2: SL x Exit Optimization (top {top_n} per side)")
    print("=" * 70)

    # 篩選 Phase1 前 N 名（PF > 0.7 且 trades > 10）
    valid = phase1[(phase1["profit_factor"] > 0.7) & (phase1["trades"] > 10)]
    top_long = valid[valid["side"] == "long"].head(top_n)["signal"].tolist()
    top_short = valid[valid["side"] == "short"].head(top_n)["signal"].tolist()

    print(f"\n  Top long signals: {top_long}")
    print(f"  Top short signals: {top_short}")

    rows = []

    for sig_name in top_long:
        for sl, ex in product(SL_METHODS, EXIT_METHODS):
            r = run_backtest(df, long_signal=sig_name, short_signal=None,
                             sl_method=sl, exit_method=ex)
            m = r["metrics"]
            rows.append({"side": "long", "signal": sig_name, "sl": sl, "exit": ex, **m})

    for sig_name in top_short:
        for sl, ex in product(SL_METHODS, EXIT_METHODS):
            r = run_backtest(df, long_signal=None, short_signal=sig_name,
                             sl_method=sl, exit_method=ex)
            m = r["metrics"]
            rows.append({"side": "short", "signal": sig_name, "sl": sl, "exit": ex, **m})

    result = pd.DataFrame(rows)
    result = result.sort_values("profit_factor", ascending=False)

    # 印出各方向 Top 10
    for side in ["long", "short"]:
        subset = result[result["side"] == side].head(10)
        print(f"\n  >> Top 10 {side.upper()} combos:")
        print(f"  {'Signal':<20} {'SL':<10} {'Exit':<16} {'PnL':>10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'MaxDD':>10}")
        print("  " + "-" * 85)
        for _, r in subset.iterrows():
            print(f"  {r['signal']:<20} {r['sl']:<10} {r['exit']:<16} ${r['total_pnl']:>9,.2f} "
                  f"{r['trades']:>7} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>9,.2f}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 3: Rolling Walk-Forward
# ══════════════════════════════════════════════════════════════

def run_phase3(df: pd.DataFrame, phase2: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    print("\n" + "=" * 70)
    print(f"  Phase 3: Rolling Walk-Forward (top {top_n} per side)")
    print("=" * 70)

    # 取各方向每個信號的最佳組合（按 PF 排），最多 top_n 個信號
    rows = []

    for side in ["long", "short"]:
        side_df = phase2[(phase2["side"] == side) & (phase2["trades"] >= 20)]
        # 每個信號取 PF 最高的一組
        best_per_sig = side_df.sort_values("profit_factor", ascending=False).drop_duplicates("signal")
        subset = best_per_sig.head(top_n)

        for _, combo in subset.iterrows():
            sig = combo["signal"]
            sl = combo["sl"]
            ex = combo["exit"]

            # 3 個滾動窗口
            windows = [
                ("2025-09-01", "2025-12-31", "2026-01-01", "2026-01-31"),
                ("2025-10-01", "2026-01-31", "2026-02-01", "2026-02-28"),
                ("2025-11-01", "2026-02-28", "2026-03-01", "2026-03-28"),
            ]

            oos_pnls = []
            oos_pfs = []
            oos_wrs = []
            oos_dds = []
            is_pnl_total = 0
            is_pf_total = 0

            for train_start, train_end, test_start, test_end in windows:
                train_df = df[train_start:train_end]
                test_df = df[test_start:test_end]

                if len(train_df) < 100 or len(test_df) < 50:
                    continue

                ls = sig if side == "long" else None
                ss = sig if side == "short" else None

                is_result = run_backtest(train_df, long_signal=ls, short_signal=ss,
                                         sl_method=sl, exit_method=ex)
                oos_result = run_backtest(test_df, long_signal=ls, short_signal=ss,
                                          sl_method=sl, exit_method=ex)

                is_m = is_result["metrics"]
                oos_m = oos_result["metrics"]

                is_pnl_total += is_m["total_pnl"]
                is_pf_total += is_m["profit_factor"]
                oos_pnls.append(oos_m["total_pnl"])
                oos_pfs.append(oos_m["profit_factor"])
                oos_wrs.append(oos_m["win_rate"])
                oos_dds.append(oos_m["max_drawdown"])

            if not oos_pnls:
                continue

            n_win = len(windows)
            avg_oos_pnl = sum(oos_pnls) / n_win
            avg_oos_pf = sum(oos_pfs) / n_win
            avg_oos_wr = sum(oos_wrs) / n_win
            worst_oos_dd = min(oos_dds)
            all_positive = all(p > 0 for p in oos_pnls)
            avg_is_pf = is_pf_total / n_win

            degradation = (avg_oos_pf - avg_is_pf) / max(avg_is_pf, 0.01) * 100

            rows.append({
                "side": side, "signal": sig, "sl": sl, "exit": ex,
                "is_pnl": round(is_pnl_total, 2),
                "is_pf": round(avg_is_pf, 2),
                "oos_pnl_avg": round(avg_oos_pnl, 2),
                "oos_pf_avg": round(avg_oos_pf, 2),
                "oos_wr_avg": round(avg_oos_wr, 1),
                "oos_max_dd": round(worst_oos_dd, 2),
                "all_oos_positive": all_positive,
                "degradation_pct": round(degradation, 1),
                "oos_pnl_w1": round(oos_pnls[0], 2) if len(oos_pnls) > 0 else 0,
                "oos_pnl_w2": round(oos_pnls[1], 2) if len(oos_pnls) > 1 else 0,
                "oos_pnl_w3": round(oos_pnls[2], 2) if len(oos_pnls) > 2 else 0,
            })

    result = pd.DataFrame(rows)
    if result.empty:
        print("\n  No valid combos for Walk-Forward.")
        return result

    result = result.sort_values("oos_pf_avg", ascending=False)

    print(f"\n  {'Side':<6} {'Signal':<18} {'SL':<10} {'Exit':<14} "
          f"{'IS_PnL':>9} {'OOS_PnL':>9} {'OOS_PF':>7} {'OOS_WR':>7} {'OOS_DD':>9} {'All+':>5} {'Deg%':>6}")
    print("  " + "-" * 110)
    for _, r in result.iterrows():
        marker = " <<" if r["all_oos_positive"] and r["oos_pf_avg"] > 1.2 else ""
        print(f"  {r['side']:<6} {r['signal']:<18} {r['sl']:<10} {r['exit']:<14} "
              f"${r['is_pnl']:>8,.2f} ${r['oos_pnl_avg']:>8,.2f} {r['oos_pf_avg']:>6.2f} "
              f"{r['oos_wr_avg']:>5.1f}% ${r['oos_max_dd']:>8,.2f} "
              f"{'Y' if r['all_oos_positive'] else 'N':>4} {r['degradation_pct']:>5.1f}%{marker}")

    return result


# ══════════════════════════════════════════════════════════════
#  Phase 4: 多空合併
# ══════════════════════════════════════════════════════════════

def run_phase4(df: pd.DataFrame, phase3: pd.DataFrame) -> Optional[dict]:
    print("\n" + "=" * 70)
    print("  Phase 4: Best Long + Short Combined")
    print("=" * 70)

    if phase3.empty:
        print("  No viable combos from Phase 3.")
        return None

    # 挑各方向最佳（優先 all_oos_positive，再看 oos_pf_avg）
    best_long = None
    best_short = None

    for side, label in [("long", "LONG"), ("short", "SHORT")]:
        candidates = phase3[phase3["side"] == side]
        if candidates.empty:
            print(f"  No viable {label} strategy found.")
            continue
        # 優先全窗口正，再按 OOS PF 排
        positive_first = candidates.sort_values(
            ["all_oos_positive", "oos_pf_avg"], ascending=[False, False]
        )
        best = positive_first.iloc[0]
        print(f"\n  Best {label}: {best['signal']} + {best['sl']} + {best['exit']}")
        print(f"    OOS PnL avg: ${best['oos_pnl_avg']:.2f}  PF: {best['oos_pf_avg']:.2f}  "
              f"WR: {best['oos_wr_avg']:.1f}%  DD: ${best['oos_max_dd']:.2f}")

        if side == "long":
            best_long = best
        else:
            best_short = best

    if best_long is None and best_short is None:
        print("\n  No strategies viable. Consider different approach.")
        return None

    # 合併回測
    ls = best_long["signal"] if best_long is not None else None
    ss = best_short["signal"] if best_short is not None else None

    # 使用做多的 SL/Exit（如果有），做空用自己的
    # 因為 run_backtest 只用一組 SL/Exit，這裡需要做兩次分開跑再合併
    print("\n  Running combined Walk-Forward...")

    windows = [
        ("2025-09-01", "2025-12-31", "2026-01-01", "2026-01-31"),
        ("2025-10-01", "2026-01-31", "2026-02-01", "2026-02-28"),
        ("2025-11-01", "2026-02-28", "2026-03-01", "2026-03-28"),
    ]

    combined_oos_pnls = []
    combined_is_pnls = []

    for train_start, train_end, test_start, test_end in windows:
        train_df = df[train_start:train_end]
        test_df = df[test_start:test_end]
        if len(train_df) < 100 or len(test_df) < 50:
            continue

        is_pnl = 0
        oos_pnl = 0

        if best_long is not None:
            is_r = run_backtest(train_df, long_signal=ls, short_signal=None,
                                sl_method=best_long["sl"], exit_method=best_long["exit"])
            oos_r = run_backtest(test_df, long_signal=ls, short_signal=None,
                                 sl_method=best_long["sl"], exit_method=best_long["exit"])
            is_pnl += is_r["metrics"]["total_pnl"]
            oos_pnl += oos_r["metrics"]["total_pnl"]

        if best_short is not None:
            is_r = run_backtest(train_df, long_signal=None, short_signal=ss,
                                sl_method=best_short["sl"], exit_method=best_short["exit"])
            oos_r = run_backtest(test_df, long_signal=None, short_signal=ss,
                                 sl_method=best_short["sl"], exit_method=best_short["exit"])
            is_pnl += is_r["metrics"]["total_pnl"]
            oos_pnl += oos_r["metrics"]["total_pnl"]

        combined_is_pnls.append(is_pnl)
        combined_oos_pnls.append(oos_pnl)

    # 全區間合併
    full_long_result = None
    full_short_result = None
    total_trades = pd.DataFrame()

    if best_long is not None:
        full_long_result = run_backtest(df, long_signal=ls, short_signal=None,
                                        sl_method=best_long["sl"], exit_method=best_long["exit"])
        if not full_long_result["trades"].empty:
            total_trades = pd.concat([total_trades, full_long_result["trades"]])

    if best_short is not None:
        full_short_result = run_backtest(df, long_signal=None, short_signal=ss,
                                         sl_method=best_short["sl"], exit_method=best_short["exit"])
        if not full_short_result["trades"].empty:
            total_trades = pd.concat([total_trades, full_short_result["trades"]])

    combined_metrics = _calc_metrics(total_trades)

    print(f"\n  ========== FINAL COMBINED RESULTS ==========")
    print(f"  Full Period:")
    print(f"    Total PnL:    ${combined_metrics['total_pnl']:,.2f}")
    print(f"    Long PnL:     ${combined_metrics['long_pnl']:,.2f}  ({combined_metrics['long_trades']} trades)")
    print(f"    Short PnL:    ${combined_metrics['short_pnl']:,.2f}  ({combined_metrics['short_trades']} trades)")
    print(f"    Win Rate:     {combined_metrics['win_rate']}%")
    print(f"    Profit Factor:{combined_metrics['profit_factor']}")
    print(f"    Max Drawdown: ${combined_metrics['max_drawdown']:,.2f}")

    if combined_oos_pnls:
        print(f"\n  Walk-Forward OOS:")
        for i, (is_p, oos_p) in enumerate(zip(combined_is_pnls, combined_oos_pnls)):
            print(f"    Window {i+1}: IS ${is_p:,.2f}  OOS ${oos_p:,.2f}")
        avg_oos = sum(combined_oos_pnls) / len(combined_oos_pnls)
        all_pos = all(p > 0 for p in combined_oos_pnls)
        print(f"    Avg OOS PnL:  ${avg_oos:,.2f}")
        print(f"    All windows +: {'YES' if all_pos else 'NO'}")

    return {
        "metrics": combined_metrics,
        "trades": total_trades,
        "best_long": dict(best_long) if best_long is not None else None,
        "best_short": dict(best_short) if best_short is not None else None,
    }


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  BTC Strategy Research v2")
    print("  10 long + 10 short signals x 4 SL x 5 exits")
    print("  All ATR/pct-based SL (direction guaranteed)")
    print("=" * 70)

    # 載入資料
    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 28, tzinfo=timezone.utc)
    df_raw = fetch_klines("BTCUSDT", "1h", start, end)
    print(f"\n  Data: {len(df_raw)} bars, {df_raw.index[0]} ~ {df_raw.index[-1]}")

    df = compute_indicators_v2(df_raw)
    print(f"  Indicators computed. Columns: {len(df.columns)}")

    # Phase 1
    p1 = run_phase1(df)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    p1.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v2_phase1.csv"), index=False)

    # Phase 2
    p2 = run_phase2(df, p1, top_n=5)
    p2.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v2_phase2.csv"), index=False)

    # Phase 3
    p3 = run_phase3(df, p2, top_n=5)
    if not p3.empty:
        p3.to_csv(os.path.join(RESULTS_DIR, f"{ts}_v2_phase3.csv"), index=False)

    # Phase 4
    p4 = run_phase4(df, p3)
    if p4 and not p4["trades"].empty:
        p4["trades"].to_csv(os.path.join(RESULTS_DIR, f"{ts}_v2_phase4_trades.csv"), index=False)

    print("\n" + "=" * 70)
    print("  Research v2 complete. Results saved to backtest/results/")
    print("=" * 70)


if __name__ == "__main__":
    main()
