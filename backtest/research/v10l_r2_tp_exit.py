"""
V10-L Round 2: Replace EMAcross with TP + MaxHold
R1 finding: EMAcross kills Long profits (WR 35%, avg -$12).
            MaxHold is the entire profit engine (29t, avg +$181, WR 100%).
Change: Remove EMAcross exit, add fixed TP instead.
        Keep entry logic: close > EMA80, pullback within [-1%, gap_max] of EMA20.

Grid: tp_pct × gap_max × safenet × max_hold × es config = 360 configs
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

DAILY_LOSS_LIMIT = -200
MONTHLY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4
CONSEC_LOSS_COOLDOWN = 24

# ─── Indicators (shift(1)) ────────────────────────────
close = df["close"].astype(float)
for span in [20, 80]:
    df[f"ema{span}"] = close.ewm(span=span, adjust=False).mean().shift(1)

df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0, 1, 2, 12]) | df["dow"].isin([0, 5, 6]))

V10_SHORT = {
    "2025-04": 527, "2025-05": 93,  "2025-06": 388,
    "2025-07": 76,  "2025-08": 392, "2025-09": 324,
    "2025-10": 467, "2025-11": 332, "2025-12": 286,
    "2026-01": 18,  "2026-02": 406, "2026-03": 98,
}

# ─── Backtest Engine (TP + MaxHold, no EMAcross) ──────
def run_backtest(df, p, start, end):
    gap_min   = p.get("gap_min", -1.0)
    gap_max   = p["gap_max"]
    safenet   = p["safenet"]
    tp_pct    = p["tp_pct"]
    max_hold  = p["max_hold"]
    exit_cd   = p.get("exit_cd", 8)
    entry_cap = p.get("entry_cap", 20)
    es_bars   = p.get("es_bars", 5)
    es_pct    = p.get("es_pct", 1.0)

    trades = []
    pos = None
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

            # 3. TP (take profit at high if reached, else close)
            if xp is None:
                tp_level = ep * (1 + tp_pct / 100)
                if b["high"] >= tp_level:
                    xp = tp_level; xr = "TP"

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
        if pos is not None: continue
        if d_pnl <= DAILY_LOSS_LIMIT:   continue
        if m_pnl <= MONTHLY_LOSS_LIMIT: continue
        if i < cd_until:                continue
        if i - last_exit < exit_cd:     continue
        if m_entries >= entry_cap:       continue
        if not b["session_ok"]:         continue

        ema80 = b["ema80"]
        ema20 = b["ema20"]
        if pd.isna(ema80) or pd.isna(ema20): continue

        # Trend: close > EMA80
        if b["close"] <= ema80: continue

        # Pullback: close near EMA20
        gap = (b["close"] / ema20 - 1) * 100
        if gap < gap_min or gap > gap_max: continue

        # Enter at next bar open
        if i + 1 < len(df):
            pos = {"ep": df.iloc[i+1]["open"], "dt": df.iloc[i+1]["datetime"], "idx": i+1}
            m_entries += 1

    if pos is not None:
        b = df.iloc[end - 1]
        pnl = (b["close"] / pos["ep"] - 1) * NOTIONAL - FEE
        trades.append({"pnl": pnl, "reason": "EOD",
                       "month": str(b["datetime"].to_period("M")),
                       "entry_dt": pos["dt"], "exit_dt": b["datetime"],
                       "held": end-1-pos["idx"]})
    return trades


def metrics(trades):
    if not trades:
        return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t = pd.DataFrame(trades)
    n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; l = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(l["pnl"].sum()) if len(l) else 0.001
    cum = t["pnl"].cumsum()
    mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    worst_day = 0
    t["date"] = pd.to_datetime(t["exit_dt"]).dt.date
    daily = t.groupby("date")["pnl"].sum()
    worst_day = daily.min() if len(daily) else 0
    return {"n":n,"pnl":pnl,"pf":gw/gl,"wr":wr,"mdd":mdd,
            "pos_m":(mo>0).sum(),"tot_m":len(mo),"worst_m":mo.min(),
            "monthly":mo,"worst_day":worst_day}


# ─── Grid ─────────────────────────────────────────────
grid = list(product(
    [1.5, 2.0, 2.5, 3.0, 4.0, 5.0],   # tp_pct
    [1.5, 2.5, 3.5],                    # gap_max
    [3.5, 4.5],                         # safenet
    [12, 15, 20],                       # max_hold
    [(5, 1.0), (3, 1.5)],              # (es_bars, es_pct)
))

print(f"=== V10-L R2: TP + MaxHold Exit (no EMAcross) ===")
print(f"Entry: close > EMA80, pullback near EMA20 (gap_min=-1.0)")
print(f"Exit: SafeNet -> EarlyStop -> TP -> MaxHold")
print(f"Grid: {len(grid)} configs")
print()

results = []
for idx, (tp, gmax, sn, maxh, (esb, esp)) in enumerate(grid):
    p = {"gap_min": -1.0, "gap_max": gmax, "safenet": sn, "tp_pct": tp,
         "max_hold": maxh, "exit_cd": 8, "entry_cap": 20,
         "es_bars": esb, "es_pct": esp}
    is_t = run_backtest(df, p, WARMUP, IS_END)
    oos_t = run_backtest(df, p, IS_END, TOTAL - 1)
    is_m = metrics(is_t); oos_m = metrics(oos_t)
    results.append({"p": p, "is": is_m, "oos": oos_m,
                     "is_pnl": is_m["pnl"], "oos_pnl": oos_m["pnl"]})
    if (idx+1) % 50 == 0:
        print(f"  ... {idx+1}/{len(grid)} done")

# ─── Results ──────────────────────────────────────────
print(f"\n{'='*65}")
print("RESULTS SUMMARY")
print(f"{'='*65}")

both = [r for r in results if r["is_pnl"] > 0 and r["oos_pnl"] > 0]
is_pos = sum(1 for r in results if r["is_pnl"] > 0)
oos_pos = sum(1 for r in results if r["oos_pnl"] > 0)
print(f"\nIS>0: {is_pos}/{len(results)}, OOS>0: {oos_pos}/{len(results)}")
print(f"IS>0 AND OOS>0: {len(both)}/{len(results)}")

# Goal pass
goal_pass = [r for r in both if
             r["oos"]["pos_m"] >= min(8, r["oos"]["tot_m"]) and
             r["oos"]["worst_m"] >= -200 and
             r["oos"]["mdd"] <= 400]
print(f"ALL GOALS PASS: {len(goal_pass)}/{len(results)}")

both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n--- Top 15 by OOS PnL (IS>0 AND OOS>0) ---")
print(f"{'#':>2} {'TP':>4} {'gmax':>4} {'SN':>4} {'maxh':>4} {'ESb':>3} {'ESp':>4} | "
      f"{'IS_n':>4} {'IS$':>6} {'IS_WR':>5} | "
      f"{'On':>4} {'O$':>7} {'OWR':>5} {'OPF':>5} {'MDD':>5} {'PM':>5} {'WM':>6}")

for i, r in enumerate(both[:15], 1):
    p=r["p"]; s=r["is"]; o=r["oos"]
    pm = f"{o['pos_m']}/{o['tot_m']}"
    print(f"{i:>2} {p['tp_pct']:>4} {p['gap_max']:>4} {p['safenet']:>4} {p['max_hold']:>4} "
          f"{p['es_bars']:>3} {p['es_pct']:>4} | "
          f"{s['n']:>4} {s['pnl']:>+6.0f} {s['wr']:>5.1f} | "
          f"{o['n']:>4} {o['pnl']:>+7.0f} {o['wr']:>5.1f} {o['pf']:>5.2f} "
          f"{o['mdd']:>5.0f} {pm:>5} {o['worst_m']:>+6.0f}")

# Detailed analysis of top 3
for rank, r in enumerate(both[:3], 1):
    p=r["p"]; o=r["oos"]; s=r["is"]
    print(f"\n{'─'*60}")
    print(f"[#{rank}] TP={p['tp_pct']}% gap_max={p['gap_max']} SN={p['safenet']}% "
          f"MaxH={p['max_hold']} ES={p['es_bars']}@{p['es_pct']}%")
    print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f} WorstDay=${o['worst_day']:.0f}")

    goals = [
        ("IS > $0",              s["pnl"] > 0),
        ("OOS > $0",             o["pnl"] > 0),
        (f"Pos months >= 8/{o['tot_m']}",  o["pos_m"] >= min(8, o["tot_m"])),
        ("Worst month >= -$200", o["worst_m"] >= -200),
        ("MDD <= $400",          o["mdd"] <= 400),
        ("Worst day >= -$200",   o["worst_day"] >= -200),
    ]
    for name, ok in goals:
        print(f"    {'PASS' if ok else 'FAIL'} {name}")

    # Monthly + L+S merge
    if "monthly" in o:
        print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
        ls_total = 0; neg_ls = 0
        for m_str, l_pnl in sorted(o["monthly"].items()):
            s_pnl = V10_SHORT.get(m_str, 0)
            ls = l_pnl + s_pnl; ls_total += ls
            flag = " *" if s_pnl < 100 else ""
            if ls < 0: neg_ls += 1
            print(f"  {m_str:>8} {l_pnl:>+7.0f} {s_pnl:>+7.0f} {ls:>+7.0f}{flag}")
        s_total = sum(V10_SHORT.get(m, 0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {s_total:>+7.0f} {ls_total:>+7.0f}")
        pos_ls = len(o["monthly"]) - neg_ls
        print(f"  L+S positive months: {pos_ls}/{len(o['monthly'])}")

    # Exit reasons
    oos_t = run_backtest(df, p, IS_END, TOTAL - 1)
    t = pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason, g in t.groupby("reason"):
            gw = len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t  avg=${g['pnl'].mean():>+.1f}  "
                  f"WR={gw/len(g)*100:.0f}%  avg_hold={g['held'].mean():.1f}bar")

# WF for best config
if both:
    best = both[0]
    p = best["p"]
    n_folds = 6
    fold_size = (TOTAL - WARMUP) // n_folds
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best: TP={p['tp_pct']}% gap={p['gap_max']} "
          f"SN={p['safenet']}% maxH={p['max_hold']}]")
    wf_pass = 0
    for fold in range(n_folds):
        fs = WARMUP + fold * fold_size
        fe = fs + fold_size if fold < n_folds - 1 else TOTAL - 1
        ft = run_backtest(df, p, fs, fe)
        fm = metrics(ft)
        ok = fm["pnl"] > 0
        wf_pass += ok
        print(f"  Fold {fold+1}: {df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf_pass}/6")

# Compare R1 vs R2
print(f"\n{'='*65}")
print("R1 vs R2 COMPARISON (best config each)")
print(f"  R1 (EMAcross): 144t $1,881 WR=38.9% MDD=$660 PM=6/13")
print(f"  R2 (TP+MaxH):  see above")

print(f"\n{'='*65}")
print("Anti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6 — all shift(1), pure 1h")
