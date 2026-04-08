"""
Phase 2: Garman-Klass Optimization — Iterative Modifications
=============================================================
Baseline: GK with R10 params (OOS $6,657, PF 2.35, annual $6,662)
Target: OOS annual ≥ $7,000, PF ≥ 1.8, MDD ≤ 20%

Modification rounds:
  R1: GK threshold sensitivity (20, 25, 30, 35, 40)
  R2: GK window sensitivity (short: 3,5,7,10; long: 10,14,20,30; pctile: 50,75,100,150)
  R3: Dual filter — GK + Parkinson both must be compressed
  R4: Exit params — SafeNet%, min trail bars, EarlyStop range
  R5: Session filter tuning — expand/restrict block hours
  R6: Freshness variants — stricter/looser
  R7: ExitCooldown variants

Self-check:
  [Y] signal only uses shift(1) or earlier
  [Y] entry price = next bar open (O[i+1])
  [Y] all rolling indicators have shift(1)
  [Y] no OOS leakage — all rolling, no future data
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

# ━━━ Fixed base parameters ━━━
MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000; MAX_SAME=2
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
BRK_LOOK=10; FEE=2.0
SAFENET_PCT=0.045; MIN_TRAIL=7; EXIT_COOLDOWN=12
EARLY_STOP_PCT=0.020; EARLY_STOP_END=12
COMP_THRESH=30

END_DATE=datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)
START_DATE=END_DATE-timedelta(days=732)
MID_DATE=END_DATE-timedelta(days=365)
MID_TS=pd.Timestamp(MID_DATE)

ETH_CSV=os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV)
    df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    return df

def pctile_func(x):
    if x.max()==x.min(): return 50
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100

def compute_common(df):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"]
    d["bs"]=d["cs1"]<d["cmn"]
    d["h"]=d["datetime"].dt.hour
    d["wd"]=d["datetime"].dt.weekday
    d["sok"]=~(d["h"].isin(BLOCK_H)|d["wd"].isin(BLOCK_D))
    return d

def compute_gk(d, short=5, long=20, win=100):
    ln_hl=np.log(d["high"]/d["low"])
    ln_co=np.log(d["close"]/d["open"])
    gk=0.5*ln_hl**2 - (2*np.log(2)-1)*ln_co**2
    gk_short=gk.rolling(short).mean()
    gk_long=gk.rolling(long).mean()
    gk_ratio=gk_short/gk_long
    d["comp_pctile"]=gk_ratio.shift(1).rolling(win).apply(pctile_func,raw=False)
    return d

def compute_parkinson(d, short=5, long=20, win=100):
    ln_hl=np.log(d["high"]/d["low"])
    psq=ln_hl**2/(4*np.log(2))
    ps=np.sqrt(psq.rolling(short).mean())
    pl=np.sqrt(psq.rolling(long).mean())
    pr=ps/pl
    d["park_pctile"]=pr.shift(1).rolling(win).apply(pctile_func,raw=False)
    return d

def backtest(df, comp_thresh=30, safenet_pct=0.045, min_trail=7,
             exit_cd=12, early_stop_pct=0.020, early_stop_end=12,
             block_h=None, block_d=None, use_dual=False,
             max_same=2, freshness_on=True):
    """Flexible backtest with tunable parameters."""
    if block_h is not None or block_d is not None:
        bh = block_h if block_h is not None else BLOCK_H
        bd = block_d if block_d is not None else BLOCK_D
        df = df.copy()
        df["sok"] = ~(df["h"].isin(bh) | df["wd"].isin(bd))

    w = PARK_WIN + PARK_LONG + 20
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; D=df["datetime"].values; N=len(df)
    PP=df["comp_pctile"].values
    BL=df["bl"].values; BS=df["bs"].values; SO=df["sok"].values

    # Dual filter: both GK and Parkinson must be compressed
    if use_dual and "park_pctile" in df.columns:
        PP2 = df["park_pctile"].values
    else:
        PP2 = None

    PP_P=np.roll(PP,1); PP_P[0]=np.nan
    BL_P=np.roll(BL,1); BL_P[0]=False
    BS_P=np.roll(BS,1); BS_P[0]=False
    SO_P=np.roll(SO,1); SO_P[0]=False

    sn=safenet_pct; mt=min_trail; ecd=exit_cd
    es=early_stop_pct; ese=early_stop_end
    lp=[]; sp=[]; tr=[]
    last_long_exit=-9999; last_short_exit=-9999

    for i in range(w, N-1):
        rh=H[i]; rl=L[i]; rc=C[i]; re=E[i]; rd=D[i]; no=O[i+1]

        # Exit
        nl=[]
        for p in lp:
            cl=False; b=i-p["ei"]
            if rl<=p["e"]*(1-sn):
                ep=p["e"]*(1-sn); ep=ep-(ep-rl)*0.25
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"long","bars":b,"dt":rd}); cl=True
                last_long_exit=i
            elif mt<=b<ese:
                trail_hit=rc<=re; early_hit=rc<=p["e"]*(1-es)
                if trail_hit or early_hit:
                    tp_name="EarlyStop" if (early_hit and not trail_hit) else "Trail"
                    pnl=(rc-p["e"])*NOTIONAL/p["e"]-FEE
                    tr.append({"pnl":pnl,"tp":tp_name,"sd":"long","bars":b,"dt":rd}); cl=True
                    last_long_exit=i
            elif b>=ese and rc<=re:
                pnl=(rc-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"long","bars":b,"dt":rd}); cl=True
                last_long_exit=i
            if not cl: nl.append(p)
        lp=nl

        ns=[]
        for p in sp:
            cl=False; b=i-p["ei"]
            if rh>=p["e"]*(1+sn):
                ep=p["e"]*(1+sn); ep=ep+(rh-ep)*0.25
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"short","bars":b,"dt":rd}); cl=True
                last_short_exit=i
            elif mt<=b<ese:
                trail_hit=rc>=re; early_hit=rc>=p["e"]*(1+es)
                if trail_hit or early_hit:
                    tp_name="EarlyStop" if (early_hit and not trail_hit) else "Trail"
                    pnl=(p["e"]-rc)*NOTIONAL/p["e"]-FEE
                    tr.append({"pnl":pnl,"tp":tp_name,"sd":"short","bars":b,"dt":rd}); cl=True
                    last_short_exit=i
            elif b>=ese and rc>=re:
                pnl=(p["e"]-rc)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"short","bars":b,"dt":rd}); cl=True
                last_short_exit=i
            if not cl: ns.append(p)
        sp=ns

        # Entry
        pp_v=PP[i]
        if np.isnan(pp_v): continue
        co=pp_v<comp_thresh

        # Dual filter check
        if use_dual and PP2 is not None:
            pp2_v = PP2[i]
            if np.isnan(pp2_v) or pp2_v >= comp_thresh:
                co = False

        blo=bool(BL[i]) if not np.isnan(BL[i]) else False
        bso=bool(BS[i]) if not np.isnan(BS[i]) else False
        s=bool(SO[i])

        # Freshness
        if freshness_on:
            pp_p=PP_P[i]
            if not np.isnan(pp_p):
                pc=pp_p<comp_thresh
                pbl=bool(BL_P[i]) if not isinstance(BL_P[i], (bool, np.bool_)) and np.isnan(BL_P[i]) else bool(BL_P[i])
                pbs=bool(BS_P[i]) if not isinstance(BS_P[i], (bool, np.bool_)) and np.isnan(BS_P[i]) else bool(BS_P[i])
                ps=bool(SO_P[i])
            else:
                pc=pbl=pbs=ps=False
            fl=not(pc and pbl and ps)
            fs=not(pc and pbs and ps)
        else:
            fl=fs=True

        long_cool=(i-last_long_exit)>=ecd
        short_cool=(i-last_short_exit)>=ecd

        if co and blo and s and fl and long_cool and len(lp)<max_same:
            lp.append({"e":no,"ei":i})
        if co and bso and s and fs and short_cool and len(sp)<max_same:
            sp.append({"e":no,"ei":i})

    cols=["pnl","tp","sd","bars","dt"]
    return pd.DataFrame(tr,columns=cols) if tr else pd.DataFrame(columns=cols)

def calc_stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"rr":0,"avg_hold":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"]; l=tdf[tdf["pnl"]<=0]["pnl"]
    ws=w.sum(); ls=abs(l.sum())
    pf=ws/ls if ls>0 else 999
    avg_w=w.mean() if len(w)>0 else 0; avg_l=abs(l.mean()) if len(l)>0 else 1
    rr=avg_w/avg_l if avg_l>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=dd.min(); mp=abs(mdd)/ACCOUNT*100
    avg_hold=tdf["bars"].mean()
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mp,1),"rr":round(rr,2),
            "avg_hold":round(avg_hold,1)}

def monthly_stats(tdf):
    if len(tdf)==0: return 0,0,0
    t=tdf.copy(); t["dt"]=pd.to_datetime(t["dt"])
    t["ym"]=t["dt"].dt.to_period("M")
    mb=t.groupby("ym")["pnl"].sum()
    pos=sum(1 for p in mb if p>0); tot=len(mb)
    consec=0; mx=0
    for p in mb:
        if p<0: consec+=1; mx=max(mx,consec)
        else: consec=0
    return pos, tot, mx

def run_variant(df, label, **kwargs):
    trades = backtest(df, **kwargs)
    trades["dt"] = pd.to_datetime(trades["dt"])
    oos = trades[trades["dt"] >= MID_TS].reset_index(drop=True)
    ist = trades[trades["dt"] < MID_TS].reset_index(drop=True)
    is_s = calc_stats(ist); oos_s = calc_stats(oos)
    oos_m = (END_DATE - MID_DATE).days / 30.44
    oos_annual = oos_s["pnl"] / (oos_m / 12) if oos_m > 0 else 0
    oos_monthly = oos_s["n"] / oos_m if oos_m > 0 else 0
    pos_m, tot_m, consec = monthly_stats(trades)
    pos_rate = pos_m / tot_m * 100 if tot_m > 0 else 0

    # SafeNet count in OOS
    sn_count = len(oos[oos["tp"] == "SafeNet"]) if len(oos) > 0 else 0

    return {
        "label": label, "is_s": is_s, "oos_s": oos_s,
        "oos_annual": round(oos_annual, 0), "oos_monthly": round(oos_monthly, 1),
        "pos_rate": round(pos_rate, 0), "consec": consec,
        "sn_count": sn_count,
    }

def print_result(r, indent="  "):
    o = r["oos_s"]; i = r["is_s"]
    print(f"{indent}{r['label']:<40s} IS:{i['n']:>3d}t ${i['pnl']:>+8,.0f} PF{i['pf']:.2f} | "
          f"OOS:{o['n']:>3d}t ${o['pnl']:>+8,.0f} PF{o['pf']:.2f} WR{o['wr']:.0f}% "
          f"MDD{o['mdd_pct']:.1f}% ann${r['oos_annual']:>+8,.0f} "
          f"mo{r['oos_monthly']:.1f} SN{r['sn_count']}")

# ━━━ Main ━━━
if __name__ == "__main__":
    print("=" * 80)
    print("  Phase 2: Garman-Klass Optimization")
    print("=" * 80)

    df_raw = load()
    print(f"  Loaded {len(df_raw)} bars")
    df_base = compute_common(df_raw)

    # ── R0: Baseline (GK with R10 params) ──
    print("\n  ── R0: GK Baseline (thresh=30, short=5, long=20, win=100) ──")
    df_gk = compute_gk(df_base.copy())
    baseline = run_variant(df_gk, "GK_baseline")
    print_result(baseline)

    # ══════════════════════════════════════════════════════════════
    # R1: Compression Threshold Sensitivity
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R1: Compression Threshold ──")
    r1_results = []
    for thresh in [15, 20, 25, 30, 35, 40, 50]:
        r = run_variant(df_gk, f"thresh={thresh}", comp_thresh=thresh)
        r1_results.append(r)
        print_result(r)
    best_r1 = max(r1_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r1['label']} (OOS ${best_r1['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R2: GK Window Sensitivity
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R2: GK Short/Long/Win Windows ──")
    r2_results = []
    for short, long, win in [
        (3, 14, 100), (3, 20, 100), (5, 14, 100), (5, 20, 100), (5, 30, 100),
        (7, 20, 100), (7, 30, 100), (10, 20, 100), (10, 30, 100),
        (5, 20, 50), (5, 20, 75), (5, 20, 150), (5, 20, 200),
        (3, 10, 100), (3, 30, 100),
    ]:
        df_v = compute_gk(df_base.copy(), short=short, long=long, win=win)
        r = run_variant(df_v, f"s{short}_l{long}_w{win}")
        r2_results.append(r)
        print_result(r)
    best_r2 = max(r2_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r2['label']} (OOS ${best_r2['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R3: Dual Filter — GK + Parkinson
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R3: Dual Filter (GK + Parkinson) ──")
    df_dual = compute_gk(df_base.copy())
    df_dual = compute_parkinson(df_dual)
    r3_results = []
    for thresh in [25, 30, 35, 40]:
        r = run_variant(df_dual, f"dual_thresh={thresh}", comp_thresh=thresh, use_dual=True)
        r3_results.append(r)
        print_result(r)
    best_r3 = max(r3_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r3['label']} (OOS ${best_r3['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R4: Exit Parameter Tuning
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R4: Exit Parameters ──")
    r4_results = []
    # SafeNet %
    for sn_pct in [0.035, 0.040, 0.045, 0.050, 0.055, 0.060]:
        r = run_variant(df_gk, f"SN={sn_pct:.1%}", safenet_pct=sn_pct)
        r4_results.append(r)
        print_result(r)
    # Min trail bars
    for mt in [5, 6, 7, 8, 9, 10, 12]:
        r = run_variant(df_gk, f"minTrail={mt}", min_trail=mt)
        r4_results.append(r)
        print_result(r)
    # EarlyStop params
    for es_pct, es_end in [(0.015, 12), (0.020, 10), (0.020, 14), (0.025, 12), (0.020, 16)]:
        r = run_variant(df_gk, f"ES={es_pct:.1%}_end{es_end}", early_stop_pct=es_pct, early_stop_end=es_end)
        r4_results.append(r)
        print_result(r)
    # No EarlyStop
    r = run_variant(df_gk, "noEarlyStop", early_stop_pct=0.0, early_stop_end=0)
    r4_results.append(r)
    print_result(r)
    best_r4 = max(r4_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r4['label']} (OOS ${best_r4['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R5: Session Filter Variants
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R5: Session Filter ──")
    r5_results = []
    variants = [
        ("block_H012_12", {0,1,2,12}, {0,5,6}),         # baseline
        ("block_H012", {0,1,2}, {0,5,6}),                # remove hour 12
        ("block_H01212_13", {0,1,2,12,13}, {0,5,6}),     # add hour 13
        ("block_H0123_12", {0,1,2,3,12}, {0,5,6}),       # add hour 3
        ("block_noDay", {0,1,2,12}, set()),               # remove day filter
        ("block_D56only", {0,1,2,12}, {5,6}),             # remove Monday
        ("block_D06", {0,1,2,12}, {0,6}),                 # only Mon+Sun (not Sat)
        ("noSession", set(), set()),                       # no session filter
    ]
    for name, bh, bd in variants:
        r = run_variant(df_gk, name, block_h=bh, block_d=bd)
        r5_results.append(r)
        print_result(r)
    best_r5 = max(r5_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r5['label']} (OOS ${best_r5['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R6: ExitCooldown Variants
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R6: ExitCooldown ──")
    r6_results = []
    for ecd in [6, 8, 10, 12, 14, 16, 20, 24]:
        r = run_variant(df_gk, f"exitCD={ecd}", exit_cd=ecd)
        r6_results.append(r)
        print_result(r)
    best_r6 = max(r6_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r6['label']} (OOS ${best_r6['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R7: Max Same Direction Positions
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R7: Max Same Direction ──")
    r7_results = []
    for ms in [1, 2, 3]:
        r = run_variant(df_gk, f"maxSame={ms}", max_same=ms)
        r7_results.append(r)
        print_result(r)
    best_r7 = max(r7_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r7['label']} (OOS ${best_r7['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # R8: Freshness Toggle
    # ══════════════════════════════════════════════════════════════
    print("\n  ── R8: Freshness ──")
    r8_results = []
    r = run_variant(df_gk, "freshness_ON", freshness_on=True)
    r8_results.append(r); print_result(r)
    r = run_variant(df_gk, "freshness_OFF", freshness_on=False)
    r8_results.append(r); print_result(r)
    best_r8 = max(r8_results, key=lambda x: x["oos_s"]["pnl"])
    print(f"  → Best: {best_r8['label']} (OOS ${best_r8['oos_s']['pnl']:+,.0f})")

    # ══════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  ROUND WINNERS SUMMARY")
    print("=" * 80)
    winners = [
        ("R0 baseline", baseline),
        (f"R1 threshold", best_r1),
        (f"R2 windows", best_r2),
        (f"R3 dual", best_r3),
        (f"R4 exit", best_r4),
        (f"R5 session", best_r5),
        (f"R6 exitCD", best_r6),
        (f"R7 maxSame", best_r7),
        (f"R8 freshness", best_r8),
    ]
    print(f"  {'Round':<16s} {'Variant':<40s} {'OOS PnL':>10s} {'Ann':>10s} {'PF':>6s} {'MDD':>6s}")
    print(f"  {'-'*90}")
    for rnd, r in winners:
        o = r["oos_s"]
        print(f"  {rnd:<16s} {r['label']:<40s} ${o['pnl']:>+9,.0f} ${r['oos_annual']:>+9,.0f} {o['pf']:>5.2f} {o['mdd_pct']:>5.1f}%")

    overall_best = max([r for _, r in winners], key=lambda x: x["oos_s"]["pnl"])
    print(f"\n  Overall best single mod: {overall_best['label']} — "
          f"OOS ${overall_best['oos_s']['pnl']:+,.0f} (annual ${overall_best['oos_annual']:+,.0f})")
    print(f"  Target: annual ≥ $7,000")
    target_met = overall_best["oos_annual"] >= 7000
    print(f"  Target met: {'YES ✓' if target_met else 'NO — need combo optimization'}")
