"""
V9 Round 2: Signal Ensemble
Combine the 3 best V8 signals into ensemble strategies:
1. BTC-ETH RelDiv (best PnL)
2. Volume Spike + dist_ema20 (most robust)
3. Momentum Continuation (best L side)

Approach: Use different signals for L and S (asymmetric strategy)
"""
import pandas as pd
import numpy as np
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

eth = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
btc = pd.read_csv("data/BTCUSDT_1h_latest730d.csv")
eth["datetime"] = pd.to_datetime(eth["datetime"])
btc["datetime"] = pd.to_datetime(btc["datetime"])

btc_cols = btc[["datetime", "close", "volume"]].rename(
    columns={"close": "btc_close", "volume": "btc_volume"})
df = eth.merge(btc_cols, on="datetime", how="left")

# ===== All indicators =====
df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)
df["roc_5"] = df["close"].pct_change(5).shift(1)
df["roc_10"] = df["close"].pct_change(10).shift(1)
df["roc_20"] = df["close"].pct_change(20).shift(1)

for n in [3, 5, 7]:
    df[f"eth_ret{n}"] = df["close"].pct_change(n).shift(1)
    df[f"btc_ret{n}"] = df["btc_close"].pct_change(n).shift(1)
    df[f"rel_ret{n}"] = df[f"eth_ret{n}"] - df[f"btc_ret{n}"]

df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek

NOTIONAL = 4000; FEE = 4.0; SAFENET_PCT = 0.045; SLIPPAGE_FACTOR = 0.25
WARMUP = 150
split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)
BLOCK_DAYS = {5, 6}


def run_backtest(long_signal_func, short_signal_func,
                 tp_pct, sl_pct, max_hold,
                 max_same_l=2, max_same_s=2, max_total=3, exit_cd=3,
                 l_block_hours=None, s_allow_hours=None):
    if l_block_hours is None: l_block_hours = {0,1,2,3}
    if s_allow_hours is None: s_allow_hours = set(range(11,22))

    trades = []; open_positions = []
    equity = 1000.0; peak_equity = 1000.0; max_dd = 0.0
    daily_pnl = 0.0; monthly_pnl = 0.0; consec_losses = 0; cooldown_until = 0
    current_month = None; current_date = None
    last_exit_l = -999; last_exit_s = -999
    worst_day_pnl = 0.0; worst_day_date = None; daily_tracker = {}

    for i in range(WARMUP, len(df) - 1):
        bar = df.iloc[i]; next_bar = df.iloc[i+1]
        bar_dt = bar["datetime"]; bar_month = bar_dt.strftime("%Y-%m"); bar_date = bar_dt.date()

        if bar_date != current_date:
            if current_date and current_date in daily_tracker:
                if daily_tracker[current_date] < worst_day_pnl:
                    worst_day_pnl = daily_tracker[current_date]; worst_day_date = current_date
            daily_pnl = 0.0; current_date = bar_date
        if bar_month != current_month: monthly_pnl = 0.0; current_month = bar_month

        for pos in list(open_positions):
            exit_price = None; exit_reason = None; hold_bars = i - pos["entry_bar"]
            if pos["side"] == "long":
                sn = pos["entry"]*(1-SAFENET_PCT)
                if bar["low"]<=sn: exit_price=sn-(sn-bar["low"])*SLIPPAGE_FACTOR; exit_reason="safenet"
            else:
                sn = pos["entry"]*(1+SAFENET_PCT)
                if bar["high"]>=sn: exit_price=sn+(bar["high"]-sn)*SLIPPAGE_FACTOR; exit_reason="safenet"
            if exit_price is None:
                if pos["side"]=="long":
                    if bar["low"]<=pos["entry"]*(1-sl_pct): exit_price=pos["entry"]*(1-sl_pct); exit_reason="sl"
                else:
                    if bar["high"]>=pos["entry"]*(1+sl_pct): exit_price=pos["entry"]*(1+sl_pct); exit_reason="sl"
            if exit_price is None:
                if pos["side"]=="long":
                    if bar["high"]>=pos["entry"]*(1+tp_pct): exit_price=pos["entry"]*(1+tp_pct); exit_reason="tp"
                else:
                    if bar["low"]<=pos["entry"]*(1-tp_pct): exit_price=pos["entry"]*(1-tp_pct); exit_reason="tp"
            if exit_price is None and hold_bars>=max_hold: exit_price=bar["close"]; exit_reason="time_stop"

            if exit_price is not None:
                pnl = ((exit_price-pos["entry"])/pos["entry"] if pos["side"]=="long" else (pos["entry"]-exit_price)/pos["entry"])*NOTIONAL-FEE
                trades.append({"entry_bar":pos["entry_bar"],"exit_bar":i,"side":pos["side"],"entry":pos["entry"],"exit":exit_price,"pnl":pnl,"reason":exit_reason,"hold_bars":hold_bars,"entry_dt":pos["entry_dt"],"exit_dt":bar_dt})
                open_positions.remove(pos)
                equity+=pnl; daily_pnl+=pnl; monthly_pnl+=pnl
                if bar_date not in daily_tracker: daily_tracker[bar_date]=0.0
                daily_tracker[bar_date]+=pnl
                peak_equity=max(peak_equity,equity); max_dd=max(max_dd,peak_equity-equity)
                if pnl<0: consec_losses+=1; (cooldown_until:=i+24) if consec_losses>=5 else None
                else: consec_losses=0
                if pos["side"]=="long": last_exit_l=i
                else: last_exit_s=i

        if daily_pnl<=-300 or monthly_pnl<=-500 or i<cooldown_until: continue
        if bar["dow"] in BLOCK_DAYS: continue
        n_long=sum(1 for p in open_positions if p["side"]=="long")
        n_short=sum(1 for p in open_positions if p["side"]=="short")
        if n_long+n_short>=max_total: continue

        entry_price=next_bar["open"]
        if n_long<max_same_l and bar["hour"] not in l_block_hours and i-last_exit_l>=exit_cd and long_signal_func(bar,i):
            open_positions.append({"side":"long","entry":entry_price,"entry_bar":i+1,"entry_dt":next_bar["datetime"]})
            n_long+=1
        if n_short<max_same_s and n_long+n_short<max_total and bar["hour"] in s_allow_hours and i-last_exit_s>=exit_cd and short_signal_func(bar,i):
            open_positions.append({"side":"short","entry":entry_price,"entry_bar":i+1,"entry_dt":next_bar["datetime"]})

    for pos in open_positions:
        pnl = ((df.iloc[-1]["close"]-pos["entry"])/pos["entry"] if pos["side"]=="long" else (pos["entry"]-df.iloc[-1]["close"])/pos["entry"])*NOTIONAL-FEE
        trades.append({"entry_bar":pos["entry_bar"],"exit_bar":len(df)-1,"side":pos["side"],"entry":pos["entry"],"exit":df.iloc[-1]["close"],"pnl":pnl,"reason":"eod","hold_bars":len(df)-1-pos["entry_bar"],"entry_dt":pos["entry_dt"],"exit_dt":df.iloc[-1]["datetime"]})

    return pd.DataFrame(trades), max_dd, worst_day_pnl, worst_day_date


def compact_report(tdf, name, mdd):
    if len(tdf)==0: print(f"  [{name}] NO TRADES"); return
    tdf=tdf.copy(); tdf["entry_dt"]=pd.to_datetime(tdf["entry_dt"])
    oos=tdf[tdf["entry_dt"]>=split_date]; iis=tdf[tdf["entry_dt"]<split_date]
    if len(oos)==0: print(f"  [{name}] No OOS"); return
    n=len(oos); pnl=oos["pnl"].sum(); is_pnl=iis["pnl"].sum(); wr=(oos["pnl"]>0).mean()
    l=oos[oos["side"]=="long"]; s=oos[oos["side"]=="short"]
    w=oos[oos["pnl"]>0]["pnl"]; lo=oos[oos["pnl"]<=0]["pnl"]
    pf=w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
    l_pnl=l["pnl"].sum() if len(l)>0 else 0; s_pnl=s["pnl"].sum() if len(s)>0 else 0
    l_wr=(l["pnl"]>0).mean() if len(l)>0 else 0; s_wr=(s["pnl"]>0).mean() if len(s)>0 else 0
    flag=" **IS+OOS+" if is_pnl>0 and pnl>0 else ""
    print(f"  [{name}] {n}t OOS${pnl:.0f} IS${is_pnl:.0f} WR{wr:.0%} PF{pf:.2f} L:{len(l)}t${l_pnl:.0f}/{l_wr:.0%} S:{len(s)}t${s_pnl:.0f}/{s_wr:.0%} MDD${mdd:.0f}{flag}")


# ===================================================================
print("=" * 80)
print("V9 Round 2: Signal Ensemble")
print("=" * 80)

# --- Ensemble 1: OR combination (any signal fires) ---
print("\n--- E1: OR Ensemble (any signal triggers entry) ---")
for tp, sl in [(0.02, 0.03), (0.025, 0.035), (0.02, 0.035)]:
    for mh in [12, 18]:
        name = f"OR_all tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
        tdf, mdd, _, _ = run_backtest(
            long_signal_func=lambda bar, i: (
                (not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] < -0.02) or
                (not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and
                 not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01) or
                (not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.03)
            ),
            short_signal_func=lambda bar, i: (
                (not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] > 0.02) or
                (not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and
                 not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01) or
                (not np.isnan(bar["roc_5"]) and bar["roc_5"] < -0.03)
            ),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        compact_report(tdf, name, mdd)

# --- Ensemble 2: Asymmetric L/S (best signal for each side) ---
print("\n--- E2: Asymmetric (different signal for L vs S) ---")

# L candidates: MomCont (best L side), RelDiv (good L), Vol+dist
# S candidates: RelDiv (best S side), Vol+dist
l_signals = {
    "L_MomCont": lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.03,
    "L_RelDiv": lambda bar, i: not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] < -0.02,
    "L_Vol2Dist": lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01,
    "L_MomOR_RelDiv": lambda bar, i: (
        (not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.03) or
        (not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] < -0.02)
    ),
}
s_signals = {
    "S_RelDiv": lambda bar, i: not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] > 0.02,
    "S_Vol2Dist": lambda bar, i: not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01,
    "S_MomCont": lambda bar, i: not np.isnan(bar["roc_5"]) and bar["roc_5"] < -0.03,
    "S_RelDivOR_Vol": lambda bar, i: (
        (not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] > 0.02) or
        (not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 2.0 and
         not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01)
    ),
}

for l_name, l_sig in l_signals.items():
    for s_name, s_sig in s_signals.items():
        for tp, sl in [(0.02, 0.03), (0.025, 0.035), (0.03, 0.04)]:
            for mh in [12, 18]:
                name = f"{l_name}+{s_name} tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
                tdf, mdd, _, _ = run_backtest(
                    long_signal_func=l_sig, short_signal_func=s_sig,
                    tp_pct=tp, sl_pct=sl, max_hold=mh,
                )
                compact_report(tdf, name, mdd)

# --- Ensemble 3: 2-of-3 AND combination ---
print("\n--- E3: 2-of-3 AND Ensemble ---")
def count_signals_l(bar):
    score = 0
    if not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] < -0.02: score += 1
    if not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] < -0.01: score += 1
    if not np.isnan(bar["roc_5"]) and bar["roc_5"] > 0.02: score += 1
    return score

def count_signals_s(bar):
    score = 0
    if not np.isnan(bar["rel_ret5"]) and bar["rel_ret5"] > 0.02: score += 1
    if not np.isnan(bar["vol_ratio"]) and bar["vol_ratio"] > 1.5 and not np.isnan(bar["dist_ema20"]) and bar["dist_ema20"] > 0.01: score += 1
    if not np.isnan(bar["roc_5"]) and bar["roc_5"] < -0.02: score += 1
    return score

for min_score in [2, 3]:
    for tp, sl in [(0.02, 0.03), (0.025, 0.035), (0.03, 0.04)]:
        for mh in [12, 18]:
            name = f"{min_score}of3 tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
            tdf, mdd, _, _ = run_backtest(
                long_signal_func=lambda bar, i, ms=min_score: count_signals_l(bar) >= ms,
                short_signal_func=lambda bar, i, ms=min_score: count_signals_s(bar) >= ms,
                tp_pct=tp, sl_pct=sl, max_hold=mh,
            )
            compact_report(tdf, name, mdd)

# --- Ensemble 4: Combine with BTC-ETH relative at different lookbacks ---
print("\n--- E4: Multi-lookback RelDiv ---")
for tp, sl in [(0.02, 0.03), (0.025, 0.035), (0.03, 0.04)]:
    for mh in [12, 18]:
        # L: rel_ret3 < -1.5% OR rel_ret7 < -2.5%
        name = f"MultiLB_RelDiv tp{tp*100:.0f}/sl{sl*100:.0f} mh{mh}"
        tdf, mdd, _, _ = run_backtest(
            long_signal_func=lambda bar, i: (
                (not np.isnan(bar["rel_ret3"]) and bar["rel_ret3"] < -0.015) or
                (not np.isnan(bar["rel_ret7"]) and bar["rel_ret7"] < -0.025)
            ),
            short_signal_func=lambda bar, i: (
                (not np.isnan(bar["rel_ret3"]) and bar["rel_ret3"] > 0.015) or
                (not np.isnan(bar["rel_ret7"]) and bar["rel_ret7"] > 0.025)
            ),
            tp_pct=tp, sl_pct=sl, max_hold=mh,
        )
        compact_report(tdf, name, mdd)

print("\n" + "=" * 80)
print("V9 R2 Complete")
print("=" * 80)
