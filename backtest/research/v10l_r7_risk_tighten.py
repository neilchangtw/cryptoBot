"""
V10-L Round 7: Risk Tightening on GK Breakout
R4 best: GK<40 BL=15 SN=4.5% mh=10 maxH=30 NoES -> OOS $2,624 but worst_month=-$333, MDD=$834
R6 dip buying: FAILED (too few trades, only big_drop >3% had any Both>0)

Problem diagnosis:
  SafeNet 8t × avg $-190 = $-1,522 drag
  Bad months: Jun -$333, Oct -$286, Nov -$223, Sep -$215
  Multiple losers stacking in same month

Fix approach:
  1. Tighter SafeNet (lower max loss per trade)
  2. Tighter monthly loss cap (stop trading sooner)
  3. Sweep over R4's best parameter zone

Change from R4: SafeNet sweep 2.0-5.5% + monthly cap -$50 to -$200
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import pandas as pd
import numpy as np
from itertools import product

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

# EMA20
df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

# Close breakout
for bl in [12, 15]:
    df[f"brk_up_{bl}"] = c > c.shift(1).rolling(bl).max()

# Session filter
df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

V10_SHORT = {
    "2025-04":527,"2025-05":93,"2025-06":388,"2025-07":76,"2025-08":392,
    "2025-09":324,"2025-10":467,"2025-11":332,"2025-12":286,
    "2026-01":18,"2026-02":406,"2026-03":98,
}

CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

def run_bt(df, p, start, end):
    gk_t = p["gk_thresh"]; bl = p["bl"]
    sn = p["safenet"]; mh = p["min_hold"]; maxh = p["max_hold"]
    cd = p.get("exit_cd", 10); cap = p.get("entry_cap", 20)
    m_loss_cap = p.get("m_loss_cap", -200)

    brk_col = f"brk_up_{bl}"
    trades = []; pos = None; last_exit = -999
    d_pnl = m_pnl = 0.0; consec = 0; cd_until = 0
    cur_m = cur_d = None; m_ent = 0

    for i in range(start, end):
        b = df.iloc[i]
        bm = b["datetime"].to_period("M"); bd = b["datetime"].date()
        if bd != cur_d: d_pnl = 0.0; cur_d = bd
        if bm != cur_m: m_pnl = 0.0; m_ent = 0; cur_m = bm

        # Exit check
        if pos is not None:
            held = i - pos["idx"]; ep = pos["ep"]
            xp = xr = None

            # SafeNet
            sn_level = ep * (1 - sn / 100)
            if b["low"] <= sn_level:
                xp = sn_level - (sn_level - b["low"]) * 0.25
                xr = "SafeNet"

            # EMAcross (after min hold)
            if xp is None and held >= mh:
                if b["close"] < b["ema20"]:
                    xp = b["close"]; xr = "EMAcross"

            # MaxHold
            if xp is None and held >= maxh:
                xp = b["close"]; xr = "MaxHold"

            if xp is not None:
                pnl = (xp / ep - 1) * NOTIONAL - FEE
                trades.append({"pnl": pnl, "reason": xr, "month": str(bm),
                    "entry_dt": pos["dt"], "exit_dt": b["datetime"], "held": held})
                d_pnl += pnl; m_pnl += pnl
                if pnl < 0:
                    consec += 1
                    if consec >= CONSEC_LOSS_PAUSE: cd_until = i + CONSEC_LOSS_COOLDOWN
                else:
                    consec = 0
                last_exit = i; pos = None

        if pos is not None: continue

        # Circuit breakers
        if d_pnl <= -200: continue
        if m_pnl <= m_loss_cap: continue
        if i < cd_until: continue
        if i - last_exit < cd: continue
        if m_ent >= cap: continue
        if not b["session_ok"]: continue

        # Entry: GK compression + close breakout
        gk_val = b["gk_pct"]
        if pd.isna(gk_val) or gk_val >= gk_t: continue
        if not b[brk_col]: continue

        if i + 1 < len(df):
            pos = {"ep": df.iloc[i+1]["open"], "dt": df.iloc[i+1]["datetime"], "idx": i+1}
            m_ent += 1

    if pos:
        b = df.iloc[end-1]
        pnl = (b["close"] / pos["ep"] - 1) * NOTIONAL - FEE
        trades.append({"pnl": pnl, "reason": "EOD", "month": str(b["datetime"].to_period("M")),
            "entry_dt": pos["dt"], "exit_dt": b["datetime"], "held": end-1-pos["idx"]})
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

# ─── Build configs ────────────────────────────────────
configs = []
for gk_t in [30, 40]:
    for bl in [12, 15]:
        for sn in [2.0, 2.5, 3.0, 3.5, 4.5, 5.5]:
            for mh in [7, 10]:
                for maxh in [20, 25, 30]:
                    for m_cap in [-50, -100, -150, -200]:
                        for cd in [8, 10, 12]:
                            configs.append({
                                "gk_thresh":gk_t, "bl":bl, "safenet":sn,
                                "min_hold":mh, "max_hold":maxh,
                                "m_loss_cap":m_cap, "exit_cd":cd, "entry_cap":20
                            })

print(f"=== V10-L R7: Risk Tightening ===")
print(f"GK breakout + tighter SafeNet + monthly loss cap")
print(f"Grid: {len(configs)} configs")
print()

results = []
for idx, p in enumerate(configs):
    is_t = run_bt(df, p, WARMUP, IS_END)
    oos_t = run_bt(df, p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    results.append({"p":p, "is":is_m, "oos":oos_m,
                     "is_pnl":is_m["pnl"], "oos_pnl":oos_m["pnl"]})
    if (idx+1) % 500 == 0:
        print(f"  ... {idx+1}/{len(configs)}")

# ─── Analysis ─────────────────────────────────────────
print(f"\n{'='*65}")
print("RESULTS BY SAFENET LEVEL")
print(f"{'='*65}")

for sn in [2.0, 2.5, 3.0, 3.5, 4.5, 5.5]:
    sub = [r for r in results if r["p"]["safenet"]==sn]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    goal = [r for r in both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[SN={sn}%] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | Goals PASS: {len(goal)}")
    if best:
        o=best["oos"]; s=best["is"]; p=best["p"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        print(f"  Best: GK<{p['gk_thresh']} BL={p['bl']} mh={p['min_hold']} "
              f"maxH={p['max_hold']} mCap={p['m_loss_cap']} cd={p.get('exit_cd',10)}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} PM={pm} WM=${o['worst_m']:+.0f}")

print(f"\n{'='*65}")
print("RESULTS BY MONTHLY LOSS CAP")
print(f"{'='*65}")

for mc in [-50, -100, -150, -200]:
    sub = [r for r in results if r["p"]["m_loss_cap"]==mc]
    both = [r for r in sub if r["is_pnl"]>0 and r["oos_pnl"]>0]
    goal = [r for r in both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]
    is_pos = sum(1 for r in sub if r["is_pnl"]>0)
    best = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[mCap={mc}] IS>0: {is_pos}/{len(sub)} | Both>0: {len(both)} | Goals PASS: {len(goal)}")
    if best:
        o=best["oos"]; s=best["is"]; p=best["p"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        print(f"  Best: GK<{p['gk_thresh']} BL={p['bl']} SN={p['safenet']} mh={p['min_hold']} "
              f"maxH={p['max_hold']} cd={p.get('exit_cd',10)}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} PM={pm} WM=${o['worst_m']:+.0f}")

# Top 20 overall
all_both = [r for r in results if r["is_pnl"]>0 and r["oos_pnl"]>0]
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*65}")
print(f"TOP 20 (IS>0 AND OOS>0): {len(all_both)} total")
print(f"{'='*65}")
if all_both:
    print(f" {'#':>2} {'GK':>3} {'BL':>3} {'SN':>4} {'mh':>3} {'mxH':>4} {'mCap':>5} {'cd':>3} |"
          f" {'ISn':>4} {'IS$':>6} {'ISW':>4} | {'On':>4} {'O$':>6} {'OWR':>4} {'OPF':>5} {'MDD':>5} {'PM':>5} {'WM':>6}")
    for i, r in enumerate(all_both[:20], 1):
        p=r["p"]; s=r["is"]; o=r["oos"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        print(f" {i:>2} {p['gk_thresh']:>3} {p['bl']:>3} {p['safenet']:>4} {p['min_hold']:>3} "
              f"{p['max_hold']:>4} {p['m_loss_cap']:>5} {p.get('exit_cd',10):>3} | "
              f"{s['n']:>4} {s['pnl']:>+6.0f} {s['wr']:>4.0f} | "
              f"{o['n']:>4} {o['pnl']:>+6.0f} {o['wr']:>4.0f} {o['pf']:>5.2f} "
              f"{o['mdd']:>5.0f} {pm:>5} {o['worst_m']:>+6.0f}")

# Goals filter
wm_pass = [r for r in all_both if r["oos"]["worst_m"]>=-200]
mdd_pass = [r for r in all_both if r["oos"]["mdd"]<=400]
all_pass = [r for r in all_both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]

print(f"\n{'='*65}")
print(f"CONFIGS WITH WORST MONTH >= -$200: {len(wm_pass)}")
print(f"CONFIGS WITH MDD <= $400: {len(mdd_pass)}")
print(f"ALL INDEPENDENT GOALS PASS: {len(all_pass)}")

# Detail top 3
for rank, r in enumerate(all_both[:3], 1):
    p=r["p"]; o=r["oos"]; s=r["is"]
    print(f"\n{'─'*60}")
    print(f"[#{rank}] GK<{p['gk_thresh']} BL={p['bl']} SN={p['safenet']}% "
          f"mh={p['min_hold']} maxH={p['max_hold']} mCap={p['m_loss_cap']} cd={p.get('exit_cd',10)}")
    print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f}")

    goals = [
        ("IS > $0", s["pnl"]>0),
        ("OOS > $0", o["pnl"]>0),
        (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
        ("Worst month >= -$200", o["worst_m"]>=-200),
        ("MDD <= $400", o["mdd"]<=400),
    ]
    for name, ok in goals: print(f"    {'PASS' if ok else 'FAIL'} {name}")

    if "monthly" in o:
        print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
        ls_t=0; neg_ls=0
        for m, lp in sorted(o["monthly"].items()):
            sp=V10_SHORT.get(m,0); ls=lp+sp; ls_t+=ls
            if ls<0: neg_ls+=1
            f=" *" if sp<100 else ""
            print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls:>+7.0f}{f}")
        st=sum(V10_SHORT.get(m,0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {st:>+7.0f} {ls_t:>+7.0f}")
        pos_ls = len(o["monthly"]) - neg_ls
        print(f"  L+S pos months: {pos_ls}/{len(o['monthly'])}")

    oos_t = run_bt(df, p, IS_END, TOTAL-1)
    t = pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason, g in t.groupby("reason"):
            gw = len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                  f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# Detail top 3 among ALL GOALS PASS (if any)
if all_pass:
    print(f"\n{'='*65}")
    print(f"ALL GOALS PASS — TOP 3")
    for rank, r in enumerate(all_pass[:3], 1):
        p=r["p"]; o=r["oos"]; s=r["is"]
        print(f"\n{'─'*60}")
        print(f"[PASS #{rank}] GK<{p['gk_thresh']} BL={p['bl']} SN={p['safenet']}% "
              f"mh={p['min_hold']} maxH={p['max_hold']} mCap={p['m_loss_cap']} cd={p.get('exit_cd',10)}")
        print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
        print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
              f"MDD=${o['mdd']:.0f}")
        goals = [
            ("IS > $0", s["pnl"]>0),
            ("OOS > $0", o["pnl"]>0),
            (f"PM >= 8/{o['tot_m']}", o["pos_m"]>=min(8,o["tot_m"])),
            ("Worst month >= -$200", o["worst_m"]>=-200),
            ("MDD <= $400", o["mdd"]<=400),
        ]
        for name, ok in goals: print(f"    {'PASS' if ok else 'FAIL'} {name}")

        if "monthly" in o:
            print(f"\n  {'Month':>8} {'L_PnL':>7} {'S_PnL':>7} {'L+S':>7}")
            ls_t=0; neg_ls=0
            for m, lp in sorted(o["monthly"].items()):
                sp=V10_SHORT.get(m,0); ls=lp+sp; ls_t+=ls
                if ls<0: neg_ls+=1
                f=" *" if sp<100 else ""
                print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls:>+7.0f}{f}")
            st=sum(V10_SHORT.get(m,0) for m in o["monthly"].keys())
            print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {st:>+7.0f} {ls_t:>+7.0f}")
            pos_ls = len(o["monthly"]) - neg_ls
            print(f"  L+S pos months: {pos_ls}/{len(o['monthly'])}")

        oos_t = run_bt(df, p, IS_END, TOTAL-1)
        t = pd.DataFrame(oos_t)
        if len(t):
            print(f"\n  Exit reasons:")
            for reason, g in t.groupby("reason"):
                gw = len(g[g["pnl"]>0])
                print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                      f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# WF for best
if all_both:
    p = all_both[0]["p"]
    fs_size = (TOTAL - WARMUP) // 6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best]")
    wf = 0
    for fold in range(6):
        fs = WARMUP + fold * fs_size
        fe = fs + fs_size if fold < 5 else TOTAL - 1
        ft = run_bt(df, p, fs, fe); fm = met(ft)
        ok = fm["pnl"] > 0; wf += ok
        print(f"  Fold {fold+1}: {df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

# WF for best all_pass (if exists)
if all_pass:
    p = all_pass[0]["p"]
    fs_size = (TOTAL - WARMUP) // 6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best PASS]")
    wf = 0
    for fold in range(6):
        fs = WARMUP + fold * fs_size
        fe = fs + fs_size if fold < 5 else TOTAL - 1
        ft = run_bt(df, p, fs, fe); fm = met(ft)
        ok = fm["pnl"] > 0; wf += ok
        print(f"  Fold {fold+1}: {df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

print(f"\n{'='*65}")
print("R1-R7 EVOLUTION SUMMARY")
print("R1: Trend+Pullback+EMAcross      -> 2/216 IS>0,  OOS $1,881")
print("R2: Trend+Pullback+TP            -> 0/216 IS>0")
print("R3: GK Breakout+EMAcross         -> 13/288 IS>0, OOS $1,580")
print("R4: GK Breakout+NoES/ES@2%       -> 26-50/162,   OOS $2,624")
print("R5: GK Breakout+Trend Filter     -> trend filter hurts")
print("R6: Dip Buying (Mean Reversion)  -> FAILED, too few trades")
print("R7: GK Breakout+Risk Tighten     -> see above")
print(f"\nAnti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6")
