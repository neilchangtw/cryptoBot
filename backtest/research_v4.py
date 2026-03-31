"""
v4 策略研究 — 多方案對比回測

站在實戰交易員角度：
  - 信號 bar 決策，下根 bar open 進場（含滑價）
  - SL 方向驗證 + fallback
  - 無 look-ahead（Swing shift、1h EMA 用已完成 bar）
  - 獨立持倉追蹤（同方向最多 3 單）

測試 8 個策略變體，涵蓋：
  A. Baseline（現行 v4）
  B. 收緊 RSI (25/75)
  C. 固定 ATR SL（取代結構止損）
  D. 加大 TP1 + R:R
  E. 15m 時間框架
  F. 15m 最佳化
  G. 加 1h 趨勢過濾
  H. 綜合最佳化（趨勢+收緊+固定SL+大TP1+冷卻）

執行: python backtest/research_v4.py
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ══════════════════════════════════════════════════════════════
#  指標計算
# ══════════════════════════════════════════════════════════════

SWING_W = 5

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # ATR(14)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # RSI(14) Wilder's
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, min_periods=14).mean()
    l_s = (-d.clip(upper=0)).ewm(alpha=1/14, min_periods=14).mean()
    df["rsi"] = 100 - 100 / (1 + g / l_s)

    # BB(20,2)
    df["bb_mid"] = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    # ATR Percentile(100)
    df["atr_pctile"] = df["atr"].rolling(100).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50, raw=False
    )

    # Swing H/L（shift(w) 避免 look-ahead）
    w = SWING_W
    raw_sh = pd.Series(np.nan, index=df.index)
    raw_sl = pd.Series(np.nan, index=df.index)
    highs, lows = df["high"].values, df["low"].values
    for i in range(w, len(df) - w):
        if highs[i] == highs[i-w:i+w+1].max():
            raw_sh.iloc[i] = highs[i]
        if lows[i] == lows[i-w:i+w+1].min():
            raw_sl.iloc[i] = lows[i]
    df["swing_high"] = raw_sh.shift(w).ffill()
    df["swing_low"] = raw_sl.shift(w).ffill()

    return df


def add_trend_filter(df_main: pd.DataFrame, df_5m: pd.DataFrame) -> pd.DataFrame:
    """
    在 df_main 上加入 1h 趨勢過濾欄位。
    用已完成的 1h bar 的 EMA(50) 判斷趨勢方向。
    """
    df_1h = df_5m.resample("1h").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    df_1h["ema50"] = df_1h["close"].ewm(span=50, adjust=False).mean()
    # shift(1) 確保只用已完成的 1h bar（避免 look-ahead）
    df_1h["trend_up"] = (df_1h["close"] > df_1h["ema50"]).shift(1)
    df_1h["trend_down"] = (df_1h["close"] < df_1h["ema50"]).shift(1)

    # 映射回 df_main 的時間框架
    df_main = df_main.copy()
    df_main["trend_up"] = df_1h["trend_up"].reindex(df_main.index, method="ffill")
    df_main["trend_down"] = df_1h["trend_down"].reindex(df_main.index, method="ffill")
    return df_main


# ══════════════════════════════════════════════════════════════
#  自適應 Trail（與 monitor 一致）
# ══════════════════════════════════════════════════════════════

def calc_adaptive_trail(side, trail_extreme, entry, atr, rsi, atr_pctile):
    base_mult = 1.0 + (atr_pctile / 100) * 1.5
    if side == "long":
        mult = base_mult * 0.6 if rsi > 65 else base_mult
        return max(trail_extreme - atr * mult, entry)
    else:
        mult = base_mult * 0.6 if rsi < 35 else base_mult
        return min(trail_extreme + atr * mult, entry)


# ══════════════════════════════════════════════════════════════
#  持倉
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

    tp1_done: bool = False
    phase: int = 1
    trail_hi: float = 0.0
    trail_lo: float = 0.0
    tp1_pnl: float = 0.0

    def __post_init__(self):
        self.trail_hi = self.entry_price
        self.trail_lo = self.entry_price


# ══════════════════════════════════════════════════════════════
#  回測引擎（參數化）
# ══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    notional = cfg["margin"] * cfg["leverage"]
    fee = cfg["fee"]
    max_same = cfg["max_same"]
    tp1_pct = cfg["tp1_pct"]
    slip = cfg.get("slippage_pct", 0.01)
    cooldown_bars = cfg.get("cooldown_after_sl", 0)

    positions: List[Position] = []
    trades = []
    cum_pnl = 0.0
    equity = []
    last_sl_bar = {"long": -999, "short": -999}

    start = max(120, cfg.get("start_bar", 120))

    for i in range(start, len(df) - 1):
        sig = df.iloc[i]
        nxt = df.iloc[i + 1]

        if pd.isna(sig["rsi"]) or pd.isna(sig["atr"]):
            equity.append(cum_pnl)
            continue

        atr = sig["atr"]
        rsi = sig["rsi"]
        atr_p = sig["atr_pctile"] if not pd.isna(sig.get("atr_pctile", np.nan)) else 50

        # ── 更新持倉 ──
        closed = []
        for pos in positions:
            res = _check_pos(pos, nxt, i+1, df, cfg)
            if res:
                cum_pnl += res["pnl"]
                trades.append({
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "entry_time": df.index[pos.entry_bar],
                    "exit_price": res["exit_price"],
                    "exit_time": df.index[i+1],
                    "exit_reason": res["reason"],
                    "pnl": res["pnl"],
                    "tp1_done": pos.tp1_done,
                    "duration_bars": i+1 - pos.entry_bar,
                })
                if res["reason"] == "stop_loss":
                    last_sl_bar[pos.side] = i + 1
                closed.append(pos)
        for p in closed:
            positions.remove(p)

        long_n = sum(1 for p in positions if p.side == "long")
        short_n = sum(1 for p in positions if p.side == "short")

        # ── 做多 ──
        if long_n < max_same:
            bb_lower = sig.get("bb_lower")
            if (not pd.isna(bb_lower)
                    and rsi < cfg["rsi_long"]
                    and sig["close"] < bb_lower):
                # 趨勢過濾
                if cfg.get("trend_filter") and not sig.get("trend_up", True):
                    pass  # 1h 趨勢向下，不做多
                # 冷卻期
                elif cooldown_bars > 0 and (i - last_sl_bar["long"]) < cooldown_bars:
                    pass
                else:
                    entry = nxt["open"] * (1 + slip / 100)
                    sl = _calc_sl(sig, entry, "long", cfg)
                    tp1 = entry + cfg["tp1_atr_mult"] * atr
                    positions.append(Position(
                        side="long", entry_price=entry, entry_bar=i+1,
                        stop_loss=sl, tp1_price=tp1, notional=notional,
                        atr_at_entry=atr,
                    ))

        # ── 做空 ──
        if short_n < max_same:
            bb_upper = sig.get("bb_upper")
            if (not pd.isna(bb_upper)
                    and rsi > cfg["rsi_short"]
                    and sig["close"] > bb_upper):
                if cfg.get("trend_filter") and not sig.get("trend_down", True):
                    pass
                elif cooldown_bars > 0 and (i - last_sl_bar["short"]) < cooldown_bars:
                    pass
                else:
                    entry = nxt["open"] * (1 - slip / 100)
                    sl = _calc_sl(sig, entry, "short", cfg)
                    tp1 = entry - cfg["tp1_atr_mult"] * atr
                    positions.append(Position(
                        side="short", entry_price=entry, entry_bar=i+1,
                        stop_loss=sl, tp1_price=tp1, notional=notional,
                        atr_at_entry=atr,
                    ))

        equity.append(cum_pnl)

    # 強制平倉
    for pos in positions:
        ep = df.iloc[-1]["close"]
        pnl = _pnl(pos, ep, fee, tp1_pct)
        cum_pnl += pnl
        trades.append({
            "side": pos.side, "entry_price": pos.entry_price,
            "entry_time": df.index[pos.entry_bar],
            "exit_price": ep, "exit_time": df.index[-1],
            "exit_reason": "end_of_data", "pnl": pnl,
            "tp1_done": pos.tp1_done,
            "duration_bars": len(df)-1-pos.entry_bar,
        })

    tdf = pd.DataFrame(trades)
    return {"trades": tdf, "metrics": _metrics(tdf, equity), "equity": equity}


def _calc_sl(sig, entry, side, cfg):
    """計算止損價格，含方向驗證"""
    atr = sig["atr"]
    sl_type = cfg.get("sl_type", "structural")
    fallback = cfg.get("sl_fallback_atr", 1.5)

    if sl_type == "fixed_atr":
        mult = cfg.get("sl_fixed_mult", 1.5)
        if side == "long":
            return entry - mult * atr
        else:
            return entry + mult * atr

    # structural
    buffer = cfg.get("sl_swing_buffer", 0.3)
    if side == "long":
        sw = sig.get("swing_low")
        if pd.isna(sw):
            sl = entry - fallback * atr
        else:
            sl = sw - buffer * atr
        if sl >= entry:
            sl = entry - fallback * atr
    else:
        sw = sig.get("swing_high")
        if pd.isna(sw):
            sl = entry + fallback * atr
        else:
            sl = sw + buffer * atr
        if sl <= entry:
            sl = entry + fallback * atr
    return sl


def _check_pos(pos, row, bar_idx, df, cfg):
    fee = cfg["fee"]
    tp1_pct = cfg["tp1_pct"]
    atr = row["atr"] if not pd.isna(row["atr"]) else pos.atr_at_entry
    rsi = row["rsi"] if not pd.isna(row["rsi"]) else 50
    atr_p = row["atr_pctile"] if not pd.isna(row.get("atr_pctile", np.nan)) else 50

    if pos.side == "long":
        # SL
        if row["low"] <= pos.stop_loss:
            pnl = _pnl(pos, pos.stop_loss, fee, tp1_pct)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "exit_price": pos.stop_loss, "reason": reason}
        # TP1
        if not pos.tp1_done and row["high"] >= pos.tp1_price:
            g = (pos.tp1_price - pos.entry_price) / pos.entry_price * (pos.notional * tp1_pct)
            pos.tp1_pnl = g - pos.notional * tp1_pct * fee * 2
            pos.tp1_done = True
            pos.phase = 2
            pos.stop_loss = pos.entry_price
            pos.trail_hi = max(pos.trail_hi, row["high"])
        # Trail
        if pos.phase == 2:
            pos.trail_hi = max(pos.trail_hi, row["high"])
            tsl = calc_adaptive_trail("long", pos.trail_hi, pos.entry_price, atr, rsi, atr_p)
            if tsl > pos.stop_loss:
                pos.stop_loss = tsl
            if row["low"] <= pos.stop_loss:
                ep = max(pos.stop_loss, row["open"])
                pnl = pos.tp1_pnl + _partial_pnl(pos, ep, fee, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": ep, "reason": "adaptive_trail"}

    else:  # short
        if row["high"] >= pos.stop_loss:
            pnl = _pnl(pos, pos.stop_loss, fee, tp1_pct)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "exit_price": pos.stop_loss, "reason": reason}
        if not pos.tp1_done and row["low"] <= pos.tp1_price:
            g = (pos.entry_price - pos.tp1_price) / pos.entry_price * (pos.notional * tp1_pct)
            pos.tp1_pnl = g - pos.notional * tp1_pct * fee * 2
            pos.tp1_done = True
            pos.phase = 2
            pos.stop_loss = pos.entry_price
            pos.trail_lo = min(pos.trail_lo, row["low"])
        if pos.phase == 2:
            pos.trail_lo = min(pos.trail_lo, row["low"])
            tsl = calc_adaptive_trail("short", pos.trail_lo, pos.entry_price, atr, rsi, atr_p)
            if tsl < pos.stop_loss:
                pos.stop_loss = tsl
            if row["high"] >= pos.stop_loss:
                ep = min(pos.stop_loss, row["open"])
                pnl = pos.tp1_pnl + _partial_pnl(pos, ep, fee, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": ep, "reason": "adaptive_trail"}
    return None


def _pnl(pos, exit_p, fee, tp1_pct):
    if pos.tp1_done:
        return pos.tp1_pnl + _partial_pnl(pos, exit_p, fee, 1 - tp1_pct)
    d = 1 if pos.side == "long" else -1
    return d * (exit_p - pos.entry_price) / pos.entry_price * pos.notional - pos.notional * fee * 2


def _partial_pnl(pos, exit_p, fee, pct):
    d = 1 if pos.side == "long" else -1
    n = pos.notional * pct
    return d * (exit_p - pos.entry_price) / pos.entry_price * n - n * fee


def _metrics(tdf, equity):
    if tdf.empty:
        return {"total_pnl": 0, "trades": 0, "win_rate": 0, "pf": 0,
                "max_dd": 0, "avg_dur": 0, "long_pnl": 0, "short_pnl": 0,
                "long_n": 0, "short_n": 0, "daily_trades": 0}
    n = len(tdf)
    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]
    gp = wins["pnl"].sum() if len(wins) else 0
    gl = abs(losses["pnl"].sum()) if len(losses) else 1e-10

    eq = pd.Series(equity)
    dd = (eq - eq.cummax()).min() if len(eq) > 0 else 0

    # 計算交易天數
    if not tdf.empty and "entry_time" in tdf.columns:
        days = (tdf["entry_time"].max() - tdf["entry_time"].min()).days
        daily = n / max(days, 1)
    else:
        daily = 0

    reasons = {}
    for r in tdf["exit_reason"].unique():
        sub = tdf[tdf["exit_reason"] == r]
        reasons[r] = {"n": len(sub), "pnl": round(sub["pnl"].sum(), 2)}

    return {
        "total_pnl": round(tdf["pnl"].sum(), 2),
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "pf": round(gp / gl, 2),
        "max_dd": round(dd, 2),
        "avg_dur": round(tdf["duration_bars"].mean(), 1),
        "long_n": len(tdf[tdf["side"] == "long"]),
        "short_n": len(tdf[tdf["side"] == "short"]),
        "long_pnl": round(tdf[tdf["side"] == "long"]["pnl"].sum(), 2),
        "short_pnl": round(tdf[tdf["side"] == "short"]["pnl"].sum(), 2),
        "daily_trades": round(daily, 1),
        "reasons": reasons,
    }


# ══════════════════════════════════════════════════════════════
#  策略定義
# ══════════════════════════════════════════════════════════════

BASE = {
    "margin": 100, "leverage": 20, "fee": 0.0004,
    "max_same": 3, "slippage_pct": 0.01,
    "sl_swing_buffer": 0.3, "sl_fallback_atr": 1.5,
}

STRATEGIES = {
    "A_baseline": {
        **BASE,
        "rsi_long": 30, "rsi_short": 70,
        "sl_type": "structural",
        "tp1_atr_mult": 1.0, "tp1_pct": 0.10,
    },
    "B_tight_rsi": {
        **BASE,
        "rsi_long": 25, "rsi_short": 75,
        "sl_type": "structural",
        "tp1_atr_mult": 1.0, "tp1_pct": 0.10,
    },
    "C_fixed_sl": {
        **BASE,
        "rsi_long": 30, "rsi_short": 70,
        "sl_type": "fixed_atr", "sl_fixed_mult": 1.5,
        "tp1_atr_mult": 1.0, "tp1_pct": 0.10,
    },
    "D_better_rr": {
        **BASE,
        "rsi_long": 30, "rsi_short": 70,
        "sl_type": "fixed_atr", "sl_fixed_mult": 1.5,
        "tp1_atr_mult": 1.5, "tp1_pct": 0.20,
    },
    "E_15m": {
        **BASE,
        "rsi_long": 30, "rsi_short": 70,
        "sl_type": "structural",
        "tp1_atr_mult": 1.0, "tp1_pct": 0.10,
    },
    "F_15m_opt": {
        **BASE,
        "rsi_long": 25, "rsi_short": 75,
        "sl_type": "fixed_atr", "sl_fixed_mult": 1.5,
        "tp1_atr_mult": 1.5, "tp1_pct": 0.20,
    },
    "G_trend": {
        **BASE,
        "rsi_long": 30, "rsi_short": 70,
        "sl_type": "structural",
        "tp1_atr_mult": 1.0, "tp1_pct": 0.10,
        "trend_filter": True,
    },
    "H_combined": {
        **BASE,
        "rsi_long": 25, "rsi_short": 75,
        "sl_type": "fixed_atr", "sl_fixed_mult": 1.5,
        "tp1_atr_mult": 1.5, "tp1_pct": 0.20,
        "trend_filter": True,
        "cooldown_after_sl": 6,
    },
}


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  v4 策略研究 — 8 方案對比回測")
    print("  實戰模式：下根 bar open 進場 + 0.01% 滑價 + SL 方向驗證")
    print("=" * 70)

    # ── 1. 抓 5m 資料 ──
    print("\n[1] 抓取 5m 資料...")
    df_5m_raw = fetch_klines(
        symbol="BTCUSDT", interval="5m",
        start_dt=datetime(2025, 10, 1, tzinfo=timezone.utc),
        end_dt=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )
    print(f"    5m: {len(df_5m_raw)} 根 ({df_5m_raw.index[0].date()} ~ {df_5m_raw.index[-1].date()})")

    # ── 2. 計算指標 ──
    print("[2] 計算指標...")
    df_5m = compute_indicators(df_5m_raw)
    df_5m = add_trend_filter(df_5m, df_5m_raw)

    # 15m：重採樣 + 重算指標
    df_15m_raw = df_5m_raw.resample("15min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    df_15m = compute_indicators(df_15m_raw)
    df_15m = add_trend_filter(df_15m, df_5m_raw)
    print(f"    15m: {len(df_15m)} 根")

    # ── 3. 跑回測 ──
    print("\n[3] 跑 8 個策略...\n")
    results = {}

    for name, cfg in STRATEGIES.items():
        df_use = df_15m if name.startswith(("E_", "F_")) else df_5m
        tf = "15m" if name.startswith(("E_", "F_")) else "5m"

        res = run_backtest(df_use, cfg)
        m = res["metrics"]
        results[name] = m

        print(f"  {name:<16} | PnL ${m['total_pnl']:>9.2f} | "
              f"{m['trades']:>5} trades ({m['daily_trades']:.1f}/day) | "
              f"WR {m['win_rate']:>5.1f}% | PF {m['pf']:>5.2f} | "
              f"DD ${m['max_dd']:>8.2f}")

    # ── 4. 詳細報告 ──
    print("\n" + "=" * 70)
    print("  詳細對比表")
    print("=" * 70)

    header = (f"{'策略':<16} {'PnL':>9} {'筆數':>6} {'日均':>5} "
              f"{'勝率':>6} {'PF':>6} {'回撤':>9} "
              f"{'多PnL':>9} {'空PnL':>9} {'持倉':>5}")
    print(header)
    print("-" * len(header))

    for name, m in results.items():
        print(f"{name:<16} ${m['total_pnl']:>8.0f} {m['trades']:>5}  "
              f"{m['daily_trades']:>4.1f} {m['win_rate']:>5.1f}% "
              f"{m['pf']:>5.2f} ${m['max_dd']:>8.0f} "
              f"${m['long_pnl']:>8.0f} ${m['short_pnl']:>8.0f} "
              f"{m['avg_dur']:>4.1f}")

    # ── 5. 出場原因分析（最佳策略）──
    best_name = max(results, key=lambda k: results[k]["total_pnl"])
    best = results[best_name]
    print(f"\n最佳策略: {best_name}")
    print(f"  出場原因明細:")
    for reason, info in best.get("reasons", {}).items():
        print(f"    {reason:<20} {info['n']:>4} 筆  ${info['pnl']:>8.2f}")

    # ── 6. 儲存最佳策略的交易明細 ──
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    best_cfg = STRATEGIES[best_name]
    df_use = df_15m if best_name.startswith(("E_", "F_")) else df_5m
    best_result = run_backtest(df_use, best_cfg)

    trades_file = os.path.join(RESULTS_DIR, f"{ts}_v4research_{best_name}_trades.csv")
    best_result["trades"].to_csv(trades_file, index=False)
    print(f"\n  [已存] {os.path.basename(trades_file)}")

    # ── 7. OOS 驗證（最佳策略）──
    print(f"\n{'='*70}")
    print(f"  Walk-Forward 驗證: {best_name}")
    print(f"{'='*70}")

    split = df_use.index[0] + pd.DateOffset(months=4)
    for label, subset in [("IS (4m)", df_use[df_use.index < split]),
                           ("OOS (2m)", df_use[df_use.index >= split])]:
        if len(subset) < 200:
            print(f"  {label}: 資料不足")
            continue
        r = run_backtest(subset, best_cfg)
        m2 = r["metrics"]
        print(f"  {label:<10} PnL ${m2['total_pnl']:>8.2f} | "
              f"{m2['trades']:>4} trades | WR {m2['win_rate']:.1f}% | "
              f"PF {m2['pf']:.2f} | DD ${m2['max_dd']:.2f}")
