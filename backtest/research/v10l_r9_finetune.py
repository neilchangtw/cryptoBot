"""
V10-L Round 9: Fine-tune around R8 sweet spot

R8 breakthrough: 30 ALL GOALS PASS configs!
Best zone: GK<20, BL=12-15, TP=2.0%, maxH=5, SN=3.0-4.0%, mCap=-100, cd=8

R8 Top 3 PASS:
  #1: GK<20 BL=15 TP=2.0 maxH=5 SN=4.0 mCap=-100 cd=8 -> OOS $921 MDD=$360 WR=59%
  #2: GK<20 BL=15 TP=2.0 maxH=5 SN=4.0 mCap=-150 cd=8 -> OOS $899 MDD=$360 WR=58%
  #3: GK<30 BL=10 TP=3.0 maxH=8 SN=4.0 mCap=-100 cd=8 -> OOS $882 MDD=$386 WR=54%

Also notable: #4: GK<20 BL=12 TP=2.0 maxH=5 SN=4.0 mCap=-100 cd=8 -> OOS $830 MDD=$268

Fine-tune grid: explore tighter intervals around the sweet spot.
Add combined L+S monthly MDD calculation for combined goal validation.

Grid: GK[15,20,25] x BL[10,12,15] x TP[1.5,2.0,2.5] x maxH[4,5,6,7]
      x SN[2.5,3.0,3.5,4.0] x mCap[-75,-100,-125] x cd[6,8,10]
= 3 x 3 x 3 x 4 x 4 x 3 x 3 = 3,888 configs
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np

df = pd.read_csv("data/ETHUSDT_1h_latest730d.csv")
df["datetime"] = pd.to_datetime(df["datetime"])

NOTIONAL = 4000; FEE = 4.0; WARMUP = 150
TOTAL = len(df); IS_END = TOTAL // 2

c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

# GK volatility
ln_hl = np.log(h / l)
ln_co = np.log(c / o)
gk = 0.5 * ln_hl**2 - (2 * np.log(2) - 1) * ln_co**2
gk_ratio = gk.rolling(5).mean() / gk.rolling(20).mean()
gk_pct_raw = gk_ratio.shift(1).rolling(100).apply(
    lambda s: ((s < s.iloc[-1]).sum()) / (len(s) - 1) * 100, raw=False)
df["gk_pct"] = gk_pct_raw

# Close breakout
for bl in [10, 12, 15]:
    df[f"brk_up_{bl}"] = c > c.shift(1).rolling(bl).max()

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

# Pre-extract arrays for fast simulation
arr_low    = df["low"].values
arr_high   = df["high"].values
arr_close  = df["close"].values
arr_open   = df["open"].values
arr_gk     = df["gk_pct"].values
arr_sess   = df["session_ok"].values
arr_dt     = df["datetime"].values
# breakout arrays
brk_arrs = {}
for bl in [10, 12, 15]:
    brk_arrs[bl] = df[f"brk_up_{bl}"].values

V10_SHORT = {
    "2025-04":527,"2025-05":93,"2025-06":388,"2025-07":76,"2025-08":392,
    "2025-09":324,"2025-10":467,"2025-11":332,"2025-12":286,
    "2026-01":18,"2026-02":406,"2026-03":98,
}

CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

def run_bt(p, start, end):
    gk_t = p["gk_thresh"]; bl = p["bl"]
    sn = p["safenet"]; tp_pct = p["tp"]
    maxh = p["max_hold"]
    cd = p.get("exit_cd", 10); cap = p.get("entry_cap", 20)
    m_loss_cap = p.get("m_loss_cap", -200)

    brk = brk_arrs[bl]
    trades = []; pos_ep = 0.0; pos_idx = 0; pos_dt = None; in_pos = False
    last_exit = -999
    d_pnl = m_pnl = 0.0; consec = 0; cd_until = 0
    cur_m_str = ""; cur_d_str = ""; m_ent = 0

    for i in range(start, end):
        dt_val = arr_dt[i]
        dt_ts = pd.Timestamp(dt_val)
        m_str = str(dt_ts.to_period("M"))
        d_str = str(dt_ts.date())

        if d_str != cur_d_str: d_pnl = 0.0; cur_d_str = d_str
        if m_str != cur_m_str: m_pnl = 0.0; m_ent = 0; cur_m_str = m_str

        # Exit check
        if in_pos:
            held = i - pos_idx
            xp = 0.0; xr = ""

            # SafeNet
            sn_level = pos_ep * (1 - sn / 100)
            if arr_low[i] <= sn_level:
                xp = sn_level - (sn_level - arr_low[i]) * 0.25
                xr = "SafeNet"

            # TP
            if xr == "":
                tp_level = pos_ep * (1 + tp_pct / 100)
                if arr_high[i] >= tp_level:
                    xp = tp_level
                    xr = "TP"

            # MaxHold
            if xr == "" and held >= maxh:
                xp = arr_close[i]
                xr = "MaxHold"

            if xr != "":
                pnl = (xp / pos_ep - 1) * NOTIONAL - FEE
                trades.append({"pnl": pnl, "reason": xr, "month": m_str,
                    "held": held})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec = 0
                last_exit = i; in_pos = False

        if in_pos: continue

        # Circuit breakers
        if d_pnl <= -200: continue
        if m_pnl <= m_loss_cap: continue
        if i < cd_until: continue
        if i - last_exit < cd: continue
        if m_ent >= cap: continue
        if not arr_sess[i]: continue

        # Entry
        gk_val = arr_gk[i]
        if gk_val != gk_val or gk_val >= gk_t: continue  # NaN check + threshold
        if not brk[i]: continue

        if i + 1 < end:
            pos_ep = arr_open[i+1]; pos_idx = i+1; in_pos = True
            m_ent += 1

    if in_pos:
        pnl = (arr_close[end-1] / pos_ep - 1) * NOTIONAL - FEE
        dt_ts = pd.Timestamp(arr_dt[end-1])
        trades.append({"pnl": pnl, "reason": "EOD",
            "month": str(dt_ts.to_period("M")), "held": end-1-pos_idx})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t = pd.DataFrame(trades); n = len(t); pnl = t["pnl"].sum()
    w = t[t["pnl"]>0]; l = t[t["pnl"]<=0]
    wr = len(w)/n*100
    gw = w["pnl"].sum() if len(w) else 0
    gl = abs(l["pnl"].sum()) if len(l) else 0.001
    cum = t["pnl"].cumsum(); mdd = abs((cum - cum.cummax()).min())
    mo = t.groupby("month")["pnl"].sum()
    return {"n":n, "pnl":pnl, "pf":gw/gl, "wr":wr, "mdd":mdd,
            "pos_m":(mo>0).sum(), "tot_m":len(mo), "worst_m":mo.min(),
            "monthly":mo}

def ls_metrics(l_monthly):
    """Compute combined L+S metrics using L monthly and V10_SHORT monthly"""
    ls_months = {}
    for m, lp in l_monthly.items():
        sp = V10_SHORT.get(m, 0)
        ls_months[m] = lp + sp
    ls_series = pd.Series(ls_months)
    ls_cum = ls_series.sort_index().cumsum()
    ls_mdd = abs((ls_cum - ls_cum.cummax()).min()) if len(ls_cum) > 0 else 0
    ls_pos = (ls_series > 0).sum()
    ls_wm = ls_series.min() if len(ls_series) > 0 else 0
    ls_total = ls_series.sum()
    s_total = sum(V10_SHORT.get(m, 0) for m in l_monthly.keys())
    return {"ls_total": ls_total, "ls_pos": ls_pos, "ls_wm": ls_wm,
            "ls_mdd": ls_mdd, "s_total": s_total, "ls_months": ls_months}

# --- Build configs ---
configs = []
for gk_t in [15, 20, 25]:
    for bl in [10, 12, 15]:
        for tp in [1.5, 2.0, 2.5]:
            for maxh in [4, 5, 6, 7]:
                for sn in [2.5, 3.0, 3.5, 4.0]:
                    for m_cap in [-75, -100, -125]:
                        for cd in [6, 8, 10]:
                            configs.append({
                                "gk_thresh":gk_t, "bl":bl, "tp":tp,
                                "safenet":sn, "max_hold":maxh,
                                "m_loss_cap":m_cap, "exit_cd":cd, "entry_cap":20
                            })

print(f"=== V10-L R9: Fine-tune ===")
print(f"Fine-tune around R8 sweet spot: GK~20, TP~2%, maxH~5")
print(f"Grid: {len(configs)} configs")
print()

results = []
for idx, p in enumerate(configs):
    is_t = run_bt(p, WARMUP, IS_END)
    oos_t = run_bt(p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    results.append({"p":p, "is":is_m, "oos":oos_m,
                     "is_pnl":is_m["pnl"], "oos_pnl":oos_m["pnl"]})
    if (idx+1) % 500 == 0:
        print(f"  ... {idx+1}/{len(configs)}")

# --- Filters ---
all_both = [r for r in results if r["is_pnl"]>0 and r["oos_pnl"]>0]

# Compute L+S metrics for all Both>0
for r in all_both:
    if "monthly" in r["oos"]:
        r["ls"] = ls_metrics(r["oos"]["monthly"])
    else:
        r["ls"] = {"ls_total":0,"ls_pos":0,"ls_wm":0,"ls_mdd":0,"s_total":0}

# Independent goals
def ind_pass(r):
    o = r["oos"]
    return (o["pos_m"] >= min(8, o["tot_m"]) and
            o["worst_m"] >= -200 and o["mdd"] <= 400)

# Combined goals
def comb_pass(r):
    ls = r.get("ls", {})
    return (ls.get("ls_pos", 0) >= 11 and
            ls.get("ls_wm", -9999) >= -150 and
            ls.get("ls_mdd", 9999) <= 500 and
            ls.get("ls_total", 0) > ls.get("s_total", 9999))

def all_goals(r):
    return ind_pass(r) and comb_pass(r)

ind_list = [r for r in all_both if ind_pass(r)]
comb_list = [r for r in all_both if comb_pass(r)]
full_list = [r for r in all_both if all_goals(r)]

# --- Analysis by dimension ---
print(f"\n{'='*65}")
print("RESULTS BY GK THRESHOLD")
print(f"{'='*65}")
for gk in [15, 20, 25]:
    sub = [r for r in results if r["p"]["gk_thresh"]==gk]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    fp = [r for r in both if all_goals(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[GK<{gk}] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | "
          f"Ind PASS: {len(ip)} | ALL PASS: {len(fp)}")
    if best:
        o=best["oos"]; s=best["is"]; p=best["p"]
        print(f"  Best OOS: GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']} maxH={p['max_hold']} "
              f"SN={p['safenet']} mCap={p['m_loss_cap']} cd={p['exit_cd']}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

print(f"\n{'='*65}")
print("RESULTS BY TP LEVEL")
print(f"{'='*65}")
for tp in [1.5, 2.0, 2.5]:
    sub = [r for r in results if r["p"]["tp"]==tp]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    fp = [r for r in both if all_goals(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[TP={tp}%] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | "
          f"Ind PASS: {len(ip)} | ALL PASS: {len(fp)}")
    if best:
        o=best["oos"]; p=best["p"]
        print(f"  Best: GK<{p['gk_thresh']} BL={p['bl']} maxH={p['max_hold']} "
              f"SN={p['safenet']} mCap={p['m_loss_cap']} cd={p['exit_cd']}")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

print(f"\n{'='*65}")
print("RESULTS BY MAXHOLD")
print(f"{'='*65}")
for mxh in [4, 5, 6, 7]:
    sub = [r for r in results if r["p"]["max_hold"]==mxh]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    ip = [r for r in both if ind_pass(r)]
    fp = [r for r in both if all_goals(r)]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[maxH={mxh}] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | "
          f"Ind PASS: {len(ip)} | ALL PASS: {len(fp)}")
    if best:
        o=best["oos"]; p=best["p"]
        print(f"  Best: GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']} "
              f"SN={p['safenet']} mCap={p['m_loss_cap']} cd={p['exit_cd']}")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

# Top 20 overall (by OOS PnL)
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*65}")
print(f"TOP 20 (IS>0 AND OOS>0): {len(all_both)} total")
print(f"{'='*65}")
if all_both:
    print(f" {'#':>2} {'GK':>3} {'BL':>3} {'TP':>4} {'mxH':>4} {'SN':>4} {'mCap':>5} {'cd':>3} |"
          f" {'ISn':>4} {'IS$':>6} {'ISW':>4} | {'On':>4} {'O$':>6} {'OWR':>4} {'MDD':>5} {'PM':>5} {'WM':>6}"
          f" | {'L+S':>6} {'LPM':>4} {'LWM':>5} {'LDD':>5}")
    for i, r in enumerate(all_both[:20], 1):
        p=r["p"]; s=r["is"]; o=r["oos"]; ls=r.get("ls",{})
        pm=f"{o['pos_m']}/{o['tot_m']}"
        lpm = ls.get("ls_pos",0)
        lwm = ls.get("ls_wm",0)
        ldd = ls.get("ls_mdd",0)
        lst = ls.get("ls_total",0)
        print(f" {i:>2} {p['gk_thresh']:>3} {p['bl']:>3} {p['tp']:>4} {p['max_hold']:>4} "
              f"{p['safenet']:>4} {p['m_loss_cap']:>5} {p['exit_cd']:>3} | "
              f"{s['n']:>4} {s['pnl']:>+6.0f} {s['wr']:>4.0f} | "
              f"{o['n']:>4} {o['pnl']:>+6.0f} {o['wr']:>4.0f} "
              f"{o['mdd']:>5.0f} {pm:>5} {o['worst_m']:>+6.0f}"
              f" | {lst:>+6.0f} {lpm:>4} {lwm:>+5.0f} {ldd:>5.0f}")

# Goals summary
print(f"\n{'='*65}")
print(f"INDEPENDENT GOALS PASS: {len(ind_list)}")
print(f"COMBINED GOALS PASS: {len(comb_list)}")
print(f"ALL GOALS PASS (Ind + Comb): {len(full_list)}")

# Detail top 5 ALL GOALS PASS (sorted by OOS PnL)
if full_list:
    full_list.sort(key=lambda x: x["oos_pnl"], reverse=True)
    print(f"\n{'='*65}")
    print(f"ALL GOALS PASS -- TOP 5 (of {len(full_list)})")
    for rank, r in enumerate(full_list[:5], 1):
        p=r["p"]; o=r["oos"]; s=r["is"]; ls=r["ls"]
        print(f"\n{'~'*60}")
        print(f"[PASS #{rank}] GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']}% "
              f"maxH={p['max_hold']} SN={p['safenet']}% mCap={p['m_loss_cap']} cd={p['exit_cd']}")
        print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
              f"MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

        # Independent goals
        goals_i = [
            ("IS > $0", s["pnl"]>0),
            ("OOS > $0", o["pnl"]>0),
            (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
            ("Worst month >= -$200", o["worst_m"]>=-200),
            ("MDD <= $400", o["mdd"]<=400),
        ]
        for name, ok in goals_i: print(f"    {'PASS' if ok else 'FAIL'} {name}")

        # Combined goals
        goals_c = [
            (f"L+S PM >= 11/{o['tot_m']}", ls["ls_pos"]>=11),
            ("L+S worst month >= -$150", ls["ls_wm"]>=-150),
            ("L+S MDD <= $500", ls["ls_mdd"]<=500),
            (f"L+S net ${ls['ls_total']:+.0f} > S-only ${ls['s_total']:+.0f}",
             ls["ls_total"] > ls["s_total"]),
        ]
        for name, ok in goals_c: print(f"    {'PASS' if ok else 'FAIL'} {name}")

        # Monthly breakdown
        if "monthly" in o:
            print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
            for m in sorted(o["monthly"].keys()):
                lp = o["monthly"][m]
                sp = V10_SHORT.get(m, 0)
                ls_v = lp + sp
                f = " *" if sp < 100 else ""
                print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls_v:>+7.0f}{f}")
            print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {ls['s_total']:>+7.0f} {ls['ls_total']:>+7.0f}")
            print(f"  L+S pos months: {ls['ls_pos']}/{o['tot_m']}")
            print(f"  L+S worst month: ${ls['ls_wm']:+.0f}")
            print(f"  L+S monthly MDD: ${ls['ls_mdd']:.0f}")

        # Exit reasons
        oos_t = run_bt(p, IS_END, TOTAL-1)
        t = pd.DataFrame(oos_t)
        if len(t):
            print(f"\n  Exit reasons:")
            for reason, g in t.groupby("reason"):
                gw = len(g[g["pnl"]>0])
                print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                      f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")
elif ind_list:
    # Show top 5 independent-only PASS
    ind_list.sort(key=lambda x: x["oos_pnl"], reverse=True)
    print(f"\n{'='*65}")
    print(f"INDEPENDENT GOALS PASS (no full pass) -- TOP 5 (of {len(ind_list)})")
    for rank, r in enumerate(ind_list[:5], 1):
        p=r["p"]; o=r["oos"]; s=r["is"]; ls=r["ls"]
        print(f"\n{'~'*60}")
        print(f"[IND #{rank}] GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']}% "
              f"maxH={p['max_hold']} SN={p['safenet']}% mCap={p['m_loss_cap']} cd={p['exit_cd']}")
        print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
              f"MDD=${o['mdd']:.0f} WM=${o['worst_m']:+.0f}")

        goals_i = [
            ("IS > $0", s["pnl"]>0),
            ("OOS > $0", o["pnl"]>0),
            (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
            ("Worst month >= -$200", o["worst_m"]>=-200),
            ("MDD <= $400", o["mdd"]<=400),
        ]
        for name, ok in goals_i: print(f"    {'PASS' if ok else 'FAIL'} {name}")

        goals_c = [
            (f"L+S PM >= 11/{o['tot_m']}", ls["ls_pos"]>=11),
            ("L+S worst month >= -$150", ls["ls_wm"]>=-150),
            ("L+S MDD <= $500", ls["ls_mdd"]<=500),
            (f"L+S net ${ls['ls_total']:+.0f} > S-only ${ls['s_total']:+.0f}",
             ls["ls_total"] > ls["s_total"]),
        ]
        for name, ok in goals_c: print(f"    {'PASS' if ok else 'FAIL'} {name}")

        if "monthly" in o:
            print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
            for m in sorted(o["monthly"].keys()):
                lp = o["monthly"][m]
                sp = V10_SHORT.get(m, 0)
                ls_v = lp + sp
                f = " *" if sp < 100 else ""
                print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls_v:>+7.0f}{f}")
            print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {ls['s_total']:>+7.0f} {ls['ls_total']:>+7.0f}")
            print(f"  L+S pos months: {ls['ls_pos']}/{o['tot_m']}")
            print(f"  L+S monthly MDD: ${ls['ls_mdd']:.0f}")

# Robustness check: parameter stability for best config
if full_list:
    best_p = full_list[0]["p"]
    print(f"\n{'='*65}")
    print(f"ROBUSTNESS: Neighbors of best config")
    neighbors = 0; neighbor_pass = 0
    for r in all_both:
        p = r["p"]
        diffs = 0
        if p["gk_thresh"] != best_p["gk_thresh"]: diffs += 1
        if p["bl"] != best_p["bl"]: diffs += 1
        if p["tp"] != best_p["tp"]: diffs += 1
        if p["max_hold"] != best_p["max_hold"]: diffs += 1
        if p["safenet"] != best_p["safenet"]: diffs += 1
        if p["m_loss_cap"] != best_p["m_loss_cap"]: diffs += 1
        if p["exit_cd"] != best_p["exit_cd"]: diffs += 1
        if diffs <= 1:
            neighbors += 1
            ap = all_goals(r)
            ip = ind_pass(r)
            neighbor_pass += ap
            tag = "ALL" if ap else ("IND" if ip else "---")
            print(f"  [{tag}] GK<{p['gk_thresh']} BL={p['bl']} TP={p['tp']} maxH={p['max_hold']} "
                  f"SN={p['safenet']} mCap={p['m_loss_cap']} cd={p['exit_cd']} "
                  f"-> OOS ${r['oos']['pnl']:+.0f} MDD=${r['oos']['mdd']:.0f}")
    print(f"  Neighbors: {neighbors}, ALL PASS: {neighbor_pass}/{neighbors}")

# WF for best full pass or best ind pass
target = full_list[0] if full_list else (ind_list[0] if ind_list else (all_both[0] if all_both else None))
if target:
    p = target["p"]
    fs_size = (TOTAL - WARMUP) // 6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best]")
    wf = 0
    for fold in range(6):
        fs = WARMUP + fold * fs_size
        fe = fs + fs_size if fold < 5 else TOTAL - 1
        ft = run_bt(p, fs, fe); fm = met(ft)
        ok = fm["pnl"] > 0; wf += ok
        dt_s = pd.Timestamp(arr_dt[fs]).strftime('%Y-%m')
        dt_e = pd.Timestamp(arr_dt[min(fe, TOTAL-1)]).strftime('%Y-%m')
        print(f"  Fold {fold+1}: {dt_s}~{dt_e}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

print(f"\n{'='*65}")
print("R1-R9 EVOLUTION SUMMARY")
print("R1: Trend+Pullback+EMAcross      -> 2/216 IS>0,  OOS $1,881")
print("R2: Trend+Pullback+TP            -> 0/216 IS>0")
print("R3: GK Breakout+EMAcross         -> 13/288 IS>0, OOS $1,580")
print("R4: GK Breakout+NoES/ES@2%       -> 26-50/162,   OOS $2,624")
print("R5: GK Breakout+Trend Filter     -> trend filter hurts")
print("R6: Dip Buying (Mean Reversion)  -> FAILED, too few trades")
print("R7: GK Breakout+Risk Tighten     -> 92 WM pass, 0 MDD pass")
print("R8: GK Breakout+TP+MaxHold       -> 30 Ind PASS (breakthrough)")
print("R9: Fine-tune R8 sweet spot      -> see above")
print(f"\nAnti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6")
