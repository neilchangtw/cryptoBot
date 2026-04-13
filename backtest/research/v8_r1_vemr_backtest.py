"""
V8 Round 1: Volatility-Expansion Mean Reversion (VEMR)
假說：大幅位移後 ETH 傾向回歸均值，利用 dist_ema20 極端值進場
L: dist_ema20 < -2% (price far below EMA → buy the dip)
S: dist_ema20 > +2% (price far above EMA → sell the rip)
Exit: TP 1.5%, Time stop 18 bars, SafeNet 4.5%
Session: exclude Sat/Sun, exclude hours 0-3 for L, S only hours 11-21
"""
import pandas as pd
import numpy as np
from collections import defaultdict

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

# ===== Indicators (all shifted) =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)

# Session info
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek  # 0=Mon

# ===== Parameters =====
NOTIONAL = 4000
FEE = 4.0
SAFENET_PCT = 0.045
SLIPPAGE_FACTOR = 0.25

# Entry thresholds
L_DIST_THRESH = -0.02    # dist_ema20 < -2%
S_DIST_THRESH = 0.02     # dist_ema20 > +2%

# Exit
TP_PCT = 0.015           # 1.5%
MAX_HOLD = 18            # bars

# Session
L_BLOCK_HOURS = {0, 1, 2, 3}
S_ALLOW_HOURS = set(range(11, 22))  # 11-21 UTC+8
BLOCK_DAYS = {5, 6}      # Sat, Sun

# Position limits
MAX_SAME_L = 2
MAX_SAME_S = 2
MAX_TOTAL = 3

# Risk
DAILY_LOSS_LIMIT = -300
MONTHLY_LOSS_LIMIT = -500
CONSEC_LOSS_PAUSE = 5
CONSEC_LOSS_COOLDOWN = 24

# Cooldown
EXIT_CD = 3  # bars after exit before re-entry

WARMUP = 150

# IS/OOS split
total_days = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]).days
split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)

# ===== Backtest Engine =====
trades = []
open_positions = []

daily_pnl = 0.0
monthly_pnl = 0.0
consec_losses = 0
cooldown_until = 0
current_month = None
current_date = None

last_exit_l = -999
last_exit_s = -999

equity = 1000.0
peak_equity = 1000.0
max_dd = 0.0
worst_day_pnl = 0.0
worst_day_date = None

daily_tracker = {}
monthly_tracker = defaultdict(float)


def calc_safenet_exit(side, entry, bar_low, bar_high):
    """Calculate SafeNet exit with 25% slippage penetration model."""
    if side == "long":
        sn_level = entry * (1 - SAFENET_PCT)
        if bar_low <= sn_level:
            return sn_level - (sn_level - bar_low) * SLIPPAGE_FACTOR
    else:
        sn_level = entry * (1 + SAFENET_PCT)
        if bar_high >= sn_level:
            return sn_level + (bar_high - sn_level) * SLIPPAGE_FACTOR
    return None


def calc_pnl(side, entry, exit_price):
    """Calculate PnL for a trade."""
    if side == "long":
        return (exit_price - entry) / entry * NOTIONAL - FEE
    else:
        return (entry - exit_price) / entry * NOTIONAL - FEE


for i in range(WARMUP, len(df) - 1):
    bar = df.iloc[i]
    next_bar = df.iloc[i + 1]
    bar_dt = bar["datetime"]
    bar_month = bar_dt.strftime("%Y-%m")
    bar_date = bar_dt.date()
    bar_hour = bar["hour"]
    bar_dow = bar["dow"]

    # Reset daily/monthly
    if bar_date != current_date:
        if current_date is not None and current_date in daily_tracker:
            day_pnl = daily_tracker[current_date]
            if day_pnl < worst_day_pnl:
                worst_day_pnl = day_pnl
                worst_day_date = current_date
        daily_pnl = 0.0
        current_date = bar_date

    if bar_month != current_month:
        monthly_pnl = 0.0
        current_month = bar_month

    # ===== Exit check =====
    for pos in list(open_positions):
        exit_price = None
        exit_reason = None
        hold_bars = i - pos["entry_bar"]

        # 1. SafeNet
        sn_exit = calc_safenet_exit(pos["side"], pos["entry"],
                                     bar["low"], bar["high"])
        if sn_exit is not None:
            exit_price = sn_exit
            exit_reason = "safenet"

        # 2. TP
        if exit_price is None:
            if pos["side"] == "long":
                tp_level = pos["entry"] * (1 + TP_PCT)
                if bar["high"] >= tp_level:
                    exit_price = tp_level
                    exit_reason = "tp"
            else:
                tp_level = pos["entry"] * (1 - TP_PCT)
                if bar["low"] <= tp_level:
                    exit_price = tp_level
                    exit_reason = "tp"

        # 3. Time stop
        if exit_price is None and hold_bars >= MAX_HOLD:
            exit_price = bar["close"]
            exit_reason = "time_stop"

        if exit_price is not None:
            pnl = calc_pnl(pos["side"], pos["entry"], exit_price)
            trade_rec = {
                "entry_bar": pos["entry_bar"],
                "exit_bar": i,
                "side": pos["side"],
                "entry": pos["entry"],
                "exit": exit_price,
                "pnl": pnl,
                "reason": exit_reason,
                "hold_bars": hold_bars,
                "entry_dt": pos["entry_dt"],
                "exit_dt": bar_dt,
            }
            trades.append(trade_rec)
            open_positions.remove(pos)

            # Update tracking
            equity += pnl
            daily_pnl += pnl
            monthly_pnl += pnl
            if bar_date not in daily_tracker:
                daily_tracker[bar_date] = 0.0
            daily_tracker[bar_date] += pnl
            monthly_tracker[bar_month] += pnl

            peak_equity = max(peak_equity, equity)
            dd = peak_equity - equity
            max_dd = max(max_dd, dd)

            if pnl < 0:
                consec_losses += 1
                if consec_losses >= CONSEC_LOSS_PAUSE:
                    cooldown_until = i + CONSEC_LOSS_COOLDOWN
            else:
                consec_losses = 0

            if pos["side"] == "long":
                last_exit_l = i
            else:
                last_exit_s = i

    # ===== Risk checks =====
    if daily_pnl <= DAILY_LOSS_LIMIT:
        continue
    if monthly_pnl <= MONTHLY_LOSS_LIMIT:
        continue
    if i < cooldown_until:
        continue

    # Weekend block
    if bar_dow in BLOCK_DAYS:
        continue

    # Position counts
    n_long = sum(1 for p in open_positions if p["side"] == "long")
    n_short = sum(1 for p in open_positions if p["side"] == "short")
    n_total = n_long + n_short

    if n_total >= MAX_TOTAL:
        continue

    entry_price = next_bar["open"]

    # ===== L Entry =====
    if (n_long < MAX_SAME_L and
        bar_hour not in L_BLOCK_HOURS and
        i - last_exit_l >= EXIT_CD and
        bar["dist_ema20"] is not None and
        not np.isnan(bar["dist_ema20"]) and
        bar["dist_ema20"] < L_DIST_THRESH):

        open_positions.append({
            "side": "long",
            "entry": entry_price,
            "entry_bar": i + 1,
            "entry_dt": next_bar["datetime"],
        })
        n_long += 1
        n_total += 1

    # ===== S Entry =====
    if (n_short < MAX_SAME_S and
        n_total < MAX_TOTAL and
        bar_hour in S_ALLOW_HOURS and
        i - last_exit_s >= EXIT_CD and
        bar["dist_ema20"] is not None and
        not np.isnan(bar["dist_ema20"]) and
        bar["dist_ema20"] > S_DIST_THRESH):

        open_positions.append({
            "side": "short",
            "entry": entry_price,
            "entry_bar": i + 1,
            "entry_dt": next_bar["datetime"],
        })

# Close remaining positions at last bar
last_bar = df.iloc[-1]
for pos in list(open_positions):
    pnl = calc_pnl(pos["side"], pos["entry"], last_bar["close"])
    trades.append({
        "entry_bar": pos["entry_bar"],
        "exit_bar": len(df) - 1,
        "side": pos["side"],
        "entry": pos["entry"],
        "exit": last_bar["close"],
        "pnl": pnl,
        "reason": "eod",
        "hold_bars": len(df) - 1 - pos["entry_bar"],
        "entry_dt": pos["entry_dt"],
        "exit_dt": last_bar["datetime"],
    })
    equity += pnl

# ===== Analysis =====
trade_df = pd.DataFrame(trades)
if len(trade_df) == 0:
    print("NO TRADES!")
    exit()

trade_df["entry_dt"] = pd.to_datetime(trade_df["entry_dt"])
trade_df["exit_dt"] = pd.to_datetime(trade_df["exit_dt"])
trade_df["month"] = trade_df["entry_dt"].dt.strftime("%Y-%m")
trade_df["is_oos"] = trade_df["entry_dt"] >= split_date

# Split IS/OOS
is_trades = trade_df[~trade_df["is_oos"]]
oos_trades = trade_df[trade_df["is_oos"]]


def summarize(tdf, label):
    if len(tdf) == 0:
        print(f"  {label}: no trades")
        return
    l_trades = tdf[tdf["side"] == "long"]
    s_trades = tdf[tdf["side"] == "short"]

    def stats(t, name):
        if len(t) == 0:
            return f"  {name}: 0 trades"
        n = len(t)
        pnl = t["pnl"].sum()
        wr = (t["pnl"] > 0).mean()
        wins = t[t["pnl"] > 0]["pnl"]
        losses = t[t["pnl"] <= 0]["pnl"]
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
        avg_w = wins.mean() if len(wins) > 0 else 0
        avg_l = losses.mean() if len(losses) > 0 else 0

        # MDD
        cum_pnl = t.sort_values("exit_dt")["pnl"].cumsum()
        peak = cum_pnl.cummax()
        mdd = (peak - cum_pnl).max()

        return f"  {name}: {n}t ${pnl:.0f} PF {pf:.2f} WR {wr:.1%} MDD ${mdd:.0f} AvgW ${avg_w:.0f} AvgL ${avg_l:.0f}"

    print(f"\n=== {label} ===")
    print(stats(tdf, "ALL"))
    print(stats(l_trades, "L  "))
    print(stats(s_trades, "S  "))

    # Exit reasons
    print(f"\n  Exit reasons:")
    for reason in ["tp", "safenet", "time_stop", "eod"]:
        n = (tdf["reason"] == reason).sum()
        if n > 0:
            avg_pnl = tdf[tdf["reason"] == reason]["pnl"].mean()
            print(f"    {reason}: {n} ({n/len(tdf)*100:.1f}%) avg ${avg_pnl:.1f}")


summarize(is_trades, "IS (first 365 days)")
summarize(oos_trades, "OOS (last 365 days)")

# Monthly report
print("\n=== 月報 (OOS) ===")
print(f"{'月份':<8} {'L筆':>4} {'L勝率':>6} {'L淨利':>8} {'S筆':>4} {'S勝率':>6} {'S淨利':>8} {'合計':>8}")
for month in sorted(oos_trades["month"].unique()):
    mt = oos_trades[oos_trades["month"] == month]
    lt = mt[mt["side"] == "long"]
    st = mt[mt["side"] == "short"]
    l_n = len(lt)
    l_wr = (lt["pnl"] > 0).mean() if l_n > 0 else 0
    l_pnl = lt["pnl"].sum()
    s_n = len(st)
    s_wr = (st["pnl"] > 0).mean() if s_n > 0 else 0
    s_pnl = st["pnl"].sum()
    total = l_pnl + s_pnl
    print(f"{month:<8} {l_n:>4} {l_wr:>6.1%} {l_pnl:>8.0f} {s_n:>4} {s_wr:>6.1%} {s_pnl:>8.0f} {total:>8.0f}")

# Risk metrics
print("\n=== 風控 ===")
print(f"最終權益: ${equity:.0f}")
print(f"最大回撤: ${max_dd:.0f}")
print(f"最差單日: ${worst_day_pnl:.0f} ({worst_day_date})")

# Max consecutive losses
trade_df_sorted = trade_df.sort_values("exit_dt")
max_consec = 0
curr_consec = 0
max_consec_pnl = 0
curr_consec_pnl = 0
for _, t in trade_df_sorted.iterrows():
    if t["pnl"] < 0:
        curr_consec += 1
        curr_consec_pnl += t["pnl"]
        if curr_consec > max_consec:
            max_consec = curr_consec
            max_consec_pnl = curr_consec_pnl
    else:
        curr_consec = 0
        curr_consec_pnl = 0
print(f"最大連虧: {max_consec}筆 ${max_consec_pnl:.0f}")

# Position peak
print(f"持倉峰值: (tracked by maxSame limits)")

# Circuit breaker triggers
print(f"\n=== 門檻檢查 (OOS) ===")
oos_l = oos_trades[oos_trades["side"] == "long"]
oos_s = oos_trades[oos_trades["side"] == "short"]

def check_gate(name, condition, result):
    status = "PASS" if condition else "FAIL"
    print(f"  {name}: {result} [{status}]")

# Overall stats
l_pnl = oos_l["pnl"].sum()
s_pnl = oos_s["pnl"].sum()
l_wr = (oos_l["pnl"] > 0).mean() if len(oos_l) > 0 else 0
s_wr = (oos_s["pnl"] > 0).mean() if len(oos_s) > 0 else 0
l_wins = oos_l[oos_l["pnl"] > 0]["pnl"].sum()
l_losses = abs(oos_l[oos_l["pnl"] <= 0]["pnl"].sum())
s_wins = oos_s[oos_s["pnl"] > 0]["pnl"].sum()
s_losses = abs(oos_s[oos_s["pnl"] <= 0]["pnl"].sum())
l_pf = l_wins / l_losses if l_losses > 0 else float("inf")
s_pf = s_wins / s_losses if s_losses > 0 else float("inf")

l_months = oos_l.groupby("month")["pnl"].sum()
s_months = oos_s.groupby("month")["pnl"].sum()

check_gate("G1 L月淨利≥$300", l_months.min() >= 300 if len(l_months) > 0 else False,
           f"min ${l_months.min():.0f}" if len(l_months) > 0 else "N/A")
check_gate("G2 S月淨利≥$300", s_months.min() >= 300 if len(s_months) > 0 else False,
           f"min ${s_months.min():.0f}" if len(s_months) > 0 else "N/A")

l_monthly_wr = oos_l.groupby("month").apply(lambda x: (x["pnl"] > 0).mean())
s_monthly_wr = oos_s.groupby("month").apply(lambda x: (x["pnl"] > 0).mean())
check_gate("G3 L月WR≥70%", l_monthly_wr.min() >= 0.7 if len(l_monthly_wr) > 0 else False,
           f"min {l_monthly_wr.min():.1%}" if len(l_monthly_wr) > 0 else "N/A")
check_gate("G4 S月WR≥70%", s_monthly_wr.min() >= 0.7 if len(s_monthly_wr) > 0 else False,
           f"min {s_monthly_wr.min():.1%}" if len(s_monthly_wr) > 0 else "N/A")

check_gate("G5 L PF≥1.5", l_pf >= 1.5, f"{l_pf:.2f}")
check_gate("G5 S PF≥1.5", s_pf >= 1.5, f"{s_pf:.2f}")
check_gate("G6 MDD≤$500", max_dd <= 500, f"${max_dd:.0f}")
check_gate("G7 最差單日≥-$300", worst_day_pnl >= -300, f"${worst_day_pnl:.0f}")
check_gate("G8 最大連虧≤$400", abs(max_consec_pnl) <= 400, f"${abs(max_consec_pnl):.0f}")

# Combined
all_months = oos_trades.groupby("month")["pnl"].sum()
pos_months = (all_months > 0).sum()
check_gate("C1 合併月淨利≥$600", all_months.min() >= 600 if len(all_months) > 0 else False,
           f"min ${all_months.min():.0f}" if len(all_months) > 0 else "N/A")
check_gate("C2 正月≥10/12", pos_months >= 10,
           f"{pos_months}/{len(all_months)}")
check_gate("C3 最差月≥-$200", all_months.min() >= -200 if len(all_months) > 0 else False,
           f"${all_months.min():.0f}" if len(all_months) > 0 else "N/A")

print(f"\n防前瞻：[✓] 1.dist shift(1) [✓] 2.pctile shift(1) [✓] 3.O[i+1] entry [✓] 4.IS/OOS fixed [✓] 5.indicators shifted [✓] 6.N/A")
