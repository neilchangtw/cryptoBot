"""
績效指標計算模組
"""
import pandas as pd
import numpy as np


def compute_metrics(trades_df: pd.DataFrame) -> dict:
    """
    從交易紀錄計算所有績效指標。
    """
    if trades_df.empty:
        return _empty_metrics()

    n = len(trades_df)
    pnl = trades_df["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]

    gross_profit = wins.sum() if len(wins) > 0 else 0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 1e-10

    eq = pnl.cumsum()
    max_dd = (eq - eq.cummax()).min()

    longs = trades_df[trades_df["side"] == "long"]
    shorts = trades_df[trades_df["side"] == "short"]

    result = {
        "total_pnl": round(pnl.sum(), 2),
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(gross_profit / gross_loss, 2),
        "max_drawdown": round(max_dd, 2),
        "avg_pnl": round(pnl.mean(), 2),
        "best_trade": round(pnl.max(), 2),
        "worst_trade": round(pnl.min(), 2),
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_pnl": round(longs["pnl"].sum(), 2) if len(longs) > 0 else 0,
        "short_pnl": round(shorts["pnl"].sum(), 2) if len(shorts) > 0 else 0,
        "long_win_rate": round(
            len(longs[longs["pnl"] > 0]) / len(longs) * 100, 1
        ) if len(longs) > 0 else 0,
        "short_win_rate": round(
            len(shorts[shorts["pnl"] > 0]) / len(shorts) * 100, 1
        ) if len(shorts) > 0 else 0,
    }

    # 出場原因統計
    if "exit_reason" in trades_df.columns:
        for reason in trades_df["exit_reason"].unique():
            subset = trades_df[trades_df["exit_reason"] == reason]
            result[f"exit_{reason}_count"] = len(subset)
            result[f"exit_{reason}_pnl"] = round(subset["pnl"].sum(), 2)

    # 持倉時間
    if "duration_bars" in trades_df.columns:
        result["avg_duration_bars"] = round(trades_df["duration_bars"].mean(), 1)

    return result


def _empty_metrics() -> dict:
    return {
        "total_pnl": 0, "trades": 0, "win_rate": 0,
        "profit_factor": 0, "max_drawdown": 0, "avg_pnl": 0,
        "best_trade": 0, "worst_trade": 0,
        "long_trades": 0, "short_trades": 0,
        "long_pnl": 0, "short_pnl": 0,
        "long_win_rate": 0, "short_win_rate": 0,
    }
