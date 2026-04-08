"""
每日健康報告

讀取 trades.csv + bar_snapshots.csv + daily_summary.csv，
輸出策略健康狀態，標記異常指標。

用法：python check_health.py [--days 30] [--telegram]
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

import recorder
import strategy


def load_trades() -> pd.DataFrame:
    """載入 trades.csv"""
    if not os.path.exists(recorder.TRADES_CSV):
        return pd.DataFrame()
    df = pd.read_csv(recorder.TRADES_CSV)
    if "entry_time_utc8" in df.columns:
        df["entry_time_utc8"] = pd.to_datetime(df["entry_time_utc8"], errors="coerce")
    if "exit_time_utc8" in df.columns:
        df["exit_time_utc8"] = pd.to_datetime(df["exit_time_utc8"], errors="coerce")
    return df


def load_daily() -> pd.DataFrame:
    """載入 daily_summary.csv"""
    if not os.path.exists(recorder.DAILY_SUMMARY_CSV):
        return pd.DataFrame()
    return pd.read_csv(recorder.DAILY_SUMMARY_CSV)


def check_health(days: int = 30) -> dict:
    """
    執行健康檢查。

    Returns:
        {
            "overall": "NORMAL" / "WARNING" / "PAUSE",
            "checks": [{name, value, threshold, status, detail}, ...],
            "summary": str,
        }
    """
    trades = load_trades()
    checks = []

    # 篩選近 N 天已平倉的交易
    cutoff = datetime.now() - timedelta(days=days)
    if len(trades) > 0 and "exit_time_utc8" in trades.columns:
        closed = trades[trades["exit_time_utc8"].notna()].copy()
        closed = closed[closed["exit_time_utc8"] >= cutoff]
    else:
        closed = pd.DataFrame()

    n_closed = len(closed)

    # ── 1. 月交易數 ──
    monthly_rate = n_closed / max(days / 30, 1)
    if 10 <= monthly_rate <= 30:
        status = "OK"
    elif 5 <= monthly_rate < 10 or 30 < monthly_rate <= 40:
        status = "WARNING"
    else:
        status = "ALERT"
    checks.append({
        "name": "Monthly trade count",
        "value": f"{monthly_rate:.1f}",
        "threshold": "10-30",
        "status": status,
        "detail": f"{n_closed} trades in {days}d",
    })

    if n_closed == 0:
        return {
            "overall": "WARNING",
            "checks": checks,
            "summary": f"No closed trades in last {days} days",
        }

    # ── 2. SafeNet 觸發率 ──
    if "exit_type" in closed.columns:
        sn_count = (closed["exit_type"] == "SafeNet").sum()
        sn_rate = sn_count / n_closed * 100
    else:
        sn_count = 0
        sn_rate = 0
    checks.append({
        "name": "SafeNet trigger rate",
        "value": f"{sn_rate:.1f}%",
        "threshold": "<15%",
        "status": "OK" if sn_rate < 15 else ("WARNING" if sn_rate < 25 else "ALERT"),
        "detail": f"{sn_count}/{n_closed}",
    })

    # ── 3. 24-48h 持倉勝率 ──
    if "hold_bars" in closed.columns:
        closed["hold_bars"] = pd.to_numeric(closed["hold_bars"], errors="coerce")
        mid_hold = closed[(closed["hold_bars"] >= 24) & (closed["hold_bars"] < 48)]
        if len(mid_hold) > 0 and "win_loss" in mid_hold.columns:
            mid_wr = (mid_hold["win_loss"] == "WIN").mean() * 100
        else:
            mid_wr = None
    else:
        mid_hold = pd.DataFrame()
        mid_wr = None

    if mid_wr is not None:
        checks.append({
            "name": "24-48h hold WR",
            "value": f"{mid_wr:.0f}%",
            "threshold": ">=70%",
            "status": "OK" if mid_wr >= 70 else ("WARNING" if mid_wr >= 50 else "ALERT"),
            "detail": f"{len(mid_hold)} trades in 24-48h bucket",
        })

    # ── 4. 平均持倉時間 ──
    if "hold_bars" in closed.columns:
        avg_hold = closed["hold_bars"].mean()
        checks.append({
            "name": "Avg hold time",
            "value": f"{avg_hold:.1f}h",
            "threshold": ">=18h",
            "status": "OK" if avg_hold >= 18 else ("WARNING" if avg_hold >= 12 else "ALERT"),
            "detail": f"Median: {closed['hold_bars'].median():.1f}h",
        })

    # ── 5. MFE/MAE 比值 ──
    if "max_favorable_excursion_pct" in closed.columns and "max_adverse_excursion_pct" in closed.columns:
        mfe = pd.to_numeric(closed["max_favorable_excursion_pct"], errors="coerce").abs().mean()
        mae = pd.to_numeric(closed["max_adverse_excursion_pct"], errors="coerce").abs().mean()
        if mae > 0:
            mfe_mae = mfe / mae
            checks.append({
                "name": "MFE/MAE ratio",
                "value": f"{mfe_mae:.2f}",
                "threshold": ">1.5",
                "status": "OK" if mfe_mae > 1.5 else ("WARNING" if mfe_mae > 1.0 else "ALERT"),
                "detail": f"Avg MFE: {mfe:.2f}%, Avg MAE: {mae:.2f}%",
            })

    # ── 6. 整體 PnL ──
    if "net_pnl_usd" in closed.columns:
        total_pnl = pd.to_numeric(closed["net_pnl_usd"], errors="coerce").sum()
        win_rate = (closed["win_loss"] == "WIN").mean() * 100 if "win_loss" in closed.columns else 0
        checks.append({
            "name": "Total PnL",
            "value": f"${total_pnl:+.2f}",
            "threshold": ">$0",
            "status": "OK" if total_pnl > 0 else ("WARNING" if total_pnl > -200 else "ALERT"),
            "detail": f"WR: {win_rate:.1f}%",
        })

    # ── 7. Profit Factor ──
    if "net_pnl_usd" in closed.columns:
        pnl_series = pd.to_numeric(closed["net_pnl_usd"], errors="coerce")
        wins_sum = pnl_series[pnl_series > 0].sum()
        loss_sum = abs(pnl_series[pnl_series < 0].sum())
        pf = wins_sum / loss_sum if loss_sum > 0 else 999
        checks.append({
            "name": "Profit Factor",
            "value": f"{pf:.2f}",
            "threshold": ">=1.5",
            "status": "OK" if pf >= 1.5 else ("WARNING" if pf >= 1.0 else "ALERT"),
            "detail": f"Wins: ${wins_sum:.0f}, Losses: ${loss_sum:.0f}",
        })

    # ── 8. Max Drawdown ──
    if "net_pnl_usd" in closed.columns:
        pnl_series = pd.to_numeric(closed["net_pnl_usd"], errors="coerce")
        cum = pnl_series.cumsum()
        dd = cum - cum.cummax()
        max_dd = dd.min()
        max_dd_pct = abs(max_dd) / strategy.MARGIN * 100 if strategy.MARGIN > 0 else 0
        checks.append({
            "name": "Max Drawdown",
            "value": f"${max_dd:.2f}",
            "threshold": ">-$500",
            "status": "OK" if max_dd > -500 else ("WARNING" if max_dd > -800 else "ALERT"),
            "detail": f"{max_dd_pct:.1f}% of margin",
        })

    # ── 整體評估 ──
    alert_count = sum(1 for c in checks if c["status"] == "ALERT")
    warning_count = sum(1 for c in checks if c["status"] == "WARNING")

    if alert_count >= 2:
        overall = "PAUSE"
    elif alert_count >= 1 or warning_count >= 3:
        overall = "WARNING"
    else:
        overall = "NORMAL"

    summary_lines = [f"Health Check ({days}d): {overall}"]
    summary_lines.append(f"Trades: {n_closed} | Checks: {len(checks)}")
    for c in checks:
        mark = "v" if c["status"] == "OK" else ("!" if c["status"] == "WARNING" else "X")
        summary_lines.append(f"  [{mark}] {c['name']}: {c['value']} (threshold: {c['threshold']}) — {c['detail']}")

    return {
        "overall": overall,
        "checks": checks,
        "summary": "\n".join(summary_lines),
    }


def main():
    parser = argparse.ArgumentParser(description="ETH Strategy Health Check")
    parser.add_argument("--days", type=int, default=30, help="Look-back days")
    parser.add_argument("--telegram", action="store_true", help="Send to Telegram")
    args = parser.parse_args()

    result = check_health(args.days)
    print(result["summary"])

    if args.telegram:
        from telegram_notify import send_telegram_message
        msg = f"<b>Health: {result['overall']}</b>\n<pre>{result['summary']}</pre>"
        send_telegram_message(msg)


if __name__ == "__main__":
    main()
