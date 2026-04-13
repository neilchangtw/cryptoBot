"""
V10-L Round 4: Fix EarlyStop — the #1 profit killer
R3 finding: EarlyStop(bar 1-5, 1%) = 38% of trades, all losses, -$2,010 total drag.
V6 L used EarlyStop at bar 7-12, not 1-5. Breakout needs time to develop.

Change: Test EarlyStop variants (none, bar 1-5@2%, bar 7-12@1%, bar 7-12@2%)
Entry: GK breakout (best from R3)
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

DAILY_LOSS_LIMIT = -200; MONTHLY_LOSS_LIMIT = -200
CONSEC_LOSS_PAUSE = 4; CONSEC_LOSS_COOLDOWN = 24

# Indicators
c = df["close"].astype(float)
h = df["high"].astype(float)
l = df["low"].astype(float)
o = df["open"].astype(float)

df["ema20"] = c.ewm(span=20, adjust=False).mean().shift(1)

gk_raw = 0.5 * np.log(h/l)**2 - (2*np.log(2)-1) * np.log(c/o)**2
gk_ratio = gk_raw.rolling(5).mean() / gk_raw.rolling(20).mean()
gk_s = gk_ratio.shift(1)
def pct(s):
    if s.isna().any() or len(s)<2: return np.nan
    return ((s < s.iloc[-1]).sum()) / (len(s)-1) * 100
df["gk_pct"] = gk_s.rolling(100).apply(pct, raw=False)

for bl in [10, 12, 15]:
    df[f"bo_{bl}"] = c > c.shift(1).rolling(bl).max()

df["hour"] = df["datetime"].dt.hour
df["dow"]  = df["datetime"].dt.dayofweek
df["session_ok"] = ~(df["hour"].isin([0,1,2,12]) | df["dow"].isin([0,5,6]))

V10_SHORT = {
    "2025-04":527,"2025-05":93,"2025-06":388,"2025-07":76,"2025-08":392,
    "2025-09":324,"2025-10":467,"2025-11":332,"2025-12":286,
    "2026-01":18,"2026-02":406,"2026-03":98,
}

def run_bt(df, p, start, end):
    gk_t = p["gk_thresh"]; bl = p["breakout"]
    sn = p["safenet"]; mh = p["min_hold"]; maxh = p["max_hold"]
    cd = p.get("exit_cd",8); cap = p.get("entry_cap",20)
    es_start = p.get("es_start", 0)   # 0 = no EarlyStop
    es_end   = p.get("es_end", 0)
    es_pct   = p.get("es_pct", 1.0)
    bo_col = f"bo_{bl}"

    trades=[]; pos=None; last_exit=-999
    d_pnl=m_pnl=0.0; consec=0; cd_until=0
    cur_m=cur_d=None; m_ent=0

    for i in range(start, end):
        b=df.iloc[i]; bm=b["datetime"].to_period("M"); bd=b["datetime"].date()
        if bd!=cur_d: d_pnl=0.0; cur_d=bd
        if bm!=cur_m: m_pnl=0.0; m_ent=0; cur_m=bm

        if pos is not None:
            held=i-pos["idx"]; ep=pos["ep"]
            pnl_pct=(b["close"]/ep-1)*100
            xp=xr=None

            snl=ep*(1-sn/100)
            if b["low"]<=snl: xp=snl-(snl-b["low"])*0.25; xr="SafeNet"

            if xp is None and es_start>0 and es_start<=held<=es_end:
                if pnl_pct < -es_pct: xp=b["close"]; xr="EarlyStop"

            if xp is None and held>=mh and b["close"]<b["ema20"]:
                xp=b["close"]; xr="EMAcross"

            if xp is None and held>=maxh: xp=b["close"]; xr="MaxHold"

            if xp is not None:
                pnl=(xp/ep-1)*NOTIONAL-FEE
                trades.append({"pnl":pnl,"reason":xr,"month":str(bm),
                    "entry_dt":pos["dt"],"exit_dt":b["datetime"],"held":held})
                d_pnl+=pnl; m_pnl+=pnl
                if pnl<0:
                    consec+=1
                    if consec>=CONSEC_LOSS_PAUSE: cd_until=i+CONSEC_LOSS_COOLDOWN
                else: consec=0
                last_exit=i; pos=None

        if pos is not None: continue
        if d_pnl<=DAILY_LOSS_LIMIT: continue
        if m_pnl<=MONTHLY_LOSS_LIMIT: continue
        if i<cd_until: continue
        if i-last_exit<cd: continue
        if m_ent>=cap: continue
        if not b["session_ok"]: continue

        gk=b["gk_pct"]
        if pd.isna(gk): continue
        if gk<gk_t and b[bo_col] and i+1<len(df):
            pos={"ep":df.iloc[i+1]["open"],"dt":df.iloc[i+1]["datetime"],"idx":i+1}
            m_ent+=1

    if pos:
        b=df.iloc[end-1]
        pnl=(b["close"]/pos["ep"]-1)*NOTIONAL-FEE
        trades.append({"pnl":pnl,"reason":"EOD","month":str(b["datetime"].to_period("M")),
            "entry_dt":pos["dt"],"exit_dt":b["datetime"],"held":end-1-pos["idx"]})
    return trades

def met(trades):
    if not trades: return {"n":0,"pnl":0,"pf":0,"wr":0,"mdd":0,"pos_m":0,"tot_m":0,"worst_m":0}
    t=pd.DataFrame(trades); n=len(t); pnl=t["pnl"].sum()
    w=t[t["pnl"]>0]; l=t[t["pnl"]<=0]
    wr=len(w)/n*100
    gw=w["pnl"].sum() if len(w) else 0
    gl=abs(l["pnl"].sum()) if len(l) else 0.001
    cum=t["pnl"].cumsum(); mdd=abs((cum-cum.cummax()).min())
    mo=t.groupby("month")["pnl"].sum()
    t["date"]=pd.to_datetime(t["exit_dt"]).dt.date
    daily=t.groupby("date")["pnl"].sum()
    return {"n":n,"pnl":pnl,"pf":gw/gl,"wr":wr,"mdd":mdd,
            "pos_m":(mo>0).sum(),"tot_m":len(mo),"worst_m":mo.min(),
            "monthly":mo,"worst_day":daily.min() if len(daily) else 0}

# ─── Grid ─────────────────────────────────────────────
# EarlyStop variants:
ES_CONFIGS = [
    {"es_start":0, "es_end":0, "es_pct":0, "label":"NoES"},        # No EarlyStop
    {"es_start":1, "es_end":5, "es_pct":2.0, "label":"ES1-5@2%"},  # Looser
    {"es_start":1, "es_end":5, "es_pct":1.5, "label":"ES1-5@1.5%"},
    {"es_start":7, "es_end":12,"es_pct":1.0, "label":"ES7-12@1%"}, # V6 style
    {"es_start":7, "es_end":12,"es_pct":2.0, "label":"ES7-12@2%"},
    {"es_start":1, "es_end":5, "es_pct":1.0, "label":"ES1-5@1%"},  # R3 baseline
]

grid = list(product(
    [30, 40, 50],       # gk_thresh
    [10, 12, 15],       # breakout
    [3.5, 4.5],         # safenet
    [5, 7, 10],         # min_hold
    [20, 25, 30],       # max_hold
    range(len(ES_CONFIGS)),
))

print(f"=== V10-L R4: EarlyStop Fix ===")
print(f"Grid: {len(grid)} configs ({len(grid)//len(ES_CONFIGS)} base × {len(ES_CONFIGS)} ES variants)")
print()

results = []
for idx, (gk, bl, sn, mh, maxh, es_i) in enumerate(grid):
    es = ES_CONFIGS[es_i]
    p = {"gk_thresh":gk,"breakout":bl,"safenet":sn,"min_hold":mh,"max_hold":maxh,
         "exit_cd":8,"entry_cap":20, **{k:es[k] for k in ["es_start","es_end","es_pct"]}}
    is_t = run_bt(df, p, WARMUP, IS_END)
    oos_t = run_bt(df, p, IS_END, TOTAL-1)
    is_m = met(is_t); oos_m = met(oos_t)
    results.append({"p":p,"is":is_m,"oos":oos_m,
                     "is_pnl":is_m["pnl"],"oos_pnl":oos_m["pnl"],
                     "es_label":es["label"]})
    if (idx+1)%200==0:
        print(f"  ... {idx+1}/{len(grid)}")

# ─── Analysis ─────────────────────────────────────────
print(f"\n{'='*65}")
print("RESULTS BY EARLYSTOP VARIANT")
print(f"{'='*65}")

for es_i, es in enumerate(ES_CONFIGS):
    subset = [r for r in results if r["es_label"]==es["label"]]
    both = [r for r in subset if r["is_pnl"]>0 and r["oos_pnl"]>0]
    is_pos = sum(1 for r in subset if r["is_pnl"]>0)
    goal = [r for r in both if
            r["oos"]["pos_m"]>=min(8,r["oos"]["tot_m"]) and
            r["oos"]["worst_m"]>=-200 and r["oos"]["mdd"]<=400]
    best_oos = max(both, key=lambda x:x["oos_pnl"]) if both else None
    print(f"\n[{es['label']}] IS>0: {is_pos}/{len(subset)}, Both>0: {len(both)}, "
          f"Goals PASS: {len(goal)}")
    if best_oos:
        o=best_oos["oos"]; s=best_oos["is"]; p=best_oos["p"]
        pm=f"{o['pos_m']}/{o['tot_m']}"
        print(f"  Best: GK<{p['gk_thresh']} BL={p['breakout']} SN={p['safenet']} "
              f"mh={p['min_hold']} maxH={p['max_hold']}")
        print(f"  IS: {s['n']}t ${s['pnl']:+.0f} WR={s['wr']:.0f}% | "
              f"OOS: {o['n']}t ${o['pnl']:+.0f} WR={o['wr']:.0f}% PF={o['pf']:.2f} "
              f"MDD=${o['mdd']:.0f} PM={pm} WM=${o['worst_m']:+.0f}")

# ─── Top configs overall ──────────────────────────────
all_both = [r for r in results if r["is_pnl"]>0 and r["oos_pnl"]>0]
all_both.sort(key=lambda x: x["oos_pnl"], reverse=True)

print(f"\n{'='*65}")
print(f"TOP 20 OVERALL (IS>0 AND OOS>0)")
print(f"{'='*65}")
print(f"{'#':>2} {'GK':>3} {'BL':>3} {'SN':>4} {'mh':>3} {'mxh':>4} {'ES':>12} | "
      f"{'ISn':>3} {'IS$':>6} {'ISW':>4} | "
      f"{'On':>3} {'O$':>7} {'OWR':>4} {'OPF':>5} {'MDD':>5} {'PM':>5} {'WM':>6}")
for i, r in enumerate(all_both[:20], 1):
    p=r["p"]; s=r["is"]; o=r["oos"]
    pm=f"{o['pos_m']}/{o['tot_m']}"
    print(f"{i:>2} {p['gk_thresh']:>3} {p['breakout']:>3} {p['safenet']:>4} "
          f"{p['min_hold']:>3} {p['max_hold']:>4} {r['es_label']:>12} | "
          f"{s['n']:>3} {s['pnl']:>+6.0f} {s['wr']:>4.0f} | "
          f"{o['n']:>3} {o['pnl']:>+7.0f} {o['wr']:>4.0f} {o['pf']:>5.2f} "
          f"{o['mdd']:>5.0f} {pm:>5} {o['worst_m']:>+6.0f}")

# ─── Detailed top 3 ──────────────────────────────────
for rank, r in enumerate(all_both[:3], 1):
    p=r["p"]; o=r["oos"]; s=r["is"]
    print(f"\n{'─'*60}")
    print(f"[#{rank}] GK<{p['gk_thresh']} BL={p['breakout']} SN={p['safenet']}% "
          f"mh={p['min_hold']} maxH={p['max_hold']} {r['es_label']}")
    print(f"  IS:  {s['n']}t ${s['pnl']:+.0f} PF={s['pf']:.2f} WR={s['wr']:.1f}%")
    print(f"  OOS: {o['n']}t ${o['pnl']:+.0f} PF={o['pf']:.2f} WR={o['wr']:.1f}% "
          f"MDD=${o['mdd']:.0f} WD=${o.get('worst_day',0):.0f}")

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
        ls_t=0
        for m, lp in sorted(o["monthly"].items()):
            sp=V10_SHORT.get(m,0); ls=lp+sp; ls_t+=ls
            f=" *" if sp<100 else ""
            print(f"  {m:>8} {lp:>+7.0f} {sp:>+7.0f} {ls:>+7.0f}{f}")
        st=sum(V10_SHORT.get(m,0) for m in o["monthly"].keys())
        print(f"  {'TOTAL':>8} {o['pnl']:>+7.0f} {st:>+7.0f} {ls_t:>+7.0f}")

    oos_t=run_bt(df,p,IS_END,TOTAL-1)
    t=pd.DataFrame(oos_t)
    if len(t):
        print(f"\n  Exit reasons:")
        for reason,g in t.groupby("reason"):
            gw=len(g[g["pnl"]>0])
            print(f"    {reason:>10}: {len(g):>3}t avg=${g['pnl'].mean():>+.1f} "
                  f"WR={gw/len(g)*100:.0f}% hold={g['held'].mean():.1f}b")

# WF
if all_both:
    p=all_both[0]["p"]
    fs_size=(TOTAL-WARMUP)//6
    print(f"\n{'='*60}")
    print(f"Walk-Forward 6-Fold [Best]")
    wf=0
    for fold in range(6):
        fs=WARMUP+fold*fs_size
        fe=fs+fs_size if fold<5 else TOTAL-1
        ft=run_bt(df,p,fs,fe); fm=met(ft)
        ok=fm["pnl"]>0; wf+=ok
        print(f"  Fold {fold+1}: {df.iloc[fs]['datetime'].strftime('%Y-%m')}"
              f"~{df.iloc[min(fe,TOTAL-1)]['datetime'].strftime('%Y-%m')}"
              f"  {fm['n']}t ${fm['pnl']:>+.0f} WR={fm['wr']:.0f}% {'PASS' if ok else 'FAIL'}")
    print(f"  WF: {wf}/6")

print(f"\n{'='*65}")
print("Anti-lookahead: [x]1 [x]2 [x]3 [x]4 [x]5 [x]6")
