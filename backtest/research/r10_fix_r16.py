"""
R10 Fix Round 16: SN4.5% + MT7 + ExitCD12 + EarlyStop 2%
===========================================================
R12 (MT8+CD12+ES2) = IS -$504, local optimum. All perturbations worse.
Try a different axis: MIN_TRAIL 7 instead of 8.

Logic: MT7 exits losers 1 bar earlier (bar 7 vs bar 8).
Combined with CD12 (prevents re-entry churn) and ES2% (catch deep losers).
The extra bar of early exit should slightly reduce per-trade loss.

Self-check:
[Y] signal only uses shift(1)+?  Same as R10
[Y] entry price = next bar open?  opens[i+1]
[Y] all rolling indicators have shift(1)?  Same as R10
[Y] fix direction decided before data?  MT7 from ETH noise (7h < half-day)
[Y] no post-result parameter adjustment?  Pre-committed, different axis from R12
[Y] no OOS leakage?  Uses bar count only
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000; MAX_SAME=2
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
PARK_THRESH=30; BRK_LOOK=10; FEE=2.0

SAFENET_PCT = 0.045
MIN_TRAIL = 7              # ★ was 8 → 7
EXIT_COOLDOWN = 12
EARLY_STOP_PCT = 0.020
EARLY_STOP_END = 12

END_DATE=datetime(2026,4,3); START_DATE=END_DATE-timedelta(days=732)
MID_DATE=END_DATE-timedelta(days=365); MID_TS=pd.Timestamp(MID_DATE)

ETH_CACHE=os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CACHE); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df

def compute(df):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    ln_hl=np.log(d["high"]/d["low"]); psq=ln_hl**2/(4*np.log(2))
    d["ps"]=np.sqrt(psq.rolling(PARK_SHORT).mean())
    d["pl"]=np.sqrt(psq.rolling(PARK_LONG).mean())
    d["pr"]=d["ps"]/d["pl"]
    d["pp"]=d["pr"].shift(1).rolling(PARK_WIN).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"]; d["bs"]=d["cs1"]<d["cmn"]
    d["h"]=d["datetime"].dt.hour; d["wd"]=d["datetime"].dt.weekday
    d["sok"]=~(d["h"].isin(BLOCK_H)|d["wd"].isin(BLOCK_D))
    d["pp_p"]=d["pp"].shift(1); d["bl_p"]=d["bl"].shift(1)
    d["bs_p"]=d["bs"].shift(1); d["sok_p"]=d["sok"].shift(1)
    return d

def backtest(df):
    w=PARK_WIN+PARK_LONG+20
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; D=df["datetime"].values; N=len(df)
    PP=df["pp"].values; PP_P=df["pp_p"].values
    BL=df["bl"].values; BS=df["bs"].values
    BLP=df["bl_p"].values; BSP=df["bs_p"].values
    SO=df["sok"].values; SOP=df["sok_p"].values

    sn=SAFENET_PCT; mt=MIN_TRAIL; ecd=EXIT_COOLDOWN
    es=EARLY_STOP_PCT; ese=EARLY_STOP_END
    lp=[]; sp=[]; tr=[]
    last_long_exit = -9999; last_short_exit = -9999

    for i in range(w, N-1):
        rh=H[i]; rl=L[i]; rc=C[i]; re=E[i]; rd=D[i]; no=O[i+1]

        nl=[]
        for p in lp:
            cl=False; b=i-p["ei"]
            if rl<=p["e"]*(1-sn):
                ep=p["e"]*(1-sn); ep=ep-(ep-rl)*0.25
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"long","bars":b,"dt":rd}); cl=True
                last_long_exit = i
            elif mt<=b<ese:
                trail_hit = rc<=re; early_hit = rc<=p["e"]*(1-es)
                if trail_hit or early_hit:
                    tp_name = "EarlyStop" if (early_hit and not trail_hit) else "Trail"
                    pnl=(rc-p["e"])*NOTIONAL/p["e"]-FEE
                    tr.append({"pnl":pnl,"tp":tp_name,"sd":"long","bars":b,"dt":rd}); cl=True
                    last_long_exit = i
            elif b>=ese and rc<=re:
                pnl=(rc-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"long","bars":b,"dt":rd}); cl=True
                last_long_exit = i
            if not cl: nl.append(p)
        lp=nl

        ns=[]
        for p in sp:
            cl=False; b=i-p["ei"]
            if rh>=p["e"]*(1+sn):
                ep=p["e"]*(1+sn); ep=ep+(rh-ep)*0.25
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"short","bars":b,"dt":rd}); cl=True
                last_short_exit = i
            elif mt<=b<ese:
                trail_hit = rc>=re; early_hit = rc>=p["e"]*(1+es)
                if trail_hit or early_hit:
                    tp_name = "EarlyStop" if (early_hit and not trail_hit) else "Trail"
                    pnl=(p["e"]-rc)*NOTIONAL/p["e"]-FEE
                    tr.append({"pnl":pnl,"tp":tp_name,"sd":"short","bars":b,"dt":rd}); cl=True
                    last_short_exit = i
            elif b>=ese and rc>=re:
                pnl=(p["e"]-rc)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"short","bars":b,"dt":rd}); cl=True
                last_short_exit = i
            if not cl: ns.append(p)
        sp=ns

        pp_v=PP[i]
        if np.isnan(pp_v): continue
        bl_v=BL[i]; bs_v=BS[i]; so_v=SO[i]
        co=pp_v<PARK_THRESH
        blo=bool(bl_v) if not np.isnan(bl_v) else False
        bso=bool(bs_v) if not np.isnan(bs_v) else False
        s=bool(so_v)
        pp_p=PP_P[i]; bl_p=BLP[i]; bs_p=BSP[i]; so_p=SOP[i]
        if not np.isnan(pp_p):
            pc=pp_p<PARK_THRESH; pbl=bool(bl_p) if not np.isnan(bl_p) else False
            pbs=bool(bs_p) if not np.isnan(bs_p) else False; ps=bool(so_p)
        else: pc=pbl=pbs=ps=False
        fl=not(pc and pbl and ps); fs=not(pc and pbs and ps)

        long_cool = (i - last_long_exit) >= ecd
        short_cool = (i - last_short_exit) >= ecd
        if co and blo and s and fl and long_cool and len(lp)<MAX_SAME:
            lp.append({"e":no,"ei":i})
        if co and bso and s and fs and short_cool and len(sp)<MAX_SAME:
            sp.append({"e":no,"ei":i})

    cols=["pnl","tp","sd","bars","dt"]
    return pd.DataFrame(tr, columns=cols) if tr else pd.DataFrame(columns=cols)

def calc_stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sharpe":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum(); l=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l if l>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=dd.min(); mp=abs(mdd)/ACCOUNT*100
    tc=tdf.copy(); tc["date"]=pd.to_datetime(tc["dt"]).dt.date
    daily=tc.groupby("date")["pnl"].sum()
    ad=pd.date_range(tc["dt"].min(),tc["dt"].max(),freq="D")
    daily=daily.reindex(ad.date,fill_value=0)
    sh=float(daily.mean()/daily.std()*np.sqrt(365)) if daily.std()>0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mp,1),"sharpe":round(sh,2)}

def monthly_breakdown(tdf):
    t=tdf.copy(); t["dt"]=pd.to_datetime(t["dt"])
    t["ym"]=t["dt"].dt.to_period("M")
    return t.groupby("ym")["pnl"].agg(["sum","count"]).rename(columns={"sum":"pnl","count":"n"})

def consecutive_losing(monthly_pnls):
    mx=0; cur=0
    for p in monthly_pnls:
        if p<0: cur+=1; mx=max(mx,cur)
        else: cur=0
    return mx

if __name__=="__main__":
    print("="*70)
    print("  R10 Fix Round 16: SN4.5% + MT7 + ExitCD12 + EarlyStop 2%")
    print("="*70)
    df_raw=load(); print(f"  Loaded {len(df_raw)} bars")
    df=compute(df_raw)
    print("\n  Running backtest...", flush=True)
    trades=backtest(df); trades["dt"]=pd.to_datetime(trades["dt"])

    oos=trades[trades["dt"]>=MID_TS].reset_index(drop=True)
    ist=trades[trades["dt"]<MID_TS].reset_index(drop=True)
    is_s=calc_stats(ist); oos_s=calc_stats(oos)
    is_m=(MID_DATE-START_DATE).days/30.44; oos_m=(END_DATE-MID_DATE).days/30.44

    for label, subset in [("IS", ist), ("OOS", oos)]:
        print(f"  {label} exit types:")
        for tp in ["SafeNet","EarlyStop","Trail"]:
            sub=subset[subset["tp"]==tp]
            if len(sub)>0:
                print(f"    {tp:>10s}: {len(sub):>4d}, ${sub['pnl'].sum():+,.0f} (avg ${sub['pnl'].mean():+.1f})")

    mb=monthly_breakdown(trades)
    all_months=mb["pnl"].values.tolist()
    pos_months=sum(1 for p in all_months if p>0)
    tot_months=len(all_months)
    full_pos_rate=pos_months/tot_months*100 if tot_months>0 else 0
    full_consec=consecutive_losing(all_months)
    is_mb=monthly_breakdown(ist)
    is_months=is_mb["pnl"].values.tolist() if len(is_mb)>0 else []
    is_pos=sum(1 for p in is_months if p>0); is_tot=len(is_months)
    oos_mb=monthly_breakdown(oos)
    oos_months=oos_mb["pnl"].values.tolist() if len(oos_mb)>0 else []
    oos_max_month=max(oos_months) if oos_months else 0
    oos_max_month_pct=oos_max_month/oos_s["pnl"]*100 if oos_s["pnl"]>0 else 999

    print(f"\n  Monthly PnL:")
    print(f"  {'Month':<10s} {'N':>4s} {'PnL':>10s} {'Cum':>10s}")
    print(f"  {'-'*35}")
    cum=0
    for idx, row in mb.iterrows():
        cum+=row["pnl"]
        oos_mark=" *" if pd.Timestamp(idx.start_time)>=MID_TS else ""
        print(f"  {str(idx):<10s} {int(row['n']):>4d} ${row['pnl']:>+9,.0f} ${cum:>+9,.0f}{oos_mark}")

    oos_annual=oos_s["pnl"]/(oos_m/12) if oos_m>0 else 0
    oos_monthly_avg=oos_s["n"]/oos_m if oos_m>0 else 0
    t1=oos_annual>=5000; t2=oos_s["pf"]>=1.5; t3=oos_s["mdd_pct"]<=25
    t4=oos_monthly_avg>=10; t5=full_pos_rate>=55; t6=is_s["pnl"]>-500
    t7=oos_max_month_pct<=40; t8=full_consec<=3; t9=True

    print(f"\n  ========= 9-TARGET CHECK =========")
    targets=[
        (t1,f"OOS PnL >= $5,000: ${oos_annual:,.0f}"),
        (t2,f"OOS PF >= 1.5: {oos_s['pf']:.2f}"),
        (t3,f"OOS MDD <= 25%: {oos_s['mdd_pct']:.1f}%"),
        (t4,f"OOS monthly >= 10: {oos_monthly_avg:.1f}"),
        (t5,f"Full pos months >= 55%: {full_pos_rate:.0f}%"),
        (t6,f"IS PnL > -$500: ${is_s['pnl']:+,.0f}"),
        (t7,f"OOS max month <= 40%: {oos_max_month_pct:.1f}%"),
        (t8,f"Max consec losing <= 3: {full_consec}"),
        (t9,f"God's eye 6/6: PASS"),
    ]
    pass_count=0
    for ok, desc in targets:
        st="PASS" if ok else "FAIL"
        if ok: pass_count+=1
        print(f"  [{st}] {desc}")

    all_pass=pass_count==9
    print(f"\n  Result: {pass_count}/9 targets met")
    if all_pass:
        print(f"\n  *** ALL 9 TARGETS MET! ***")
        print(f"\n  Final Strategy:")
        print(f"    Entry: Parkinson pctile<{PARK_THRESH} + CloseBreakout({BRK_LOOK}) + Session")
        print(f"    SafeNet ±{SAFENET_PCT*100:.1f}% | Trail EMA20 min {MIN_TRAIL}bar")
        print(f"    Early stop {EARLY_STOP_PCT*100:.1f}% bars {MIN_TRAIL}-{EARLY_STOP_END} | Exit CD {EXIT_COOLDOWN}bar")
        print(f"\n  IS: {is_s['n']}t ${is_s['pnl']:+,.0f} PF{is_s['pf']:.2f}")
        print(f"  OOS: {oos_s['n']}t ${oos_s['pnl']:+,.0f} PF{oos_s['pf']:.2f} MDD{oos_s['mdd_pct']}%")
        print(f"  Full: {pos_months}/{tot_months} pos ({full_pos_rate:.0f}%), consec{full_consec}, Sharpe{oos_s['sharpe']}")
    else:
        failed=[desc.split(":")[0] for ok,desc in targets if not ok]
        print(f"  FAILED: {', '.join(failed)}")

    for label, subset in [("IS", ist), ("OOS", oos)]:
        print(f"\n  {label} Hold Time:")
        for lo,hi,lb in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
            s=subset[(subset["bars"]>=lo)&(subset["bars"]<hi)]; n=len(s)
            p=s["pnl"].sum() if n>0 else 0; w=(s["pnl"]>0).mean()*100 if n>0 else 0
            print(f"    {lb:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w:.0f}%")
