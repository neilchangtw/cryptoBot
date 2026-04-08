"""
回測 vs 實盤對比工具

用 strategy.py 跑歷史資料模擬 + live trades.csv：
  - 按時間 matching（±1 bar 容差）
  - 比較 entry price、exit type、PnL
  - 輸出信號一致率、平均價格落差、最大單筆落差

用法：python compare_backtest.py [--update-trades]
      --update-trades: 自動填入 trades.csv 的 backtest_* 欄位
"""
import os
import sys
import argparse
from datetime import timedelta

import pandas as pd
import numpy as np

# 加入專案根目錄到 path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import strategy
import recorder


def load_backtest_trades() -> pd.DataFrame:
    """
    用 strategy.py 跑歷史回測，產生參考交易列表。

    Returns:
        DataFrame with columns: [pnl, tp, sd, bars, dt]
    """
    csv_path = os.path.join(BASE_DIR, "data", "ETHUSDT_1h_latest730d.csv")
    if not os.path.exists(csv_path):
        print(f"[ERROR] Data file not found: {csv_path}")
        return pd.DataFrame()

    df_raw = pd.read_csv(csv_path)
    df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        df_raw[c] = pd.to_numeric(df_raw[c], errors="coerce")

    df = strategy.compute_indicators(df_raw)
    w = strategy.WARMUP_BARS
    N = len(df)

    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    O = df["open"].values
    E = df["ema20"].values
    D = df["datetime"].values

    lp = []
    sp = []
    trades = []
    last_exits = {"long": -9999, "short": -9999}

    for i in range(w, N - 1):
        # 出場
        nl = []
        for p in lp:
            exit_result = strategy.check_exit(
                "long", p["e"], p["ei"], i, H[i], L[i], C[i], E[i])
            if exit_result["exit"]:
                pnl_usd, _ = strategy.compute_pnl(p["e"], exit_result["exit_price"], "long")
                trades.append({"pnl": pnl_usd, "tp": exit_result["reason"],
                               "sd": "long", "bars": i - p["ei"], "dt": D[i]})
                last_exits["long"] = i
            else:
                nl.append(p)
        lp = nl

        ns = []
        for p in sp:
            exit_result = strategy.check_exit(
                "short", p["e"], p["ei"], i, H[i], L[i], C[i], E[i])
            if exit_result["exit"]:
                pnl_usd, _ = strategy.compute_pnl(p["e"], exit_result["exit_price"], "short")
                trades.append({"pnl": pnl_usd, "tp": exit_result["reason"],
                               "sd": "short", "bars": i - p["ei"], "dt": D[i]})
                last_exits["short"] = i
            else:
                ns.append(p)
        sp = ns

        # 進場
        gp_v = df.iloc[i]["gk_pctile"]
        if np.isnan(gp_v):
            continue

        bl_v = bool(df.iloc[i]["breakout_long"]) if not pd.isna(df.iloc[i]["breakout_long"]) else False
        bs_v = bool(df.iloc[i]["breakout_short"]) if not pd.isna(df.iloc[i]["breakout_short"]) else False
        sok_v = bool(df.iloc[i]["session_ok"])
        cond = gp_v < strategy.GK_THRESH

        gp_p = df.iloc[i]["gk_pctile_prev"]
        bl_p = df.iloc[i]["bl_prev"]
        bs_p = df.iloc[i]["bs_prev"]
        sok_p = df.iloc[i]["sok_prev"]

        if not pd.isna(gp_p):
            pc = gp_p < strategy.GK_THRESH
            pbl = bool(bl_p) if not pd.isna(bl_p) else False
            pbs = bool(bs_p) if not pd.isna(bs_p) else False
            ps = bool(sok_p)
        else:
            pc = pbl = pbs = ps = False

        fl = not (pc and pbl and ps)
        fs = not (pc and pbs and ps)
        long_cool = (i - last_exits["long"]) >= strategy.EXIT_COOLDOWN
        short_cool = (i - last_exits["short"]) >= strategy.EXIT_COOLDOWN

        if cond and bl_v and sok_v and fl and long_cool and len(lp) < strategy.MAX_SAME:
            lp.append({"e": O[i + 1], "ei": i})
        if cond and bs_v and sok_v and fs and short_cool and len(sp) < strategy.MAX_SAME:
            sp.append({"e": O[i + 1], "ei": i})

    result = pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl", "tp", "sd", "bars", "dt"])
    result["dt"] = pd.to_datetime(result["dt"])
    return result


def load_live_trades() -> pd.DataFrame:
    """載入 live trades.csv"""
    if not os.path.exists(recorder.TRADES_CSV):
        print("[INFO] No live trades found")
        return pd.DataFrame()
    df = pd.read_csv(recorder.TRADES_CSV)
    for col in ["entry_time_utc8", "exit_time_utc8"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def estimate_backtest_entry_time(bt_row, df_computed) -> pd.Timestamp:
    """
    回測只記錄出場時間，反推進場時間：
    entry_time = exit_time - hold_bars hours
    """
    exit_dt = bt_row["dt"]
    bars = bt_row["bars"]
    return exit_dt - timedelta(hours=bars)


def match_trades(bt_trades: pd.DataFrame, live_trades: pd.DataFrame,
                 tolerance_hours: int = 2) -> list:
    """
    按時間匹配回測和實盤交易。

    匹配規則：
      1. 同方向 (long/short)
      2. 進場時間差 <= tolerance_hours
    """
    if len(bt_trades) == 0 or len(live_trades) == 0:
        return []

    bt = bt_trades.copy()
    bt["entry_time_est"] = bt.apply(
        lambda r: r["dt"] - timedelta(hours=r["bars"]), axis=1
    )

    live = live_trades.copy()
    if "direction" not in live.columns:
        return []
    live["direction_lower"] = live["direction"].str.lower()

    matches = []
    used_live = set()

    for bt_idx, bt_row in bt.iterrows():
        bt_dir = bt_row["sd"]
        bt_entry = bt_row["entry_time_est"]

        best_match = None
        best_diff = float("inf")

        for live_idx, live_row in live.iterrows():
            if live_idx in used_live:
                continue
            if live_row["direction_lower"] != bt_dir:
                continue

            live_entry = live_row.get("entry_time_utc8")
            if pd.isna(live_entry):
                continue

            diff_hours = abs((live_entry - bt_entry).total_seconds()) / 3600
            if diff_hours <= tolerance_hours and diff_hours < best_diff:
                best_diff = diff_hours
                best_match = live_idx

        if best_match is not None:
            live_row = live.loc[best_match]
            live_pnl = pd.to_numeric(live_row.get("net_pnl_usd", 0), errors="coerce")
            if pd.isna(live_pnl):
                live_pnl = 0

            matches.append({
                "bt_idx": bt_idx,
                "live_idx": best_match,
                "bt_entry_time": bt_entry,
                "live_entry_time": live_row["entry_time_utc8"],
                "time_diff_hours": best_diff,
                "direction": bt_dir,
                "bt_pnl": bt_row["pnl"],
                "live_pnl": live_pnl,
                "pnl_diff": live_pnl - bt_row["pnl"],
                "bt_exit_type": bt_row["tp"],
                "live_exit_type": live_row.get("exit_type", ""),
                "exit_type_match": bt_row["tp"] == live_row.get("exit_type", ""),
                "live_entry_price": pd.to_numeric(live_row.get("entry_price", 0), errors="coerce"),
                "bt_hold_bars": bt_row["bars"],
                "live_trade_id": live_row.get("trade_id", ""),
            })
            used_live.add(best_match)

    return matches


def compute_comparison_stats(matches: list, bt_trades: pd.DataFrame,
                             live_trades: pd.DataFrame) -> dict:
    """計算對比統計"""
    n_bt = len(bt_trades)
    n_live = len(live_trades)
    n_matched = len(matches)

    if n_matched == 0:
        return {
            "bt_trades": n_bt,
            "live_trades": n_live,
            "matched": 0,
            "signal_match_rate": 0,
            "summary": "No matching trades found",
        }

    pnl_diffs = [m["pnl_diff"] for m in matches]
    exit_matches = sum(1 for m in matches if m["exit_type_match"])
    time_diffs = [m["time_diff_hours"] for m in matches]

    stats = {
        "bt_trades": n_bt,
        "live_trades": n_live,
        "matched": n_matched,
        "unmatched_live": n_live - n_matched,
        "signal_match_rate": n_matched / max(n_live, 1) * 100,
        "exit_type_match_rate": exit_matches / n_matched * 100,
        "avg_pnl_diff": np.mean(pnl_diffs),
        "max_pnl_diff": max(pnl_diffs, key=abs),
        "avg_time_diff_hours": np.mean(time_diffs),
        "bt_total_pnl": sum(m["bt_pnl"] for m in matches),
        "live_total_pnl": sum(m["live_pnl"] for m in matches),
    }

    return stats


def format_report(stats: dict, matches: list) -> str:
    """���式化報告"""
    lines = []
    lines.append("=" * 60)
    lines.append("  Backtest vs Live Comparison Report")
    lines.append("=" * 60)
    lines.append(f"  Backtest trades: {stats['bt_trades']}")
    lines.append(f"  Live trades: {stats['live_trades']}")
    lines.append(f"  Matched: {stats['matched']}")
    lines.append(f"  Unmatched live: {stats.get('unmatched_live', 'N/A')}")
    lines.append("")

    if stats["matched"] > 0:
        lines.append(f"  Signal match rate: {stats['signal_match_rate']:.1f}%")
        lines.append(f"  Exit type match rate: {stats['exit_type_match_rate']:.1f}%")
        lines.append(f"  Avg PnL diff: ${stats['avg_pnl_diff']:.2f}")
        lines.append(f"  Max PnL diff: ${stats['max_pnl_diff']:.2f}")
        lines.append(f"  Avg time diff: {stats['avg_time_diff_hours']:.2f}h")
        lines.append(f"  BT matched PnL: ${stats['bt_total_pnl']:.2f}")
        lines.append(f"  Live matched PnL: ${stats['live_total_pnl']:.2f}")

        lines.append("")
        lines.append("  Trade-by-trade comparison:")
        lines.append(f"  {'Dir':<6s} {'BT Entry':<18s} {'Live Entry':<18s} "
                     f"{'dt':>5s} {'BT PnL':>10s} {'Live PnL':>10s} {'Diff':>8s} {'Exit Match':>10s}")
        lines.append("  " + "-" * 95)

        for m in matches:
            bt_t = m["bt_entry_time"].strftime("%m-%d %H:%M") if pd.notna(m["bt_entry_time"]) else "N/A"
            live_t = m["live_entry_time"].strftime("%m-%d %H:%M") if pd.notna(m["live_entry_time"]) else "N/A"
            em = "Y" if m["exit_type_match"] else f"N({m['bt_exit_type']}/{m['live_exit_type']})"
            lines.append(
                f"  {m['direction']:<6s} {bt_t:<18s} {live_t:<18s} "
                f"{m['time_diff_hours']:>5.1f} ${m['bt_pnl']:>+9.2f} ${m['live_pnl']:>+9.2f} "
                f"${m['pnl_diff']:>+7.2f} {em:>10s}"
            )

    lines.append("")
    return "\n".join(lines)


def update_trades_csv(matches: list, bt_trades: pd.DataFrame):
    """把回測對比結果填入 trades.csv 的 backtest_* 欄位。"""
    if not matches or not os.path.exists(recorder.TRADES_CSV):
        return

    import csv
    rows = []
    with open(recorder.TRADES_CSV, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    match_map = {}
    for m in matches:
        tid = m.get("live_trade_id")
        if tid:
            match_map[tid] = m

    updated = 0
    for row in rows:
        tid = row.get("trade_id", "")
        if tid in match_map:
            m = match_map[tid]
            row["backtest_had_same_trade"] = "YES"
            row["backtest_entry_price"] = ""
            row["backtest_exit_type"] = m["bt_exit_type"]
            row["backtest_pnl_usd"] = f"{m['bt_pnl']:.2f}"
            if not m["exit_type_match"]:
                row["discrepancy_note"] = f"exit_type: BT={m['bt_exit_type']} vs Live={m['live_exit_type']}"
            updated += 1

    with open(recorder.TRADES_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=recorder.TRADES_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Updated {updated} trades in trades.csv with backtest comparison data")


def main():
    parser = argparse.ArgumentParser(description="Backtest vs Live Comparison")
    parser.add_argument("--update-trades", action="store_true",
                        help="Update trades.csv with backtest_* fields")
    parser.add_argument("--tolerance", type=int, default=2,
                        help="Time tolerance for matching (hours)")
    args = parser.parse_args()

    print("Loading backtest trades (GK strategy simulation)...")
    bt_trades = load_backtest_trades()
    if len(bt_trades) == 0:
        print("[ERROR] No backtest trades loaded")
        return

    print(f"  Backtest: {len(bt_trades)} trades")

    print("Loading live trades...")
    live_trades = load_live_trades()
    if len(live_trades) == 0:
        print("[INFO] No live trades to compare")
        return

    # 只比較 live 運行期間的回測交易
    if "entry_time_utc8" in live_trades.columns:
        earliest_live = live_trades["entry_time_utc8"].min()
        if pd.notna(earliest_live):
            bt_trades["entry_est"] = bt_trades.apply(
                lambda r: r["dt"] - timedelta(hours=r["bars"]), axis=1
            )
            bt_overlap = bt_trades[bt_trades["entry_est"] >= earliest_live - timedelta(hours=24)]
            print(f"  Backtest trades in live period: {len(bt_overlap)}")
        else:
            bt_overlap = bt_trades
    else:
        bt_overlap = bt_trades

    print("Matching trades...")
    matches = match_trades(bt_overlap, live_trades, tolerance_hours=args.tolerance)

    stats = compute_comparison_stats(matches, bt_overlap, live_trades)
    report = format_report(stats, matches)
    print(report)

    if args.update_trades and matches:
        update_trades_csv(matches, bt_trades)


if __name__ == "__main__":
    main()
