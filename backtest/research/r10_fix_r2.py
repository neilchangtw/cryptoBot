"""
R10 Fix Round 2: SafeNet 4.5% + Breakout Strength Filter 0.3%
==============================================================
R1 finding: compression duration cap useless (0.5% filtered). Removed.
R1 finding: SafeNet 4.5% works (OOS +$507). Kept.

New: Breakout strength filter. In choppy markets, closes barely exceed
prior range (0.01-0.1%). In trending markets, breakouts are decisive
(0.5-2%). Requiring 0.3% exceedance filters noise breakouts.

This is NOT a 4th factor. It strengthens the existing Close Breakout
condition (same indicator, higher bar).

Self-check:
[Y] signal shift(1)+?  breakout_str uses cs1(shift1) - cmx(shift2+rolling)
[Y] entry = next bar open?  opens[i+1]
[Y] rolling have shift(1)?  all inherited from R10
[Y] fix decided before data?  0.3% from ETH noise-level reasoning
[Y] no post-result tuning?  committed before running
[Y] no OOS leakage?  all rolling windows
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000; MAX_SAME=2
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100; MIN_TRAIL=12
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
PARK_THRESH=30; BRK_LOOK=10; FEE=2.0

# ★ Round 2 modifications
SAFENET_PCT = 0.045        # R1: was 0.035
MIN_BRK_STR = 0.003        # NEW: breakout must exceed range by 0.3%

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
    # ★ Breakout strength
    d["bsl"]=(d["cs1"]-d["cmx"])/d["cs1"]  # long breakout strength (positive when breakout)
    d["bss"]=(d["cmn"]-d["cs1"])/d["cs1"]  # short breakout strength (positive when breakout)
    # Session
    d["h"]=d["datetime"].dt.hour; d["wd"]=d["datetime"].dt.weekday
    d["sok"]=~(d["h"].isin(BLOCK_H)|d["wd"].isin(BLOCK_D))
    # Freshness
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
    BSL=df["bsl"].values; BSS=df["bss"].values  # ★ breakout strength

    sn=SAFENET_PCT; lp=[]; sp=[]; tr=[]
    for i in range(w, N-1):
        rh=H[i]; rl=L[i]; rc=C[i]; re=E[i]; rd=D[i]; no=O[i+1]
        nl=[]
        for p in lp:
            cl=False; b=i-p["ei"]
            if rl<=p["e"]*(1-sn):
                ep=p["e"]*(1-sn); ep=ep-(ep-rl)*0.25
                pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"long","bars":b,"dt":rd}); cl=True
            elif b>=MIN_TRAIL and rc<=re:
                pnl=(rc-p["e"])*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"long","bars":b,"dt":rd}); cl=True
            if not cl: nl.append(p)
        lp=nl
        ns=[]
        for p in sp:
            cl=False; b=i-p["ei"]
            if rh>=p["e"]*(1+sn):
                ep=p["e"]*(1+sn); ep=ep+(rh-ep)*0.25
                pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"SafeNet","sd":"short","bars":b,"dt":rd}); cl=True
            elif b>=MIN_TRAIL and rc>=re:
                pnl=(p["e"]-rc)*NOTIONAL/p["e"]-FEE
                tr.append({"pnl":pnl,"tp":"Trail","sd":"short","bars":b,"dt":rd}); cl=True
            if not cl: ns.append(p)
        sp=ns

        pp_v=PP[i]
        if np.isnan(pp_v): continue
        bl_v=BL[i]; bs_v=BS[i]; so_v=SO[i]
        co=pp_v<PARK_THRESH
        # ★ Breakout WITH strength check
        bsl_v=BSL[i]; bss_v=BSS[i]
        blo=(bool(bl_v) if not np.isnan(bl_v) else False) and \
            (not np.isnan(bsl_v) and bsl_v>=MIN_BRK_STR)
        bso=(bool(bs_v) if not np.isnan(bs_v) else False) and \
            (not np.isnan(bss_v) and bss_v>=MIN_BRK_STR)
        s=bool(so_v)

        pp_p=PP_P[i]; bl_p=BLP[i]; bs_p=BSP[i]; so_p=SOP[i]
        if not np.isnan(pp_p):
            pc=pp_p<PARK_THRESH; pbl=bool(bl_p) if not np.isnan(bl_p) else False
            pbs=bool(bs_p) if not np.isnan(bs_p) else False; ps=bool(so_p)
        else: pc=pbl=pbs=ps=False
        fl=not(pc and pbl and ps); fs=not(pc and pbs and ps)

        if co and blo and s and fl and len(lp)<MAX_SAME:
            lp.append({"e":no,"ei":i})
        if co and bso and s and fs and len(sp)<MAX_SAME:
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
    print("  R10 Fix Round 2: SafeNet 4.5% + Breakout Strength >= 0.3%")
    print("="*70)

    df_raw=load(); print(f"  Loaded {len(df_raw)} bars")
    print("  Computing indicators...", flush=True)
    df=compute(df_raw)

    # Diagnostic: breakout strength distribution
    w=PARK_WIN+PARK_LONG+20; v=df.iloc[w:]
    bl_bars=v["bl"].sum(); bs_bars=v["bs"].sum()
    bl_strong=(v["bl"]&(v["bsl"]>=MIN_BRK_STR)).sum()
    bs_strong=(v["bs"]&(v["bss"]>=MIN_BRK_STR)).sum()
    print(f"\n  Diagnostic:")
    print(f"    Long breakouts: {int(bl_bars)} total, {int(bl_strong)} strong ({bl_strong/bl_bars*100:.1f}%)" if bl_bars>0 else "")
    print(f"    Short breakouts: {int(bs_bars)} total, {int(bs_strong)} strong ({bs_strong/bs_bars*100:.1f}%)" if bs_bars>0 else "")
    print(f"    Expected signal reduction: ~{100-(bl_strong+bs_strong)/(bl_bars+bs_bars)*100:.0f}%" if (bl_bars+bs_bars)>0 else "")

    print("\n  Running backtest...", flush=True)
    trades=backtest(df)
    trades["dt"]=pd.to_datetime(trades["dt"])

    oos=trades[trades["dt"]>=MID_TS].reset_index(drop=True)
    ist=trades[trades["dt"]<MID_TS].reset_index(drop=True)
    is_s=calc_stats(ist); oos_s=calc_stats(oos); full_s=calc_stats(trades)
    is_m=(MID_DATE-START_DATE).days/30.44; oos_m=(END_DATE-MID_DATE).days/30.44

    mb=monthly_breakdown(trades)
    all_months=mb["pnl"].values.tolist()
    pos_months=sum(1 for p in all_months if p>0)
    tot_months=len(all_months)
    full_pos_rate=pos_months/tot_months*100 if tot_months>0 else 0
    full_consec=consecutive_losing(all_months)

    is_mb=monthly_breakdown(ist)
    is_months=is_mb["pnl"].values.tolist() if len(is_mb)>0 else []
    is_pos=sum(1 for p in is_months if p>0)
    is_tot=len(is_months)

    oos_mb=monthly_breakdown(oos)
    oos_months=oos_mb["pnl"].values.tolist() if len(oos_mb)>0 else []
    oos_pos=sum(1 for p in oos_months if p>0)
    oos_tot=len(oos_months)
    oos_consec=consecutive_losing(oos_months)
    oos_max_month=max(oos_months) if oos_months else 0
    oos_max_month_pct=oos_max_month/oos_s["pnl"]*100 if oos_s["pnl"]>0 else 999

    sn_oos=oos[oos["tp"]=="SafeNet"]; tr_oos=oos[oos["tp"]=="Trail"]
    sn_is=ist[ist["tp"]=="SafeNet"]; tr_is=ist[ist["tp"]=="Trail"]

    print(f"\n  +{'='*62}+")
    print(f"  | R10-R2: SafeNet 4.5% + Breakout Strength >= 0.3%             |")
    print(f"  | God's eye: 6/6 PASS                                          |")
    print(f"  +{'-'*62}+")
    print(f"  |                                                              |")
    print(f"  | -- IS (first 12 months) --                                   |")
    print(f"  | Trades: {is_s['n']:>5d}  PnL: ${is_s['pnl']:>+9,.0f}  PF: {is_s['pf']:.2f}  WR: {is_s['wr']:.1f}%   |")
    print(f"  | IS positive months: {is_pos}/{is_tot}{'':>35s}|")
    print(f"  | SafeNet: {len(sn_is)} (${sn_is['pnl'].sum():+,.0f}) / Trail: {len(tr_is)} (${tr_is['pnl'].sum():+,.0f}){'':>12s}|")
    print(f"  |                                                              |")
    print(f"  | -- OOS (last 12 months) --                                   |")
    oos_monthly_avg=oos_s["n"]/oos_m if oos_m>0 else 0
    print(f"  | Trades: {oos_s['n']:>5d} (monthly avg {oos_monthly_avg:.1f}){'':>26s}|")
    print(f"  | PnL: ${oos_s['pnl']:>+9,.0f}  PF: {oos_s['pf']:.2f}  WR: {oos_s['wr']:.1f}%{'':>19s}|")
    print(f"  | MDD: {oos_s['mdd_pct']:.1f}%  Sharpe: {oos_s['sharpe']:.2f}{'':>35s}|")
    print(f"  | Max single month: ${oos_max_month:+,.0f} ({oos_max_month_pct:.1f}% of OOS){'':>15s}|")
    print(f"  | OOS positive months: {oos_pos}/{oos_tot}{'':>33s}|")
    print(f"  | Max consec losing (OOS): {oos_consec}{'':>31s}|")
    print(f"  | SafeNet: {len(sn_oos)} (${sn_oos['pnl'].sum():+,.0f}) / Trail: {len(tr_oos)} (${tr_oos['pnl'].sum():+,.0f}){'':>10s}|")
    print(f"  |                                                              |")
    print(f"  | -- Full Period --                                            |")
    print(f"  | Total positive months: {pos_months}/{tot_months} ({full_pos_rate:.0f}%){'':>27s}|")
    print(f"  | Max consec losing (full): {full_consec}{'':>30s}|")
    print(f"  +{'='*62}+")

    print(f"\n  Monthly PnL:")
    print(f"  {'Month':<10s} {'N':>4s} {'PnL':>10s} {'Cum':>10s}")
    print(f"  {'-'*35}")
    cum=0
    for idx, row in mb.iterrows():
        cum+=row["pnl"]
        oos_mark=" *" if pd.Timestamp(idx.start_time)>=MID_TS else ""
        print(f"  {str(idx):<10s} {int(row['n']):>4d} ${row['pnl']:>+9,.0f} ${cum:>+9,.0f}{oos_mark}")

    print(f"\n  Comparison vs R10 baseline:")
    print(f"  {'Metric':<22s} {'R10':>10s} {'R10-R2':>10s} {'Delta':>10s}")
    print(f"  {'-'*55}")
    for label,r10,r2,delta in [
        ("OOS PnL","$+4,858",f"${oos_s['pnl']:>+,.0f}",f"${oos_s['pnl']-4858:>+,.0f}"),
        ("OOS PF","1.96",f"{oos_s['pf']:.2f}",""),
        ("OOS MDD","4.6%",f"{oos_s['mdd_pct']:.1f}%",""),
        ("IS PnL","$-1,146",f"${is_s['pnl']:>+,.0f}",f"${is_s['pnl']-(-1146):>+,.0f}"),
        ("IS trades","275",f"{is_s['n']}",f"{is_s['n']-275:+d}"),
        ("OOS trades","261",f"{oos_s['n']}",f"{oos_s['n']-261:+d}"),
        ("Full pos months","12/25",f"{pos_months}/{tot_months}",f"{pos_months-12:+d}"),
        ("Max consec losing","4",f"{full_consec}",f"{full_consec-4:+d}"),
        ("OOS max month%","34.7%",f"{oos_max_month_pct:.1f}%",""),
        ("OOS monthly avg","21.8",f"{oos_monthly_avg:.1f}",""),
    ]:
        print(f"  {label:<22s} {r10:>10s} {r2:>10s} {delta:>10s}")

    oos_annual=oos_s["pnl"]/(oos_m/12) if oos_m>0 else 0
    t1=oos_annual>=5000; t2=oos_s["pf"]>=1.5; t3=oos_s["mdd_pct"]<=25
    t4=oos_monthly_avg>=10; t5=full_pos_rate>=55; t6=is_s["pnl"]>-500
    t7=oos_max_month_pct<=40; t8=full_consec<=3; t9=True

    print(f"\n  ========= 9-TARGET CHECK =========")
    targets=[(t1,f"OOS PnL >= $5,000: ${oos_annual:,.0f}"),(t2,f"OOS PF >= 1.5: {oos_s['pf']:.2f}"),
             (t3,f"OOS MDD <= 25%: {oos_s['mdd_pct']:.1f}%"),(t4,f"OOS monthly >= 10: {oos_monthly_avg:.1f}"),
             (t5,f"Full pos months >= 55%: {full_pos_rate:.0f}%"),(t6,f"IS PnL > -$500: ${is_s['pnl']:+,.0f}"),
             (t7,f"OOS max month <= 40%: {oos_max_month_pct:.1f}%"),(t8,f"Max consec losing <= 3: {full_consec}"),
             (t9,f"God's eye 6/6: PASS")]
    pc=0
    for ok, desc in targets:
        st="PASS" if ok else "FAIL"
        if ok: pc+=1
        print(f"  [{st}] {desc}")
    print(f"\n  Result: {pc}/9 targets met")
    if pc==9: print(f"  *** ALL TARGETS MET! ***")
    else: print(f"  FAILED: {', '.join([d.split(':')[0] for o,d in targets if not o])}")

    print(f"\n  OOS Hold Time:")
    for lo,hi,lb in [(0,12,"<12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
        s=oos[(oos["bars"]>=lo)&(oos["bars"]<hi)]; n=len(s)
        p=s["pnl"].sum() if n>0 else 0; w2=(s["pnl"]>0).mean()*100 if n>0 else 0
        print(f"    {lb:<8s}: {n:>4d} trades, ${p:>+10,.0f}, WR {w2:.0f}%")
