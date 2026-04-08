"""
Phase 2 Combo: Combine best single modifications from R1-R8
============================================================
Top single-mod winners:
  R7 maxSame=3:  OOS $7,837 (annual $7,843) PF 2.37 MDD 5.2%
  R6 exitCD=20:  OOS $6,953 (annual $6,958) PF 2.78 MDD 5.3%
  R4 SN=5.5%:    OOS $6,723 (annual $6,728) PF 2.39 MDD 5.0%
  R1 thresh=40:  OOS $6,710 (annual $6,715) PF 2.20 MDD 6.0%

Combo strategy: stack compatible improvements.
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
BRK_LOOK=10; FEE=2.0

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

def backtest(df, comp_thresh=30, safenet_pct=0.045, min_trail=7,
             exit_cd=12, early_stop_pct=0.020, early_stop_end=12,
             max_same=2, freshness_on=True):
    w=PARK_WIN+PARK_LONG+20
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; D=df["datetime"].values; N=len(df)
    PP=df["comp_pctile"].values
    BL=df["bl"].values; BS=df["bs"].values; SO=df["sok"].values

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

        pp_v=PP[i]
        if np.isnan(pp_v): continue
        co=pp_v<comp_thresh

        blo=bool(BL[i]) if not np.isnan(BL[i]) else False
        bso=bool(BS[i]) if not np.isnan(BS[i]) else False
        s=bool(SO[i])

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

def hold_breakdown(tdf, label):
    print(f"    {label} Hold Time:")
    for lo,hi,lb in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
        s=tdf[(tdf["bars"]>=lo)&(tdf["bars"]<hi)]; n=len(s)
        p=s["pnl"].sum() if n>0 else 0; w=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"      {lb:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")

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
    sn_count = len(oos[oos["tp"] == "SafeNet"]) if len(oos) > 0 else 0
    return {
        "label": label, "is_s": is_s, "oos_s": oos_s, "oos": oos, "ist": ist,
        "oos_annual": round(oos_annual, 0), "oos_monthly": round(oos_monthly, 1),
        "pos_rate": round(pos_rate, 0), "consec": consec, "sn_count": sn_count,
        "trades": trades,
    }

def print_result(r, indent="  "):
    o = r["oos_s"]; i = r["is_s"]
    print(f"{indent}{r['label']:<45s} IS:{i['n']:>3d}t ${i['pnl']:>+8,.0f} PF{i['pf']:.2f} | "
          f"OOS:{o['n']:>3d}t ${o['pnl']:>+8,.0f} PF{o['pf']:.2f} WR{o['wr']:.0f}% "
          f"MDD{o['mdd_pct']:.1f}% ann${r['oos_annual']:>+8,.0f} "
          f"mo{r['oos_monthly']:.1f} SN{r['sn_count']} hold{o['avg_hold']:.0f}h")

def walk_forward(df, n_folds=10, **kwargs):
    """Rolling walk-forward: train on 1 fold, test on next fold."""
    N = len(df)
    fold_size = N // (n_folds + 1)
    results = []
    for fold in range(n_folds):
        test_start = (fold + 1) * fold_size
        test_end = test_start + fold_size
        if test_end > N: break
        test_df = df.iloc[:test_end].copy()
        trades = backtest(test_df, **kwargs)
        trades["dt"] = pd.to_datetime(trades["dt"])
        cutoff = test_df.iloc[test_start]["datetime"]
        fold_trades = trades[trades["dt"] >= cutoff]
        fold_pnl = fold_trades["pnl"].sum() if len(fold_trades) > 0 else 0
        results.append({"fold": fold + 1, "pnl": round(fold_pnl, 2),
                        "n": len(fold_trades),
                        "positive": fold_pnl > 0})
    return results

if __name__ == "__main__":
    print("=" * 80)
    print("  Phase 2 Combo: Stacking Best Single Modifications")
    print("=" * 80)

    df_raw = load()
    print(f"  Loaded {len(df_raw)} bars")
    df_base = compute_common(df_raw)
    df_gk = compute_gk(df_base.copy())

    # ── Combo variants ──
    combos = [
        ("C0: GK baseline",
         dict(comp_thresh=30, safenet_pct=0.045, min_trail=7, exit_cd=12, max_same=2)),
        ("C1: maxSame=3 only",
         dict(comp_thresh=30, safenet_pct=0.045, min_trail=7, exit_cd=12, max_same=3)),
        ("C2: maxSame=3 + exitCD=20",
         dict(comp_thresh=30, safenet_pct=0.045, min_trail=7, exit_cd=20, max_same=3)),
        ("C3: maxSame=3 + SN=5.5%",
         dict(comp_thresh=30, safenet_pct=0.055, min_trail=7, exit_cd=12, max_same=3)),
        ("C4: maxSame=3 + thresh=40",
         dict(comp_thresh=40, safenet_pct=0.045, min_trail=7, exit_cd=12, max_same=3)),
        ("C5: maxSame=3 + exitCD=20 + SN=5.5%",
         dict(comp_thresh=30, safenet_pct=0.055, min_trail=7, exit_cd=20, max_same=3)),
        ("C6: maxSame=3 + exitCD=20 + thresh=40",
         dict(comp_thresh=40, safenet_pct=0.045, min_trail=7, exit_cd=20, max_same=3)),
        ("C7: maxSame=3 + exitCD=20 + SN=5.5% + thresh=40",
         dict(comp_thresh=40, safenet_pct=0.055, min_trail=7, exit_cd=20, max_same=3)),
        ("C8: maxSame=3 + exitCD=16",
         dict(comp_thresh=30, safenet_pct=0.045, min_trail=7, exit_cd=16, max_same=3)),
        ("C9: maxSame=3 + exitCD=14 + SN=5.0%",
         dict(comp_thresh=30, safenet_pct=0.050, min_trail=7, exit_cd=14, max_same=3)),
        ("C10: maxSame=3 + SN=5.0%",
         dict(comp_thresh=30, safenet_pct=0.050, min_trail=7, exit_cd=12, max_same=3)),
        ("C11: maxSame=3 + exitCD=20 + SN=5.0%",
         dict(comp_thresh=30, safenet_pct=0.050, min_trail=7, exit_cd=20, max_same=3)),
        ("C12: maxSame=3 + exitCD=18",
         dict(comp_thresh=30, safenet_pct=0.045, min_trail=7, exit_cd=18, max_same=3)),
    ]

    print("\n  ── All Combos ──")
    all_results = []
    for label, params in combos:
        r = run_variant(df_gk, label, **params)
        all_results.append((r, params))
        print_result(r)

    # Sort by OOS PnL
    all_results.sort(key=lambda x: x[0]["oos_s"]["pnl"], reverse=True)

    print("\n" + "=" * 80)
    print("  TOP 5 COMBOS BY OOS PnL")
    print("=" * 80)
    for i, (r, params) in enumerate(all_results[:5]):
        print_result(r, f"  #{i+1} ")

    # ── Detailed analysis of the champion ──
    champ, champ_params = all_results[0]
    print(f"\n  === CHAMPION: {champ['label']} ===")
    o = champ["oos_s"]; i = champ["is_s"]
    print(f"  IS:  {i['n']}t  ${i['pnl']:+,.0f}  PF {i['pf']:.2f}  WR {i['wr']:.0f}%  MDD {i['mdd_pct']:.1f}%  Avg hold {i['avg_hold']:.0f}h")
    print(f"  OOS: {o['n']}t  ${o['pnl']:+,.0f}  PF {o['pf']:.2f}  WR {o['wr']:.0f}%  MDD {o['mdd_pct']:.1f}%  Avg hold {o['avg_hold']:.0f}h")
    print(f"  OOS annual: ${champ['oos_annual']:+,.0f}  monthly: {champ['oos_monthly']:.1f}t  SafeNet: {champ['sn_count']}")
    print(f"  Positive months: {champ['pos_rate']:.0f}%  Max consec losing: {champ['consec']}")

    # Exit breakdown
    oos = champ["oos"]
    print(f"\n  OOS Exit Breakdown:")
    for tp in ["SafeNet", "EarlyStop", "Trail"]:
        sub = oos[oos["tp"] == tp]
        if len(sub) > 0:
            print(f"    {tp:<12s}: {len(sub):>3d}t  ${sub['pnl'].sum():>+8,.0f}  WR {(sub['pnl']>0).mean()*100:.0f}%  avg hold {sub['bars'].mean():.0f}h")

    # Long/Short breakdown
    print(f"\n  OOS Long/Short:")
    for sd in ["long", "short"]:
        sub = oos[oos["sd"] == sd]
        if len(sub) > 0:
            print(f"    {sd:<6s}: {len(sub):>3d}t  ${sub['pnl'].sum():>+8,.0f}  WR {(sub['pnl']>0).mean()*100:.0f}%")

    hold_breakdown(oos, "OOS")

    # Monthly PnL
    print(f"\n  OOS Monthly PnL:")
    t = oos.copy(); t["dt"] = pd.to_datetime(t["dt"])
    t["ym"] = t["dt"].dt.to_period("M")
    mb = t.groupby("ym")["pnl"].sum()
    for ym, pnl in mb.items():
        flag = " <<<" if pnl < -200 else ""
        print(f"    {ym}: ${pnl:>+8,.0f}{flag}")

    # Walk-forward
    print(f"\n  Walk-Forward (10 folds):")
    wf = walk_forward(df_gk, n_folds=10, **champ_params)
    wf_total = sum(r["pnl"] for r in wf)
    wf_pos = sum(1 for r in wf if r["positive"])
    for r in wf:
        flag = "+" if r["positive"] else "-"
        print(f"    Fold {r['fold']:>2d}: {r['n']:>3d}t  ${r['pnl']:>+8,.0f}  [{flag}]")
    print(f"    Total: ${wf_total:>+8,.0f}  Positive folds: {wf_pos}/{len(wf)}")

    # Final assessment
    print(f"\n  === FINAL ASSESSMENT ===")
    print(f"  Champion: {champ['label']}")
    print(f"  OOS annual: ${champ['oos_annual']:+,.0f}")
    checks = [
        ("OOS >= $7,000/yr", champ["oos_annual"] >= 7000),
        ("PF >= 1.8", o["pf"] >= 1.8),
        ("MDD <= 20%", o["mdd_pct"] <= 20),
        ("WR >= 35%", o["wr"] >= 35),
        ("WF positive folds >= 6/10", wf_pos >= 6),
    ]
    for desc, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"    [{status}] {desc}")
    all_pass = all(p for _, p in checks)
    print(f"\n  All targets met: {'YES' if all_pass else 'NO'}")
    print(f"  Parameters: {champ_params}")
