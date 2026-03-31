"""
v4 回測引擎 — 與實盤策略完全對齊

策略：Strategy H — 5m RSI+BB 均值回歸
  進場：RSI<30 + Close<BB_Lower (做多) / RSI>70 + Close>BB_Upper (做空)
  止損：結構止損 Swing ± 0.3×ATR（方向錯誤時 fallback 1.5×ATR）
  出場：TP1 10% at 1.0×ATR + 自適應 ATR+RSI Trail

實戰對齊重點：
  1. 信號 bar 做決策，下根 bar 的 open 進場（模擬市價單滑價）
  2. SL 方向驗證 + fallback
  3. 自適應 ATR+RSI Trail（非固定倍數、非 EMA9）
  4. 同方向最多 3 單（獨立持倉追蹤）

執行方式: python backtest/backtest.py
結果輸出: backtest/results/
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ── 風控設定（與實盤 .env 一致）─────────────────────────────
RISK = {
    "account_balance": 1000,
    "margin_per_trade": 100,
    "leverage": 20,
    "max_same_direction": 3,
    "fee_rate": 0.0004,          # 0.04% maker
    "tp1_pct": 0.10,             # TP1 平倉 10%
    "tp1_atr_mult": 1.0,         # TP1 = entry ± 1.0×ATR
    "sl_swing_buffer": 0.3,      # SL = Swing ± 0.3×ATR
    "sl_fallback_atr": 1.5,      # SL 方向錯誤 fallback
    "slippage_pct": 0.01,        # 0.01% 滑價（市價單）
}

# ── 指標參數（與 strategy_runner 一致）───────────────────────
SWING_WINDOW = 5
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2
ATR_PERIOD = 14
ATR_PCTILE_WINDOW = 100


# ══════════════════════════════════════════════════════════════
#  指標計算（與 strategy_runner.compute_5m_indicators 完全一致）
# ══════════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算 5m 指標：RSI(14), BB(20,2), ATR(14), ATR Pctile, Swing H/L(5)"""
    df = df.copy()

    # ATR(14)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()

    # RSI(14) — Wilder's smoothing
    d = df["close"].diff()
    g = d.clip(lower=0).ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    l_s = (-d.clip(upper=0)).ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    df["rsi"] = 100 - 100 / (1 + g / l_s)

    # Bollinger Bands(20, 2)
    df["bb_mid"] = df["close"].rolling(BB_PERIOD).mean()
    bb_std = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * bb_std
    df["bb_lower"] = df["bb_mid"] - BB_STD * bb_std

    # ATR Percentile
    df["atr_pctile"] = df["atr"].rolling(ATR_PCTILE_WINDOW).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100
        if x.max() != x.min() else 50, raw=False
    )

    # Swing High/Low（window=5，用 shift 避免 look-ahead）
    w = SWING_WINDOW
    raw_sh = pd.Series(np.nan, index=df.index)
    raw_sl = pd.Series(np.nan, index=df.index)
    highs = df["high"].values
    lows = df["low"].values
    for i in range(w, len(df) - w):
        seg_h = highs[i - w: i + w + 1]
        if highs[i] == seg_h.max():
            raw_sh.iloc[i] = highs[i]
        seg_l = lows[i - w: i + w + 1]
        if lows[i] == seg_l.min():
            raw_sl.iloc[i] = lows[i]
    # shift(w) 避免 look-ahead：bar i 只能用 bar i-w 確認的 Swing
    df["swing_high"] = raw_sh.shift(w).ffill()
    df["swing_low"] = raw_sl.shift(w).ffill()

    return df


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
    atr_pctile_at_entry: float

    tp1_done: bool = False
    phase: int = 1              # 1=等TP1, 2=自適應trail
    trail_hi: float = 0.0
    trail_lo: float = 0.0
    tp1_pnl: float = 0.0

    def __post_init__(self):
        self.trail_hi = self.entry_price
        self.trail_lo = self.entry_price


# ══════════════════════════════════════════════════════════════
#  自適應 ATR+RSI Trail（與 cryptobot_monitor 完全一致）
# ══════════════════════════════════════════════════════════════

def calc_adaptive_trail_sl(side, trail_extreme, entry, atr, rsi, atr_pctile):
    """
    base_mult = 1.0 + (atr_pctile/100) × 1.5
    RSI 加速：做多 RSI>65 → ×0.6 / 做空 RSI<35 → ×0.6
    最低為保本
    """
    base_mult = 1.0 + (atr_pctile / 100) * 1.5
    if side == "long":
        mult = base_mult * 0.6 if rsi > 65 else base_mult
        trail_sl = trail_extreme - atr * mult
        return max(trail_sl, entry)
    else:
        mult = base_mult * 0.6 if rsi < 35 else base_mult
        trail_sl = trail_extreme + atr * mult
        return min(trail_sl, entry)


# ══════════════════════════════════════════════════════════════
#  回測引擎
# ══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, config: dict = None) -> dict:
    if config is None:
        config = RISK

    notional = config["margin_per_trade"] * config["leverage"]
    fee_rate = config["fee_rate"]
    max_same = config["max_same_direction"]
    tp1_pct = config["tp1_pct"]
    slippage_pct = config.get("slippage_pct", 0)

    positions: List[Position] = []
    trades: List[dict] = []
    cumulative_pnl = 0.0
    equity = []

    # 從第 101 根開始（確保所有指標包含 ATR Pctile 都有值）
    start_bar = max(ATR_PCTILE_WINDOW + ATR_PERIOD, 101)

    for i in range(start_bar, len(df) - 1):
        # 信號 bar = i（已完成），進場 bar = i+1（下一根的 open）
        signal_row = df.iloc[i]
        next_row = df.iloc[i + 1]

        if pd.isna(signal_row["rsi"]) or pd.isna(signal_row["atr"]):
            equity.append(cumulative_pnl)
            continue

        atr = signal_row["atr"]
        rsi = signal_row["rsi"]
        atr_pctile = signal_row["atr_pctile"] if not pd.isna(signal_row["atr_pctile"]) else 50

        # ── 1. 更新現有持倉（用 next_row 的 OHLC 模擬 bar 內價格變動）─
        closed_this_bar = []
        for pos in positions:
            result = _check_position(pos, next_row, i + 1, df, config)
            if result:
                pnl = result["pnl"]
                cumulative_pnl += pnl
                trades.append({
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "entry_time": df.index[pos.entry_bar],
                    "exit_price": result["exit_price"],
                    "exit_time": df.index[i + 1],
                    "exit_reason": result["reason"],
                    "pnl": pnl,
                    "tp1_done": pos.tp1_done,
                    "duration_bars": i + 1 - pos.entry_bar,
                    "rsi_entry": rsi,
                    "atr_pctile_entry": atr_pctile,
                })
                closed_this_bar.append(pos)
        for pos in closed_this_bar:
            positions.remove(pos)

        # ── 2. 持倉數量（獨立追蹤，與回測一致）────────────────
        long_count = sum(1 for p in positions if p.side == "long")
        short_count = sum(1 for p in positions if p.side == "short")

        # ── 3. 做多：RSI<30 AND Close<BB_Lower（雙條件）──────
        if long_count < max_same:
            bb_lower = signal_row.get("bb_lower")
            if (not pd.isna(bb_lower) and rsi < 30
                    and signal_row["close"] < bb_lower):

                swing_low = signal_row.get("swing_low")
                if pd.isna(swing_low):
                    sl = signal_row["close"] - config["sl_fallback_atr"] * atr
                else:
                    sl = swing_low - config["sl_swing_buffer"] * atr

                # 進場價 = 下根 bar 的 open + 滑價
                entry = next_row["open"] * (1 + slippage_pct / 100)

                # SL 方向驗證
                if sl >= entry:
                    sl = entry - config["sl_fallback_atr"] * atr

                tp1 = entry + config["tp1_atr_mult"] * atr

                positions.append(Position(
                    side="long", entry_price=entry, entry_bar=i + 1,
                    stop_loss=sl, tp1_price=tp1, notional=notional,
                    atr_at_entry=atr, atr_pctile_at_entry=atr_pctile,
                ))

        # ── 4. 做空：RSI>70 AND Close>BB_Upper（雙條件）──────
        if short_count < max_same:
            bb_upper = signal_row.get("bb_upper")
            if (not pd.isna(bb_upper) and rsi > 70
                    and signal_row["close"] > bb_upper):

                swing_high = signal_row.get("swing_high")
                if pd.isna(swing_high):
                    sl = signal_row["close"] + config["sl_fallback_atr"] * atr
                else:
                    sl = swing_high + config["sl_swing_buffer"] * atr

                # 進場價 = 下根 bar 的 open - 滑價
                entry = next_row["open"] * (1 - slippage_pct / 100)

                # SL 方向驗證
                if sl <= entry:
                    sl = entry + config["sl_fallback_atr"] * atr

                tp1 = entry - config["tp1_atr_mult"] * atr

                positions.append(Position(
                    side="short", entry_price=entry, entry_bar=i + 1,
                    stop_loss=sl, tp1_price=tp1, notional=notional,
                    atr_at_entry=atr, atr_pctile_at_entry=atr_pctile,
                ))

        equity.append(cumulative_pnl)

    # 強制平倉未平倉單
    last_row = df.iloc[-1]
    for pos in positions:
        exit_price = last_row["close"]
        pnl = _calc_pnl(pos, exit_price, fee_rate, tp1_pct)
        cumulative_pnl += pnl
        trades.append({
            "side": pos.side,
            "entry_price": pos.entry_price,
            "entry_time": df.index[pos.entry_bar],
            "exit_price": exit_price,
            "exit_time": df.index[-1],
            "exit_reason": "end_of_data",
            "pnl": pnl,
            "tp1_done": pos.tp1_done,
            "duration_bars": len(df) - 1 - pos.entry_bar,
            "rsi_entry": 0,
            "atr_pctile_entry": 0,
        })

    trades_df = pd.DataFrame(trades)
    metrics = _calc_metrics(trades_df, equity)
    return {"trades": trades_df, "metrics": metrics, "equity_curve": equity}


def _check_position(pos: Position, row, bar_idx: int, df: pd.DataFrame,
                     config: dict) -> Optional[dict]:
    """檢查持倉是否觸發出場"""
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]

    atr = row["atr"] if not pd.isna(row["atr"]) else pos.atr_at_entry
    rsi = row["rsi"] if not pd.isna(row["rsi"]) else 50
    atr_pctile = row["atr_pctile"] if not pd.isna(row.get("atr_pctile", float("nan"))) else 50

    if pos.side == "long":
        # ── SL 檢查（優先）──
        if row["low"] <= pos.stop_loss:
            exit_p = pos.stop_loss
            pnl = _calc_pnl(pos, exit_p, fee_rate, tp1_pct)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "exit_price": exit_p, "reason": reason}

        # ── Phase 1：TP1 ──
        if not pos.tp1_done and row["high"] >= pos.tp1_price:
            tp1_gross = (pos.tp1_price - pos.entry_price) / pos.entry_price * (pos.notional * tp1_pct)
            tp1_fee = pos.notional * tp1_pct * fee_rate * 2
            pos.tp1_pnl = tp1_gross - tp1_fee
            pos.tp1_done = True
            pos.phase = 2
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_hi = max(pos.trail_hi, row["high"])

        # ── Phase 2：自適應 ATR+RSI Trail ──
        if pos.phase == 2:
            pos.trail_hi = max(pos.trail_hi, row["high"])
            trail_sl = calc_adaptive_trail_sl(
                "long", pos.trail_hi, pos.entry_price, atr, rsi, atr_pctile
            )
            if trail_sl > pos.stop_loss:
                pos.stop_loss = trail_sl
            if row["low"] <= pos.stop_loss:
                exit_p = max(pos.stop_loss, row["open"])
                pnl = pos.tp1_pnl + _calc_partial_pnl(pos, exit_p, fee_rate, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": exit_p, "reason": "adaptive_trail"}

    elif pos.side == "short":
        # ── SL 檢查（優先）──
        if row["high"] >= pos.stop_loss:
            exit_p = pos.stop_loss
            pnl = _calc_pnl(pos, exit_p, fee_rate, tp1_pct)
            reason = "breakeven_sl" if pos.tp1_done else "stop_loss"
            return {"pnl": pnl, "exit_price": exit_p, "reason": reason}

        # ── Phase 1：TP1 ──
        if not pos.tp1_done and row["low"] <= pos.tp1_price:
            tp1_gross = (pos.entry_price - pos.tp1_price) / pos.entry_price * (pos.notional * tp1_pct)
            tp1_fee = pos.notional * tp1_pct * fee_rate * 2
            pos.tp1_pnl = tp1_gross - tp1_fee
            pos.tp1_done = True
            pos.phase = 2
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_lo = min(pos.trail_lo, row["low"])

        # ── Phase 2：自適應 ATR+RSI Trail ──
        if pos.phase == 2:
            pos.trail_lo = min(pos.trail_lo, row["low"])
            trail_sl = calc_adaptive_trail_sl(
                "short", pos.trail_lo, pos.entry_price, atr, rsi, atr_pctile
            )
            if trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
            if row["high"] >= pos.stop_loss:
                exit_p = min(pos.stop_loss, row["open"])
                pnl = pos.tp1_pnl + _calc_partial_pnl(pos, exit_p, fee_rate, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": exit_p, "reason": "adaptive_trail"}

    return None


def _calc_pnl(pos: Position, exit_price: float, fee_rate: float, tp1_pct: float) -> float:
    if pos.tp1_done:
        return pos.tp1_pnl + _calc_partial_pnl(pos, exit_price, fee_rate, 1 - tp1_pct)
    direction = 1 if pos.side == "long" else -1
    gross = direction * (exit_price - pos.entry_price) / pos.entry_price * pos.notional
    return gross - pos.notional * fee_rate * 2


def _calc_partial_pnl(pos: Position, exit_price: float, fee_rate: float, pct: float) -> float:
    direction = 1 if pos.side == "long" else -1
    n = pos.notional * pct
    gross = direction * (exit_price - pos.entry_price) / pos.entry_price * n
    return gross - n * fee_rate


# ══════════════════════════════════════════════════════════════
#  績效計算
# ══════════════════════════════════════════════════════════════

def _calc_metrics(trades_df: pd.DataFrame, equity: list) -> dict:
    if trades_df.empty:
        return {k: 0 for k in ["total_pnl", "trades", "win_rate", "profit_factor",
                                "max_drawdown", "avg_duration_bars"]}

    total_pnl = trades_df["pnl"].sum()
    n_trades = len(trades_df)
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    win_rate = len(wins) / n_trades
    gross_profit = wins["pnl"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl"].sum()) if len(losses) > 0 else 1e-10
    profit_factor = gross_profit / gross_loss

    eq = pd.Series(equity)
    max_drawdown = (eq - eq.cummax()).min() if len(eq) > 0 else 0

    # 出場原因分析
    reason_counts = {}
    reason_pnl = {}
    if "exit_reason" in trades_df.columns:
        for reason in trades_df["exit_reason"].unique():
            subset = trades_df[trades_df["exit_reason"] == reason]
            reason_counts[reason] = len(subset)
            reason_pnl[reason] = round(subset["pnl"].sum(), 2)

    return {
        "total_pnl": round(total_pnl, 2),
        "trades": n_trades,
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_drawdown, 2),
        "avg_duration_bars": round(trades_df["duration_bars"].mean(), 1),
        "long_trades": len(trades_df[trades_df["side"] == "long"]),
        "short_trades": len(trades_df[trades_df["side"] == "short"]),
        "long_pnl": round(trades_df[trades_df["side"] == "long"]["pnl"].sum(), 2),
        "short_pnl": round(trades_df[trades_df["side"] == "short"]["pnl"].sum(), 2),
        "reason_counts": reason_counts,
        "reason_pnl": reason_pnl,
    }


# ══════════════════════════════════════════════════════════════
#  Walk-Forward 驗證
# ══════════════════════════════════════════════════════════════

def run_walkforward(df: pd.DataFrame, train_months: int = 4, config: dict = None) -> dict:
    if config is None:
        config = RISK

    start = df.index[0]
    split = start + pd.DateOffset(months=train_months)
    end = df.index[-1]

    df_train = df[df.index < split]
    df_test = df[df.index >= split]

    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Walk-Forward 驗證")
    print(f"  訓練期: {start.date()} ~ {split.date()} ({len(df_train)} 根)")
    print(f"  測試期: {split.date()} ~ {end.date()} ({len(df_test)} 根)")
    print(sep)

    results = {}
    for label, subset in [("訓練期 In-Sample", df_train), ("測試期 Out-of-Sample", df_test)]:
        if len(subset) < 200:
            print(f"  [{label}] 資料不足 ({len(subset)} 根)，跳過")
            continue
        result = run_backtest(subset, config)
        results[label] = result
        _print_report(label, result)

    return results


def _print_report(label: str, result: dict):
    m = result["metrics"]
    sep = "-" * 50

    print(f"\n  [{label}]")
    print(f"  {sep}")
    print(f"  總損益:            ${m['total_pnl']:>9.2f}")
    print(f"  交易次數:          {m['trades']:>5} 筆  (多 {m['long_trades']} / 空 {m['short_trades']})")
    print(f"  多單損益:          ${m['long_pnl']:>9.2f}")
    print(f"  空單損益:          ${m['short_pnl']:>9.2f}")
    print(f"  勝率:              {m['win_rate']:>5.1f}%")
    print(f"  Profit Factor:     {m['profit_factor']:>9.2f}")
    print(f"  最大回撤:          ${m['max_drawdown']:>9.2f}")
    print(f"  平均持倉時間:      {m['avg_duration_bars']:>5.1f} 根 K線")
    if m.get("reason_counts"):
        print(f"  出場原因:")
        for reason, cnt in m["reason_counts"].items():
            pnl_s = m["reason_pnl"].get(reason, 0)
            print(f"    {reason:<20} {cnt:>4} 筆  ${pnl_s:>8.2f}")


def save_results(label: str, result: dict):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "-")

    trades_file = os.path.join(RESULTS_DIR, f"{ts}_{safe_label}_trades.csv")
    result["trades"].to_csv(trades_file, index=False)

    metrics_file = os.path.join(RESULTS_DIR, f"{ts}_{safe_label}_metrics.csv")
    metrics_flat = {k: v for k, v in result["metrics"].items()
                    if not isinstance(v, dict)}
    pd.DataFrame([metrics_flat]).to_csv(metrics_file, index=False)

    print(f"\n  [已存] {os.path.basename(trades_file)}")
    print(f"  [已存] {os.path.basename(metrics_file)}")


# ══════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. 抓 5m 資料（與實盤一致）
    df_raw = fetch_klines(
        symbol="BTCUSDT",
        interval="5m",
        start_dt=datetime(2025, 10, 1, tzinfo=timezone.utc),
        end_dt=datetime(2026, 3, 31, tzinfo=timezone.utc),
    )

    # 2. 計算指標
    df = compute_indicators(df_raw)

    valid = df.dropna(subset=["rsi", "atr", "bb_lower", "bb_upper"])
    long_signals = (valid["rsi"] < 30) & (valid["close"] < valid["bb_lower"])
    short_signals = (valid["rsi"] > 70) & (valid["close"] > valid["bb_upper"])

    print(f"\n資料: {df.index[0].date()} ~ {df.index[-1].date()}, {len(df)} 根 5m K線")
    print(f"做多信號 (RSI<30 + BB_Lower): {long_signals.sum()} 次")
    print(f"做空信號 (RSI>70 + BB_Upper): {short_signals.sum()} 次")

    # 3. 全期回測
    print("\n" + "=" * 62)
    print("  v4 全期回測（5m, 雙條件, 自適應Trail, 含滑價）")
    full_result = run_backtest(df)
    _print_report("全期", full_result)
    save_results("v4_full", full_result)

    # 4. Walk-Forward 驗證
    wf_results = run_walkforward(df, train_months=4)
    for label, res in wf_results.items():
        save_results(f"v4_{label}", res)
