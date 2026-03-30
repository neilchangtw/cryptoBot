"""
BTC 策略回測引擎 + Walk-Forward 驗證

執行方式: python backtest/backtest.py
結果輸出: backtest/results/
"""
import os
import sys
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import numpy as np

# 讓同目錄的 module 可以 import
sys.path.insert(0, os.path.dirname(__file__))
from data_fetcher import fetch_klines
from strategy_engine import compute_indicators, get_signals

warnings.filterwarnings("ignore")

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

# ── 風控設定 ──────────────────────────────────────────────────
RISK = {
    "account_balance": 1000,      # 帳戶資金 USDT
    "margin_per_trade": 100,       # 每單保證金 USDT
    "leverage": 20,                # 槓桿
    "max_same_direction": 3,       # 同方向最多幾單
    "fee_rate": 0.0004,            # 0.04% maker
    "tp1_pct": 0.10,               # TP1 出場比例 (10%)
    "tp1_atr_mult": 1.0,           # TP1 距離 = 1×ATR
    "sl_swing_buffer": 0.3,        # SL 距 Swing H/L 的 ATR 倍數
    "trail_atr_mult": 1.5,         # ATR Trail: trail_hi - 1.5×ATR（做多）
    "sl_fallback_atr": 1.5,        # SL 方向錯誤時的 fallback（ATR 倍數），0=跳過交易
}


# ── 持倉資料結構 ──────────────────────────────────────────────
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
    phase: str = "initial"
    trail_hi: float = 0.0
    trail_lo: float = 0.0
    tp1_pnl: float = 0.0

    def __post_init__(self):
        self.trail_hi = self.entry_price
        self.trail_lo = self.entry_price


# ── 回測引擎 ──────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, config: dict = None) -> dict:
    if config is None:
        config = RISK

    notional = config["margin_per_trade"] * config["leverage"]
    fee_rate = config["fee_rate"]
    max_same = config["max_same_direction"]
    tp1_pct = config["tp1_pct"]

    positions: List[Position] = []
    trades: List[dict] = []
    cumulative_pnl = 0.0
    equity = []

    for i in range(50, len(df)):
        row = df.iloc[i]

        if pd.isna(row["rsi"]) or pd.isna(row["bb_upper"]) or pd.isna(row["atr"]):
            equity.append(cumulative_pnl)
            continue

        # ── 1. 更新現有持倉 ───────────────────────────────────
        closed_this_bar = []
        for pos in positions:
            result = _check_position(pos, row, i, config)
            if result:
                pnl = result["pnl"]
                cumulative_pnl += pnl
                trades.append({
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "entry_time": df.index[pos.entry_bar],
                    "exit_price": result["exit_price"],
                    "exit_time": df.index[i],
                    "exit_reason": result["reason"],
                    "pnl": pnl,
                    "tp1_done": pos.tp1_done,
                    "duration_bars": i - pos.entry_bar,
                    "sl_was_breakeven": pos.tp1_done and result["reason"] == "stop_loss",
                })
                closed_this_bar.append(pos)

        for pos in closed_this_bar:
            positions.remove(pos)

        # ── 2. 計算目前多空數量 ───────────────────────────────
        long_count = sum(1 for p in positions if p.side == "long")
        short_count = sum(1 for p in positions if p.side == "short")

        # ── 3. 做多：RSI < 30 ─────────────────────────────────
        if row["long_signal"] and long_count < max_same:
            swing_low = row.get("swing_low", float("nan"))
            if pd.isna(swing_low):
                sl = row["close"] - 2.0 * row["atr"]
            else:
                sl = swing_low - config["sl_swing_buffer"] * row["atr"]

            # SL 方向驗證：做多 SL 必須 < 進場價
            if sl >= row["close"]:
                fallback = config.get("sl_fallback_atr", 1.5)
                if fallback > 0:
                    sl = row["close"] - fallback * row["atr"]
                else:
                    continue  # 跳過此交易

            tp1 = row["close"] + config["tp1_atr_mult"] * row["atr"]
            positions.append(Position(
                side="long", entry_price=row["close"], entry_bar=i,
                stop_loss=sl, tp1_price=tp1, notional=notional,
                atr_at_entry=row["atr"],
            ))

        # ── 4. 做空：close > BB上軌 ───────────────────────────
        if row["short_signal"] and short_count < max_same:
            swing_high = row.get("swing_high", float("nan"))
            if pd.isna(swing_high):
                sl = row["close"] + 2.0 * row["atr"]
            else:
                sl = swing_high + config["sl_swing_buffer"] * row["atr"]

            # SL 方向驗證：做空 SL 必須 > 進場價
            if sl <= row["close"]:
                fallback = config.get("sl_fallback_atr", 1.5)
                if fallback > 0:
                    sl = row["close"] + fallback * row["atr"]
                else:
                    continue  # 跳過此交易

            tp1 = row["close"] - config["tp1_atr_mult"] * row["atr"]
            positions.append(Position(
                side="short", entry_price=row["close"], entry_bar=i,
                stop_loss=sl, tp1_price=tp1, notional=notional,
                atr_at_entry=row["atr"],
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
            "sl_was_breakeven": False,
        })

    trades_df = pd.DataFrame(trades)
    metrics = _calc_metrics(trades_df, equity)
    return {"trades": trades_df, "metrics": metrics, "equity_curve": equity}


def _check_position(pos: Position, row, i: int, config: dict) -> Optional[dict]:
    fee_rate = config["fee_rate"]
    tp1_pct = config["tp1_pct"]
    trail_atr_mult = config["trail_atr_mult"]

    if pos.side == "long":
        if row["low"] <= pos.stop_loss:
            pnl = _calc_pnl(pos, pos.stop_loss, fee_rate, tp1_pct)
            return {"pnl": pnl, "exit_price": pos.stop_loss, "reason": "stop_loss"}

        if not pos.tp1_done and row["high"] >= pos.tp1_price:
            tp1_gross = (pos.tp1_price - pos.entry_price) / pos.entry_price * (pos.notional * tp1_pct)
            tp1_fee = pos.notional * tp1_pct * fee_rate * 2
            pos.tp1_pnl = tp1_gross - tp1_fee
            pos.tp1_done = True
            pos.phase = "trailing"
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_hi = max(pos.trail_hi, row["high"])

        if pos.phase == "trailing":
            pos.trail_hi = max(pos.trail_hi, row["high"])
            trail_stop = pos.trail_hi - trail_atr_mult * row["atr"]
            if row["low"] <= trail_stop:
                exit_p = max(trail_stop, row["open"])
                pnl = pos.tp1_pnl + _calc_partial_pnl(pos, exit_p, fee_rate, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": exit_p, "reason": "atr_trail"}

    elif pos.side == "short":
        if row["high"] >= pos.stop_loss:
            pnl = _calc_pnl(pos, pos.stop_loss, fee_rate, tp1_pct)
            return {"pnl": pnl, "exit_price": pos.stop_loss, "reason": "stop_loss"}

        if not pos.tp1_done and row["low"] <= pos.tp1_price:
            tp1_gross = (pos.entry_price - pos.tp1_price) / pos.entry_price * (pos.notional * tp1_pct)
            tp1_fee = pos.notional * tp1_pct * fee_rate * 2
            pos.tp1_pnl = tp1_gross - tp1_fee
            pos.tp1_done = True
            pos.phase = "trailing"
            pos.stop_loss = pos.entry_price  # 保本
            pos.trail_lo = min(pos.trail_lo, row["low"])

        if pos.phase == "trailing":
            if row["close"] > row["ema9"]:
                exit_p = row["close"]
                pnl = pos.tp1_pnl + _calc_partial_pnl(pos, exit_p, fee_rate, 1 - tp1_pct)
                return {"pnl": pnl, "exit_price": exit_p, "reason": "ema9_trail"}

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


def _calc_metrics(trades_df: pd.DataFrame, equity: list) -> dict:
    if trades_df.empty:
        return {k: 0 for k in ["total_pnl", "trades", "win_rate", "profit_factor",
                                "max_drawdown", "avg_duration_bars", "long_trades",
                                "short_trades", "long_pnl", "short_pnl",
                                "real_sl_count", "breakeven_sl_count"]}

    total_pnl = trades_df["pnl"].sum()
    n_trades = len(trades_df)
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    win_rate = len(wins) / n_trades
    gross_profit = wins["pnl"].sum() if len(wins) > 0 else 0
    gross_loss = abs(losses["pnl"].sum()) if len(losses) > 0 else 1e-10
    profit_factor = gross_profit / gross_loss

    eq = pd.Series(equity)
    max_drawdown = (eq - eq.cummax()).min()

    # 分析止損類型：真實止損 vs 保本止損
    sl_trades = trades_df[trades_df["exit_reason"] == "stop_loss"]
    real_sl = sl_trades[~sl_trades["sl_was_breakeven"]] if "sl_was_breakeven" in trades_df.columns else sl_trades
    be_sl = sl_trades[sl_trades["sl_was_breakeven"]] if "sl_was_breakeven" in trades_df.columns else pd.DataFrame()

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
        "real_sl_count": len(real_sl),
        "real_sl_pnl": round(real_sl["pnl"].sum(), 2) if len(real_sl) > 0 else 0,
        "breakeven_sl_count": len(be_sl),
        "breakeven_sl_pnl": round(be_sl["pnl"].sum(), 2) if len(be_sl) > 0 else 0,
    }


# ── Walk-Forward 驗證 ─────────────────────────────────────────
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
        if len(subset) < 100:
            continue
        result = run_backtest(subset, config)
        results[label] = result
        _print_report(label, result)

    return results


def _print_report(label: str, result: dict):
    m = result["metrics"]
    trades = result["trades"]
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
    print(f"  止損明細:")
    print(f"    真實止損:        {m['real_sl_count']:>4} 筆  ${m['real_sl_pnl']:>8.2f}")
    print(f"    保本止損:        {m['breakeven_sl_count']:>4} 筆  ${m['breakeven_sl_pnl']:>8.2f}")

    if not trades.empty:
        print(f"  出場原因:")
        for reason, cnt in trades["exit_reason"].value_counts().items():
            pnl_s = trades[trades["exit_reason"] == reason]["pnl"].sum()
            print(f"    {reason:<20} {cnt:>4} 筆  ${pnl_s:>8.2f}")


def save_results(label: str, result: dict):
    """儲存回測結果到 backtest/results/"""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "-")

    trades_file = os.path.join(RESULTS_DIR, f"{ts}_{safe_label}_trades.csv")
    result["trades"].to_csv(trades_file, index=False)

    metrics_file = os.path.join(RESULTS_DIR, f"{ts}_{safe_label}_metrics.csv")
    pd.DataFrame([result["metrics"]]).to_csv(metrics_file, index=False)

    print(f"\n  [已存] {os.path.basename(trades_file)}")
    print(f"  [已存] {os.path.basename(metrics_file)}")


# ── 主程式 ──────────────────────────────────────────────────
if __name__ == "__main__":
    # 1. 抓資料
    df_raw = fetch_klines(
        symbol="BTCUSDT",
        interval="1h",
        start_dt=datetime(2025, 9, 1, tzinfo=timezone.utc),
        end_dt=datetime(2026, 3, 28, tzinfo=timezone.utc),
    )

    # 2. 計算指標 + 標記信號
    df = compute_indicators(df_raw)
    df = get_signals(df)

    print(f"\n資料: {df.index[0].date()} ~ {df.index[-1].date()}, {len(df)} 根 1h K線")
    print(f"多頭信號 (RSI<30):    {df['long_signal'].sum()} 次")
    print(f"空頭信號 (>BB上軌):   {df['short_signal'].sum()} 次")

    # 3. 全期回測
    print("\n" + "=" * 62)
    print("  全期回測（In-Sample，供參考）")
    full_result = run_backtest(df)
    _print_report("全期", full_result)
    save_results("full", full_result)

    # 4. Walk-Forward 驗證
    wf_results = run_walkforward(df, train_months=4)
    for label, res in wf_results.items():
        save_results(label, res)
