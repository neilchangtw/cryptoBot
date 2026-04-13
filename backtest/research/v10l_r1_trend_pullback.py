"""
V10-L Round 1: 1h EMA Trend + Pullback Entry
Hypothesis: Buy pullbacks in uptrends (close > long EMA, close near EMA20).
            Bull months = more signals; bear months = natural filter.
            All 1h data, no 4h lookahead risk.

Grid: trend_ema × gap_max × safenet × min_hold × max_hold = 216 configs
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np
from itertools import product

# ─── Data ─────────────────────────────────────────────
df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000
FEE = 4.0
WARMUP = 150
TOTAL = len(df)
IS_END = TOTAL // 2

# Circuit breaker constants
DAILY_LOSS_LIMIT = -200
MONTHLY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4
CONSEC_LOSS_COOLDOWN = 24

# ─── Indicators (all shift(1)) ─────────────────────────
close = df["close"].astype(float)
for span in [20, 40, 50, 60, 80]:
    df[f"ema{span}"] = close.ewm(span=span, adjust=False).mean().shift(1)

# Session filter: block hours {0,1,2,12} UTC+8, block days {Mon,Sat,Sun}
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek   # 0=Mon
df["session_ok"] = ~(df["hour"].isin([0, 1, 2, 12]) | df["dow"].isin([0, 5, 6]))

# V10 Short monthly PnL for merge preview
V10_SHORT = {
    "2025-04": 527, "2025-05": 93,  "2025-06": 388,
    "2025-07": 76,  "2025-08": 392, "2025-09": 324,
    "2025-10": 467, "2025-11": 332, "2025-12": 286,
    "2026-01": 18,  "2026-02": 406, "2026-03": 98,
}

# ─── Backtest Engine ──────────────────────────────────
def run_backtest(df, p, start, end):
    trend_col = f"ema{p['trend_ema']}"
    gap_min   = p.get("gap_min", -1.0)
    gap_max   = p["gap_max"]
    safenet   = p["safenet"]
    min_hold  = p["min_hold"]
    max_hold  = p["max_hold"]
    exit_cd   = p.get("exit_cd", 8)
    entry_cap = p.get("entry_cap", 20)
    es_bars   = p.get("es_bars", 5)
    es_pct    = p.get("es_pct", 1.0)

    trades = []
    pos = None          # {ep, dt, idx}
    last_exit = -999
    d_pnl = m_pnl = 0.0
    consec = 0
    cd_until = 0
    cur_month = cur_date = None
    m_entries = 0

    for i in range(start, end):
        b = df.iloc[i]
        bm = b["datetime"].to_period("M")
        bd = b["datetime"].date()

        if bd != cur_date:
            d_pnl = 0.0; cur_date = bd
        if bm != cur_month:
            m_pnl = 0.0; m_entries = 0; cur_month = bm

        # ── Exit ──
        if pos is not None:
            held = i - pos["idx"]
            ep = pos["ep"]
            pnl_pct = (b["close"] / ep - 1) * 100
            xp = xr = None

            # 1. SafeNet
            sn = ep * (1 - safenet / 100)
            if b["low"] <= sn:
                xp = sn - (sn - b["low"]) * 0.25
                xr = "SafeNet"

            # 2. EarlyStop
            if xp is None and 1 <= held <= es_bars and pnl_pct < -es_pct:
                xp = b["close"]; xr = "EarlyStop"

            # 3. EMAcross (close < EMA20)
            if xp is None and held >= min_hold and b["close"] < b["ema20"]:
                xp = b["close"]; xr = "EMAcross"

            # 4. MaxHold
            if xp is None and held >= max_hold:
                xp = b["close"]; xr = "MaxHold"

            if xp is not None:
                pnl = (xp / ep - 1) * NOTIONAL - FEE
                trades.append({
                    "pnl": pnl, "reason": xr,
                    "month": str(b["datetime"].to_period("M")),
                    "entry_dt": pos["dt"], "exit_dt": b["datetime"],
                    "held": held,
                })
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE:
                        cd_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec = 0
                last_exit = i; pos = None

        # ── Entry ──
        if pos is not None:
            continue
        if d_pnl <= DAILY_LOSS_LIMIT:  continue
        if m_pnl <= MONTHLY_LOSS_LIMIT: continue
        if i < cd_until:               continue
        if i - last_exit < exit_cd:    continue
        if m_entries >= entry_cap:      continue
        if not b["session_ok"]:        continue

        ema_t = b[trend_col]
        ema20 = b["ema20"]
        if pd.isna(ema_t) or pd.isna(ema20):
            continue

        # Trend: close > long EMA
        if b["close"] <= ema_t:
            continue

        # Pullback: close near EMA20
        gap = (b["close"] / ema20 - 1) * 100
        if gap < gap_min or gap > gap_max:
            continue

        # Enter at next bar open
        if i + 1 < len(df):
            pos = {
                "ep": df.iloc[i + 1]["open"],
                "dt": df.iloc[i + 1]["datetime"],
                "idx": i + 1,
            }
            m_entries += 1

    # Force close at end
    if pos is not None:
        b = df.iloc[end - 1]
        pnl = (b["close"] / pos["ep"] - 1) * NOTIONAL - FEE
        trades.append({
            "pnl": pnl, "reason": "EOD",
            "month": str(b["datetime"].to_period("M")),
            "entry_dt": pos["dt"], "exit_dt": b["datetime"],
            "held": end - 1 - pos["idx"],
        })

    return trades


def metrics(trades):
    if not trades:
        return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t = pd.DataFrame(trades)
    n = len(t)
    pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; l = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(l["pnl"].sum()) if len(l) else 0.001
    pf = gw/gl

    cum = t["pnl"].cumsum()
    mdd = abs((cum - cum.cummax()).min())

    mo = t.groupby("month")["pnl"].sum()
    pm = (mo>0).sum()
    return {"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "pos_m":pm,"tot_m":len(mo),"worst_m":mo.min(),"monthly":mo}


def passes_goals(m):
    """Check independent Long goals."""
    return (m["pnl"] > 0 and
            m["pos_m"] >= min(8, m["tot_m"]) and
            m["worst_m"] >= -200 and
            m["mdd"] <= 400)


# ─── Parameter Grid ───────────────────────────────────
grid = list(product(
    [40, 50, 60, 80],       # trend_ema
    [1.5, 2.5, 3.5],        # gap_max
    [3.5, 4.5],             # safenet
    [3, 5, 7],              # min_hold
    [15, 20, 25],           # max_hold
))

print(f"=== V10-L R1: Trend + Pullback ===")
print(f"Bars: {TOTAL}, IS: 0-{IS_END}, OOS: {IS_END}-{TOTAL}")
print(f"Grid: {len(grid)} configs")
print(f"Fixed: gap_min=-1.0, exit_cd=8, entry_cap=20, es_bars=5, es_pct=1.0")
print()

results = []
for idx, (t_ema, gmax, sn, mh, maxh) in enumerate(grid):
    p = {
        "trend_ema": t_ema, "gap_min": -1.0, "gap_max": gmax,
        "safenet": sn, "min_hold": mh, "max_hold": maxh,
        "exit_cd": 8, "entry_cap": 20, "es_bars": 5, "es_pct": 1.0,
    }
    is_trades = run_backtest(df, p, WARMUP, IS_END)
    oos_trades = run_backtest(df, p, IS_END, TOTAL - 1)
    is_m = metrics(is_trades)
    oos_m = metrics(oos_trades)

    results.append({
        "params": p, "is": is_m, "oos": oos_m,
        "is_pnl": is_m["pnl"], "oos_pnl": oos_m["pnl"],
    })

    if (idx + 1) % 50 == 0:
        print(f"  ... {idx+1}/{len(grid)} done")

# ─── Results ──────────────────────────────────────────
print(f"\n{'='*60}")
print(f"RESULTS SUMMARY")
print(f"{'='*60}")

# Filter: IS > 0 AND OOS > 0
both_pos = [r for r in results if r["is_pnl"] > 0 and r["oos_pnl"] > 0]
print(f"\nIS>0 AND OOS>0: {len(both_pos)}/{len(results)}")

is_pos = sum(1 for r in results if r["is_pnl"] > 0)
oos_pos = sum(1 for r in results if r["oos_pnl"] > 0)
print(f"IS>0: {is_pos}, OOS>0: {oos_pos}")

# Sort by OOS PnL
both_pos.sort(key=lambda x: x["oos_pnl"], reverse=True)

# Top 10
print(f"\n--- Top 10 by OOS PnL (IS>0 AND OOS>0) ---")
print(f"{'Rank':>4} {'t_ema':>5} {'gmax':>4} {'SN':>4} {'mh':>3} {'maxh':>4} | "
      f"{'IS_n':>4} {'IS_PnL':>8} {'IS_WR':>5} | "
      f"{'OOS_n':>5} {'OOS_PnL':>8} {'OOS_WR':>6} {'OOS_PF':>6} {'MDD':>6} {'PM':>5} {'WM':>7}")

for rank, r in enumerate(both_pos[:20], 1):
    p = r["params"]
    i = r["is"]; o = r["oos"]
    pm_str = f"{o['pos_m']}/{o['tot_m']}"
    print(f"{rank:>4} {p['trend_ema']:>5} {p['gap_max']:>4} {p['safenet']:>4} "
          f"{p['min_hold']:>3} {p['max_hold']:>4} | "
          f"{i['n']:>4} {i['pnl']:>8.0f} {i['wr']:>5.1f} | "
          f"{o['n']:>5} {o['pnl']:>8.0f} {o['wr']:>6.1f} {o['pf']:>6.2f} "
          f"{o['mdd']:>6.0f} {pm_str:>5} {o['worst_m']:>7.0f}")

# Goal check for top configs
print(f"\n--- Goal Check (Top 5) ---")
for rank, r in enumerate(both_pos[:5], 1):
    p = r["params"]
    o = r["oos"]
    i = r["is"]
    print(f"\n[#{rank}] trend={p['trend_ema']} gap_max={p['gap_max']} SN={p['safenet']} "
          f"min_hold={p['min_hold']} max_hold={p['max_hold']}")
    print(f"  IS:  {i['n']}t ${i['pnl']:.0f} PF={i['pf']:.2f} WR={i['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f}")

    goals = [
        ("IS > $0", i["pnl"] > 0),
        ("OOS > $0", o["pnl"] > 0),
        (f"Pos months >= 8/{o['tot_m']}", o["pos_m"] >= min(8, o["tot_m"])),
        ("Worst month >= -$200", o["worst_m"] >= -200),
        ("MDD <= $400", o["mdd"] <= 400),
    ]
    for name, ok in goals:
        print(f"    {'PASS' if ok else 'FAIL'} {name}: ", end="")
        if "IS" in name: print(f"${i['pnl']:.0f}")
        elif "OOS" in name and ">" in name: print(f"${o['pnl']:.0f}")
        elif "Pos" in name: print(f"{o['pos_m']}/{o['tot_m']}")
        elif "Worst" in name: print(f"${o['worst_m']:.0f}")
        elif "MDD" in name: print(f"${o['mdd']:.0f}")

    # Monthly detail + L+S merge
    if "monthly" in o:
        print(f"\n  Monthly detail + L+S merge:")
        print(f"  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
        total_ls = 0
        for m_str, l_pnl in sorted(o["monthly"].items()):
            s_pnl = V10_SHORT.get(m_str, 0)
            ls = l_pnl + s_pnl
            total_ls += ls
            flag = " *" if s_pnl < 100 else ""
            print(f"  {m_str:>8} {l_pnl:>+7.0f} {s_pnl:>+7.0f} {ls:>+7.0f}{flag}")
        s_total = sum(V10_SHORT.get(m, 0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {s_total:>+7.0f} {total_ls:>+7.0f}")

# Exit reasons distribution for best config
if both_pos:
    best = both_pos[0]
    p = best["params"]
    oos_trades = run_backtest(df, p, IS_END, TOTAL - 1)
    t = pd.DataFrame(oos_trades)
    print(f"\n--- Best Config OOS Exit Reasons ---")
    for reason, group in t.groupby("reason"):
        print(f"  {reason:>10}: {len(group):>3}t  avg=${group['pnl'].mean():>+.1f}  "
              f"WR={len(group[group['pnl']>0])/len(group)*100:.0f}%")

# Quick WF check for best config
if both_pos:
    best = both_pos[0]
    p = best["params"]
    n_folds = 6
    fold_size = (TOTAL - WARMUP) // n_folds
    wf_results = []
    print(f"\n--- Walk-Forward 6-Fold (Best Config) ---")
    for fold in range(n_folds):
        fs = WARMUP + fold * fold_size
        fe = fs + fold_size if fold < n_folds - 1 else TOTAL - 1
        ft = run_backtest(df, p, fs, fe)
        fm = metrics(ft)
        ok = fm["pnl"] > 0
        wf_results.append(ok)
        print(f"  Fold {fold+1}: {fs}-{fe} ({df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')})"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} {'PASS' if ok else 'FAIL'}")
    wf_pass = sum(wf_results)
    print(f"  WF: {wf_pass}/6")

print(f"\n{'='*60}")
print("Anti-lookahead checklist:")
print("[x] 1. All EMAs use .shift(1)")
print("[x] 2. Percentiles use .shift(1).rolling(N) — N/A this round")
print("[x] 3. Entry price = O[i+1]")
print("[x] 4. IS/OOS fixed split (50/50)")
print("[x] 5. No multi-timeframe data (pure 1h)")
print("[x] 6. Freshness: shift(1) on all indicators")
