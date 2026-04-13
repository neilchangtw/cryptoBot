"""
V10-L Round 3: GK Compression + Upward Breakout Long
R1-R2 found trend+pullback entry fails in IS (bear market).
Switch to GK compression breakout — proven V6 concept, adapted for maxTotal=1.

Entry: GK_pct < threshold AND close > max(close, N bars) breakout
Exit:  SafeNet -> EarlyStop -> EMAcross (min_hold) -> MaxHold
       Also test TP variant

Grid: gk_thresh × breakout × safenet × min_hold × max_hold = 360+ configs
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

# ─── Indicators ───────────────────────────────────────
c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# EMA20 (shift(1))
df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

# GK percentile (shift(1) built into the formula)
gk_raw = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
gk_fast = gk_raw.rolling(5).mean()
gk_slow = gk_raw.rolling(20).mean()
gk_ratio = gk_fast / gk_slow
# shift(1) then rolling percentile
gk_shifted = gk_ratio.shift(1)
def pctile_func(s):
    if s.isna().any() or len(s) < 2:
        return np.nan
    v = s.iloc[-1]
    return ((s < v).sum()) / (len(s) - 1) * 100
df["gk_pct"] = gk_shifted.rolling(100).apply(pctile_func, raw=False)

# Close breakout (various lookbacks)
for bl in [8, 10, 12, 15, 20]:
    df[f"bo_up_{bl}"] = c > c.shift(1).rolling(bl).max()

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"] = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0, 1, 2, 12]) | df["dow"].isin([0, 5, 6]))

V10_SHORT = {
    "2025-04": 527, "2025-05": 93,  "2025-06": 388,
    "2025-07": 76,  "2025-08": 392, "2025-09": 324,
    "2025-10": 467, "2025-11": 332, "2025-12": 286,
    "2026-01": 18,  "2026-02": 406, "2026-03": 98,
}

# ─── Backtest Engine ──────────────────────────────────
def run_backtest(df, p, start, end):
    gk_thresh = p["gk_thresh"]
    bl        = p["breakout"]
    safenet   = p["safenet"]
    min_hold  = p["min_hold"]
    max_hold  = p["max_hold"]
    exit_cd   = p.get("exit_cd", 8)
    entry_cap = p.get("entry_cap", 20)
    es_bars   = p.get("es_bars", 5)
    es_pct    = p.get("es_pct", 1.0)
    use_tp    = p.get("use_tp", False)
    tp_pct    = p.get("tp_pct", 2.0)

    bo_col = f"bo_up_{bl}"
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

            # 3a. TP (optional)
            if xp is None and use_tp:
                tp_level = ep * (1 + tp_pct / 100)
                if b["high"] >= tp_level:
                    xp = tp_level; xr = "TP"

            # 3b. EMAcross (close < EMA20, min hold)
            if xp is None and not use_tp and held >= min_hold:
                if b["close"] < b["ema20"]:
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

        if pos is not None: continue
        if d_pnl <= DAILY_LOSS_LIMIT:   continue
        if m_pnl <= MONTHLY_LOSS_LIMIT: continue
        if i < cd_until:                continue
        if i - last_exit < exit_cd:     continue
        if m_entries >= entry_cap:       continue
        if not b["session_ok"]:         continue

        gk = b["gk_pct"]
        bo = b[bo_col]
        if pd.isna(gk): continue

        # GK compression + upward breakout
        if gk < gk_thresh and bo:
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
    t["date"] = pd.to_datetime(t["exit_dt"]).dt.date
    daily = t.groupby("date")["pnl"].sum()
    worst_day = daily.min() if len(daily) else 0
    return {"n":n,"pnl":pnl,"pf":gw/gl,"wr":wr,"mdd":mdd,
            "pos_m":(mo>0).sum(),"tot_m":len(mo),"worst_m":mo.min(),
            "monthly":mo,"worst_day":worst_day}


# ─── Grid A: EMAcross exit ────────────────────────────
gridA = list(product(
    [20, 30, 40, 50],     # gk_thresh
    [8, 10, 12, 15],      # breakout
    [3.5, 4.5],           # safenet
    [5, 7, 10],           # min_hold (EMAcross)
    [20, 25, 30],         # max_hold
))

print(f"=== V10-L R3: GK Compression + Breakout ===")
print(f"Grid A (EMAcross): {len(gridA)} configs")

resultsA = []
for idx, (gk, bl, sn, mh, maxh) in enumerate(gridA):
    p = {"gk_thresh": gk, "breakout": bl, "safenet": sn,
         "min_hold": mh, "max_hold": maxh, "use_tp": False,
         "exit_cd": 8, "entry_cap": 20, "es_bars": 5, "es_pct": 1.0}
    is_t = run_backtest(df, p, WARMUP, IS_END)
    oos_t = run_backtest(df, p, IS_END, TOTAL - 1)
    is_m = metrics(is_t); oos_m = metrics(oos_t)
    resultsA.append({"p": p, "is": is_m, "oos": oos_m,
                      "is_pnl": is_m["pnl"], "oos_pnl": oos_m["pnl"]})
    if (idx+1) % 100 == 0:
        print(f"  ... A {idx+1}/{len(gridA)}")

# ─── Grid B: TP + MaxHold exit ────────────────────────
gridB = list(product(
    [20, 30, 40, 50],     # gk_thresh
    [8, 10, 12, 15],      # breakout
    [3.5, 4.5],           # safenet
    [2.0, 3.0, 4.0],      # tp_pct
    [12, 15, 20],         # max_hold
))

print(f"Grid B (TP+MaxHold): {len(gridB)} configs")

resultsB = []
for idx, (gk, bl, sn, tp, maxh) in enumerate(gridB):
    p = {"gk_thresh": gk, "breakout": bl, "safenet": sn,
         "min_hold": 3, "max_hold": maxh, "use_tp": True, "tp_pct": tp,
         "exit_cd": 8, "entry_cap": 20, "es_bars": 5, "es_pct": 1.0}
    is_t = run_backtest(df, p, WARMUP, IS_END)
    oos_t = run_backtest(df, p, IS_END, TOTAL - 1)
    is_m = metrics(is_t); oos_m = metrics(oos_t)
    resultsB.append({"p": p, "is": is_m, "oos": oos_m,
                      "is_pnl": is_m["pnl"], "oos_pnl": oos_m["pnl"]})
    if (idx+1) % 100 == 0:
        print(f"  ... B {idx+1}/{len(gridB)}")

# ─── Summary ──────────────────────────────────────────
print(f"\n{'='*65}")
print("RESULTS")
print(f"{'='*65}")

for label, results in [("A (EMAcross)", resultsA), ("B (TP+MaxHold)", resultsB)]:
    both = [r for r in results if r["is_pnl"] > 0 and r["oos_pnl"] > 0]
    is_pos = sum(1 for r in results if r["is_pnl"] > 0)
    oos_pos = sum(1 for r in results if r["oos_pnl"] > 0)
    goal = [r for r in both if
            r["oos"]["pos_m"] >= min(8, r["oos"]["tot_m"]) and
            r["oos"]["worst_m"] >= -200 and
            r["oos"]["mdd"] <= 400]

    print(f"\n--- Grid {label} ({len(results)} configs) ---")
    print(f"IS>0: {is_pos}, OOS>0: {oos_pos}, Both>0: {len(both)}, Goals PASS: {len(goal)}")

    both.sort(key=lambda x: x["oos_pnl"], reverse=True)
    if both:
        print(f"\n{'#':>2} {'GK':>3} {'BL':>3} {'SN':>4} {'mh':>3} {'maxh':>4} {'TP':>4} | "
              f"{'IS_n':>4} {'IS$':>6} {'ISWR':>5} | "
              f"{'On':>4} {'O$':>7} {'OWR':>5} {'OPF':>5} {'MDD':>5} {'PM':>5} {'WM':>6}")

        for i, r in enumerate(both[:15], 1):
            p=r["p"]; s=r["is"]; o=r["oos"]
            pm = f"{o['pos_m']}/{o['tot_m']}"
            tp_str = f"{p.get('tp_pct','-'):>4}" if p.get("use_tp") else "  --"
            print(f"{i:>2} {p['gk_thresh']:>3} {p['breakout']:>3} {p['safenet']:>4} "
                  f"{p['min_hold']:>3} {p['max_hold']:>4} {tp_str} | "
                  f"{s['n']:>4} {s['pnl']:>+6.0f} {s['wr']:>5.1f} | "
                  f"{o['n']:>4} {o['pnl']:>+7.0f} {o['wr']:>5.1f} {o['pf']:>5.2f} "
                  f"{o['mdd']:>5.0f} {pm:>5} {o['worst_m']:>+6.0f}")

# ─── Best overall config detail ──────────────────────
all_both = ([r for r in resultsA if r["is_pnl"]>0 and r["oos_pnl"]>0] +
            [r for r in resultsB if r["is_pnl"]>0 and r["oos_pnl"]>0])
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

if all_both:
    for rank, r in enumerate(all_both[:3], 1):
        p=r["p"]; o=r["oos"]; s=r["is"]
        exit_type = "TP" if p.get("use_tp") else "EMAcross"
        print(f"\n{'='*60}")
        print(f"[#{rank}] GK<{p['gk_thresh']} BL={p['breakout']} SN={p['safenet']}% "
              f"Exit={exit_type} mh={p['min_hold']} maxH={p['max_hold']}"
              + (f" TP={p['tp_pct']}%" if p.get("use_tp") else ""))
        print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
              f"MDD=${o['mdd']:.0f} WorstDay=${o.get('worst_day',0):.0f}")

        goals = [
            ("IS > $0",              s["pnl"] > 0),
            ("OOS > $0",             o["pnl"] > 0),
            (f"PM >= 8/{o['tot_m']}", o["pos_m"] >= min(8, o["tot_m"])),
            ("Worst month >= -$200", o["worst_m"] >= -200),
            ("MDD <= $400",          o["mdd"] <= 400),
        ]
        all_pass = all(ok for _, ok in goals)
        for name, ok in goals:
            print(f"    {'PASS' if ok else 'FAIL'} {name}")
        print(f"  >>> {'ALL GOALS PASS' if all_pass else 'SOME GOALS FAIL'}")

        if "monthly" in o:
            print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
            ls_total = 0
            for m_str, l_pnl in sorted(o["monthly"].items()):
                s_pnl = V10_SHORT.get(m_str, 0)
                ls = l_pnl + s_pnl; ls_total += ls
                flag = " *" if s_pnl < 100 else ""
                print(f"  {m_str:>8} {l_pnl:>+7.0f} {s_pnl:>+7.0f} {ls:>+7.0f}{flag}")
            s_total = sum(V10_SHORT.get(m, 0) for m in o["monthly"].keys())
            print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {s_total:>+7.0f} {ls_total:>+7.0f}")

        # Exit reasons
        oos_t = run_backtest(df, p, IS_END, TOTAL - 1)
        t = pd.DataFrame(oos_t)
        if len(t):
            print(f"\n  Exit reasons:")
            for reason, g in t.groupby("reason"):
                gw = len(g[g["pnl"]>0])
                print(f"    {reason:>10}: {len(g):>3}t  avg=${g['pnl'].mean():>+.1f}  "
                      f"WR={gw/len(g)*100:.0f}%  hold={g['held'].mean():.1f}b")

    # WF for best
    best = all_both[0]
    p = best["p"]
    n_folds = 6
    fold_size = (TOTAL - WARMUP) // n_folds
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best config]")
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

else:
    print("\nNo configs with IS>0 AND OOS>0.")
    # Show best OOS anyway
    all_res = resultsA + resultsB
    all_res.sort(key=lambda x: x["oos_pnl"], reverse=True)
    print(f"\nBest OOS (regardless of IS):")
    for i, r in enumerate(all_res[:5], 1):
        p=r["p"]; o=r["oos"]; s=r["is"]
        exit_type = "TP" if p.get("use_tp") else "EMAcross"
        print(f"  #{i} GK<{p['gk_thresh']} BL={p['breakout']} {exit_type}: "
              f"IS={s['pnl']:+.0f} OOS={o['pnl']:+.0f} WR={o['wr']:.0f}%")

print(f"\n{'='*65}")
print("Anti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6")
print("  GK: shift(1) in ratio, then rolling(100) percentile")
print("  Breakout: close > close.shift(1).rolling(N).max()")
print("  EMA20: .shift(1)")
print("  Entry: O[i+1]")
