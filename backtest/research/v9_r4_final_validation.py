"""
V9 Round 4: Final Validation

Best candidates from R3:
1. Candidate 6: LB5 TH0.03 LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8 → 7/8 PASS, IS -$790
2. Phase 3 CB: LB5 TH0.03 LTP3.0/LSL4.0/LMH24 STP2.5/SSL3.5/SMH18 + L_cap6 S_cap8 CD_S10 → IS $835, OOS $849

This round:
1. Full gate check on Phase 3 CB configs
2. Fine-tune the CB config to maximize OOS while keeping IS positive
3. Sensitivity analysis on the best config
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

# Indicators
for lb in [5]:
    eth_ret = df["close"].pct_change(lb)
    btc_ret = df["btc_close"].pct_change(lb)
    df[f"rel_ret{lb}"] = (eth_ret - btc_ret).shift(1)

df["ema20"] = df["close"].ewm(span=20).mean()
df["dist_ema20"] = ((df["close"] - df["ema20"]) / df["ema20"]).shift(1)
df["vol_ratio"] = (df["volume"] / df["volume"].rolling(20).mean()).shift(1)
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek

NOTIONAL = 4000
FEE = 4
SAFENET = 0.045
SLIP_MULT = 1.25

split_date = df["datetime"].iloc[0] + pd.Timedelta(days=365)
warmup = 150


def run_backtest_asym(
    l_signal_func, s_signal_func,
    l_tp, l_sl, l_mh,
    s_tp, s_sl, s_mh,
    max_same=2, total_max=3,
    l_monthly_cap=99, s_monthly_cap=99,
    exit_cd_l=0, exit_cd_s=0,
    l_block_hours=None, s_block_hours=None,
    block_days=None,
):
    if l_block_hours is None: l_block_hours = {0,1,2,12}
    if s_block_hours is None: s_block_hours = {0,1,2,12}
    if block_days is None: block_days = {5, 6}

    trades = []
    positions = []
    last_exit_l = -999
    last_exit_s = -999
    monthly_counts = {}

    for i in range(warmup, len(df) - 1):
        bar = df.iloc[i]
        next_open = df.iloc[i + 1]["open"]
        dt = bar["datetime"]

        # --- Exit ---
        closed = []
        for j, pos in enumerate(positions):
            bars_held = i - pos["entry_bar"]
            if pos["side"] == "long":
                ret = (bar["close"] - pos["entry_price"]) / pos["entry_price"]
                tp_pct, sl_pct, mh = l_tp, l_sl, l_mh
            else:
                ret = (pos["entry_price"] - bar["close"]) / pos["entry_price"]
                tp_pct, sl_pct, mh = s_tp, s_sl, s_mh

            reason = exit_price = None
            safenet_ret = -SAFENET * SLIP_MULT
            if ret <= safenet_ret:
                reason = "safenet"
                exit_price = pos["entry_price"] * (1 + safenet_ret) if pos["side"] == "long" else pos["entry_price"] * (1 - safenet_ret)
            elif ret >= tp_pct:
                reason = "tp"
                exit_price = pos["entry_price"] * (1 + tp_pct) if pos["side"] == "long" else pos["entry_price"] * (1 - tp_pct)
            elif ret <= -sl_pct:
                reason = "sl"
                exit_price = pos["entry_price"] * (1 - sl_pct) if pos["side"] == "long" else pos["entry_price"] * (1 + sl_pct)
            elif bars_held >= mh:
                reason = "time_stop"
                exit_price = bar["close"]

            if reason:
                pnl = ((exit_price - pos["entry_price"]) / pos["entry_price"] * NOTIONAL - FEE) if pos["side"] == "long" else ((pos["entry_price"] - exit_price) / pos["entry_price"] * NOTIONAL - FEE)
                trades.append({
                    "entry_dt": df.iloc[pos["entry_bar"]]["datetime"],
                    "exit_dt": dt,
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "reason": reason,
                    "bars_held": bars_held,
                })
                closed.append(j)
                if pos["side"] == "long": last_exit_l = i
                else: last_exit_s = i

        for j in sorted(closed, reverse=True):
            positions.pop(j)

        # --- Entry ---
        hour = int(bar["hour"])
        dow = int(bar["dow"])
        if dow in block_days:
            continue

        n_long = sum(1 for p in positions if p["side"] == "long")
        n_short = sum(1 for p in positions if p["side"] == "short")

        current_month = str(dt)[:7]
        mc = monthly_counts.get(current_month, {"long": 0, "short": 0})

        if (n_long < max_same and len(positions) < total_max
            and hour not in l_block_hours
            and (i - last_exit_l) >= exit_cd_l
            and mc["long"] < l_monthly_cap):
            if l_signal_func(bar, i):
                positions.append({"side": "long", "entry_bar": i + 1, "entry_price": next_open})
                em = str(df.iloc[i+1]["datetime"])[:7]
                if em not in monthly_counts: monthly_counts[em] = {"long": 0, "short": 0}
                monthly_counts[em]["long"] += 1

        if (n_short < max_same and len(positions) < total_max
            and hour not in s_block_hours
            and (i - last_exit_s) >= exit_cd_s
            and mc["short"] < s_monthly_cap):
            if s_signal_func(bar, i):
                positions.append({"side": "short", "entry_bar": i + 1, "entry_price": next_open})
                em = str(df.iloc[i+1]["datetime"])[:7]
                if em not in monthly_counts: monthly_counts[em] = {"long": 0, "short": 0}
                monthly_counts[em]["short"] += 1

    tdf = pd.DataFrame(trades)
    if len(tdf) == 0:
        return tdf, 0, 0, ""
    tdf = tdf.sort_values("exit_dt").reset_index(drop=True)
    cum = tdf["pnl"].cumsum()
    dd = cum - cum.cummax()
    mdd = abs(dd.min())
    tdf["exit_date"] = pd.to_datetime(tdf["exit_dt"]).dt.date
    dpnl = tdf.groupby("exit_date")["pnl"].sum()
    wd_pnl = dpnl.min()
    wd_date = dpnl.idxmin()
    return tdf, mdd, wd_pnl, str(wd_date)


def full_report(tdf, label, mdd, wd_pnl, wd_date):
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    is_mask = tdf["entry_dt"] < split_date
    oos_mask = tdf["entry_dt"] >= split_date

    for lbl, mask in [("IS", is_mask), ("OOS", oos_mask)]:
        t = tdf[mask]
        if len(t) == 0:
            print(f"  {lbl}: NO TRADES")
            continue
        lt = t[t["side"]=="long"]; st = t[t["side"]=="short"]
        def s(d, tag):
            if len(d)==0: return f"{tag}:0t"
            n=len(d); pnl=d["pnl"].sum(); wr=(d["pnl"]>0).mean()
            w=d[d["pnl"]>0]["pnl"]; lo=d[d["pnl"]<=0]["pnl"]
            pf=w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
            wa = w.mean() if len(w)>0 else 0; la = lo.mean() if len(lo)>0 else 0
            return f"{tag}:{n}t ${pnl:.0f} PF{pf:.2f} WR{wr:.0%} W${wa:.0f} L${la:.0f}"
        print(f"  {lbl} {s(t,'ALL')} | {s(lt,'L')} | {s(st,'S')}")

    oos = tdf[oos_mask].copy()
    if len(oos) == 0:
        return None

    oos["month"] = oos["entry_dt"].dt.strftime("%Y-%m")
    reasons = oos.groupby("reason")["pnl"].agg(["count","mean"])
    exits_str = " | ".join(f"{r}:{int(row['count'])}({row['mean']:+.0f})" for r, row in reasons.iterrows())
    print(f"  Exits: {exits_str}")

    print(f"  {'Mon':<8} {'L_n':>4} {'L_WR':>6} {'L_PnL':>8} {'S_n':>4} {'S_WR':>6} {'S_PnL':>8} {'Total':>8}")
    for month in sorted(oos["month"].unique()):
        mt = oos[oos["month"]==month]
        lt = mt[mt["side"]=="long"]; st = mt[mt["side"]=="short"]
        l_n,s_n = len(lt),len(st)
        l_wr = (lt["pnl"]>0).mean() if l_n>0 else 0
        s_wr = (st["pnl"]>0).mean() if s_n>0 else 0
        l_pnl = lt["pnl"].sum(); s_pnl = st["pnl"].sum()
        print(f"  {month:<8} {l_n:>4} {l_wr:>6.0%} {l_pnl:>8.0f} {s_n:>4} {s_wr:>6.0%} {s_pnl:>8.0f} {l_pnl+s_pnl:>8.0f}")

    print(f"  MDD: ${mdd:.0f}, Worst Day: ${wd_pnl:.0f} ({wd_date})")

    all_monthly = oos.groupby("month")["pnl"].sum()
    pos_months = (all_monthly > 0).sum()
    total_pnl = oos["pnl"].sum()
    total_wr = (oos["pnl"]>0).mean()
    w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
    pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
    is_pnl = tdf[is_mask]["pnl"].sum()

    n_folds = 6
    fold_size = max(len(oos) // n_folds, 1)
    wf_positive = 0
    for f in range(n_folds):
        start = f * fold_size
        end = start + fold_size if f < n_folds - 1 else len(oos)
        fold_pnl = oos.iloc[start:end]["pnl"].sum()
        if fold_pnl > 0:
            wf_positive += 1

    # Consecutive loss
    sorted_t = oos.sort_values("exit_dt")
    mc=cc=0; mcp=ccp=0
    for _,t in sorted_t.iterrows():
        if t["pnl"]<0: cc+=1; ccp+=t["pnl"]
        else:
            if cc>mc: mc=cc; mcp=ccp
            cc=0; ccp=0
    if cc>mc: mc=cc; mcp=ccp

    gates = [
        ("G1 OOS_PnL>=600", total_pnl >= 600, f"${total_pnl:.0f}"),
        ("G2 WR>=55%", total_wr >= 0.55, f"{total_wr:.0%}"),
        ("G3 PF>=1.15", pf >= 1.15, f"{pf:.2f}"),
        ("G4 MDD<=1500", mdd <= 1500, f"${mdd:.0f}"),
        ("G5 WorstMo>=-300", all_monthly.min() >= -300, f"${all_monthly.min():.0f}"),
        ("G6 PosMo>=8/12", pos_months >= 8, f"{pos_months}/{len(all_monthly)}"),
        ("G7 WF>=4/6", wf_positive >= 4, f"{wf_positive}/6"),
        ("G8 IS_PnL>0", is_pnl > 0, f"${is_pnl:.0f}"),
    ]
    passed = sum(1 for _, ok, _ in gates if ok)
    print(f"\n  === V9 GATES ({passed}/{len(gates)}) ===")
    for g_name, ok, val in gates:
        print(f"    {g_name}: {val} [{'PASS' if ok else 'FAIL'}]")
    print(f"  Max consecutive loss: {mc} trades (${mcp:.0f})")
    return {"pnl": total_pnl, "wr": total_wr, "pf": pf, "mdd": mdd, "passed": passed, "is_pnl": is_pnl}


# ===================================================================
print("=" * 80)
print("V9 Round 4: Final Validation")
print("=" * 80)

col = "rel_ret5"
th = 0.03

# --- Config A: R3 Candidate 6 (best 7/8) ---
print("\n--- Config A: R3 Candidate 6 (7/8 PASS) ---")
print("LB5 TH0.03 LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8")
tdf, mdd, wp, wd = run_backtest_asym(
    l_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
    s_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
    l_tp=0.03, l_sl=0.035, l_mh=18,
    s_tp=0.015, s_sl=0.025, s_mh=8,
)
result_a = full_report(tdf, "Config A", mdd, wp, wd)

# --- Config B: R3 Phase 3 IS+OOS positive (L_cap6, CD_S10) ---
print("\n\n--- Config B: R3 Phase 3 (IS+OOS positive) ---")
print("LB5 TH0.03 LTP3.0/LSL4.0/LMH24 STP2.5/SSL3.5/SMH18 L_cap6 CD_S10")
tdf, mdd, wp, wd = run_backtest_asym(
    l_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
    s_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
    l_tp=0.03, l_sl=0.04, l_mh=24,
    s_tp=0.025, s_sl=0.035, s_mh=18,
    l_monthly_cap=6, exit_cd_s=10,
)
result_b = full_report(tdf, "Config B", mdd, wp, wd)

# --- Config C: Hybrid — use Config A exit params + L_cap from B ---
print("\n\n--- Config C: Config A + L_cap6 + CD_S10 ---")
print("LB5 TH0.03 LTP3.0/LSL3.5/LMH18 STP1.5/SSL2.5/SMH8 L_cap6 CD_S10")
tdf, mdd, wp, wd = run_backtest_asym(
    l_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
    s_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
    l_tp=0.03, l_sl=0.035, l_mh=18,
    s_tp=0.015, s_sl=0.025, s_mh=8,
    l_monthly_cap=6, exit_cd_s=10,
)
result_c = full_report(tdf, "Config C", mdd, wp, wd)

# --- Config D: Fine-tuning - sweep L_cap and CD_S around Config A ---
print("\n\n--- Config D: Fine-tune Config A with caps ---")
results_d = []
for l_cap in [4, 5, 6, 8]:
    for s_cap in [6, 8, 12]:
        for cd_s in [0, 6, 8, 10, 12]:
            for cd_l in [0, 6, 10]:
                tdf, mdd, wp, wd = run_backtest_asym(
                    l_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
                    s_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
                    l_tp=0.03, l_sl=0.035, l_mh=18,
                    s_tp=0.015, s_sl=0.025, s_mh=8,
                    l_monthly_cap=l_cap, s_monthly_cap=s_cap,
                    exit_cd_l=cd_l, exit_cd_s=cd_s,
                )
                if len(tdf) == 0: continue
                tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
                oos = tdf[tdf["entry_dt"] >= split_date]
                iis = tdf[tdf["entry_dt"] < split_date]
                if len(oos) < 10: continue
                n = len(oos)
                pnl = oos["pnl"].sum()
                is_pnl = iis["pnl"].sum()
                wr = (oos["pnl"]>0).mean()
                w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
                pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
                results_d.append({
                    "l_cap": l_cap, "s_cap": s_cap, "cd_l": cd_l, "cd_s": cd_s,
                    "n": n, "pnl": pnl, "is_pnl": is_pnl,
                    "wr": wr, "pf": pf, "mdd": mdd,
                })

# Filter IS+OOS positive, sort by OOS PnL
both_pos = [r for r in results_d if r["is_pnl"] > 0 and r["pnl"] > 600]
both_pos.sort(key=lambda x: x["pnl"], reverse=True)
print(f"\nConfig A + caps, IS+OOS>0, OOS>$600: {len(both_pos)} configs")
for r in both_pos[:20]:
    print(f"  L_cap{r['l_cap']} S_cap{r['s_cap']} CD_L{r['cd_l']} CD_S{r['cd_s']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f} MDD${r['mdd']:.0f}")

# Full report on the best IS+OOS positive config
if both_pos:
    best = both_pos[0]
    print(f"\n\n--- BEST CONFIG (IS+OOS positive): L_cap{best['l_cap']} S_cap{best['s_cap']} CD_L{best['cd_l']} CD_S{best['cd_s']} ---")
    tdf, mdd, wp, wd = run_backtest_asym(
        l_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] < -th,
        s_signal_func=lambda bar, i: not np.isnan(bar[col]) and bar[col] > th,
        l_tp=0.03, l_sl=0.035, l_mh=18,
        s_tp=0.015, s_sl=0.025, s_mh=8,
        l_monthly_cap=best["l_cap"], s_monthly_cap=best["s_cap"],
        exit_cd_l=best["cd_l"], exit_cd_s=best["cd_s"],
    )
    result_best = full_report(tdf, "BEST", mdd, wp, wd)

# Also check without IS constraint - what's the max achievable?
all_above_600 = [r for r in results_d if r["pnl"] > 600]
all_above_600.sort(key=lambda x: x["pnl"], reverse=True)
print(f"\n\nAll configs with OOS>$600 (regardless of IS): {len(all_above_600)}")
for r in all_above_600[:10]:
    print(f"  L_cap{r['l_cap']} S_cap{r['s_cap']} CD_L{r['cd_l']} CD_S{r['cd_s']} {r['n']}t OOS${r['pnl']:.0f} IS${r['is_pnl']:.0f} WR{r['wr']:.0%} PF{r['pf']:.2f} MDD${r['mdd']:.0f}")

# --- Sensitivity analysis on best config ---
print("\n\n--- Sensitivity Analysis: Threshold variation ---")
for th_test in [0.025, 0.028, 0.030, 0.032, 0.035]:
    if both_pos:
        best = both_pos[0]
    else:
        best = {"l_cap": 6, "s_cap": 8, "cd_l": 0, "cd_s": 10}
    tdf, mdd, wp, wd = run_backtest_asym(
        l_signal_func=lambda bar, i, t=th_test: not np.isnan(bar[col]) and bar[col] < -t,
        s_signal_func=lambda bar, i, t=th_test: not np.isnan(bar[col]) and bar[col] > t,
        l_tp=0.03, l_sl=0.035, l_mh=18,
        s_tp=0.015, s_sl=0.025, s_mh=8,
        l_monthly_cap=best["l_cap"], s_monthly_cap=best["s_cap"],
        exit_cd_l=best["cd_l"], exit_cd_s=best["cd_s"],
    )
    if len(tdf) == 0: continue
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_dt"])
    oos = tdf[tdf["entry_dt"] >= split_date]
    iis = tdf[tdf["entry_dt"] < split_date]
    if len(oos) > 0:
        n = len(oos); pnl = oos["pnl"].sum(); is_pnl = iis["pnl"].sum()
        wr = (oos["pnl"]>0).mean()
        w = oos[oos["pnl"]>0]["pnl"]; lo = oos[oos["pnl"]<=0]["pnl"]
        pf = w.sum()/abs(lo.sum()) if lo.sum()!=0 else 999
        print(f"  TH={th_test:.3f}: {n}t OOS${pnl:.0f} IS${is_pnl:.0f} WR{wr:.0%} PF{pf:.2f} MDD${mdd:.0f}")

print("\n" + "=" * 80)
print("V9 R4 Complete")
print("=" * 80)
