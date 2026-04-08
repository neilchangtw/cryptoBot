"""
Phase 1: Theory Exploration — 6 Compression/Entry Theories
===========================================================
Controlled experiment: only the entry COMPRESSION DETECTION varies.
Everything else is locked to R10 Final spec:
  - Close Breakout 10bar for direction
  - Session Filter (Block H{0,1,2,12}, Block D{Mon,Sat,Sun})
  - Freshness + ExitCD 12
  - SafeNet ±4.5% + EMA20 trail min 7 + EarlyStop bars 7-12 loss>2%
  - $2,000 notional, $2/trade cost

Theories tested:
  1. Parkinson Ratio Percentile (baseline R10)
  2. Garman-Klass Ratio Percentile (more efficient vol estimator)
  3. Keltner Channel Width Percentile (ATR-based compression)
  4. Choppiness Index Percentile (trend/range structure)
  5. Multi-scale Vol Cone (novel: compression at 5h+10h+20h simultaneously)
  6. Large Bar Continuation (non-compression: impulse event-driven)

Self-check (applies to ALL methods):
  [Y] signal only uses shift(1) or earlier? — All indicators shifted
  [Y] entry price = next bar open? — opens[i+1]
  [Y] all rolling indicators have shift(1)? — Yes
  [Y] parameters decided before data? — All standard/theoretical values
  [Y] no post-result adjustment? — First run, 6 methods simultaneously
  [Y] no OOS leakage? — Rolling windows only
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

# ━━━ Fixed parameters (R10 Final, locked) ━━━
MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000; MAX_SAME=2
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
BRK_LOOK=10; FEE=2.0
SAFENET_PCT=0.045; MIN_TRAIL=7; EXIT_COOLDOWN=12
EARLY_STOP_PCT=0.020; EARLY_STOP_END=12

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

# ━━━ Common indicators (used by all methods) ━━━
def compute_common(df):
    """Compute breakout, session, freshness indicators — shared across all methods."""
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()

    # Close Breakout (same as R10)
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"]
    d["bs"]=d["cs1"]<d["cmn"]

    # Session (UTC+8)
    d["h"]=d["datetime"].dt.hour
    d["wd"]=d["datetime"].dt.weekday
    d["sok"]=~(d["h"].isin(BLOCK_H)|d["wd"].isin(BLOCK_D))

    return d

def pctile_func(x):
    """Min-max percentile for rolling apply."""
    if x.max()==x.min(): return 50
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100

# ━━━ Method 1: Parkinson (baseline R10) ━━━
def compute_parkinson(d):
    ln_hl=np.log(d["high"]/d["low"])
    psq=ln_hl**2/(4*np.log(2))
    ps=np.sqrt(psq.rolling(PARK_SHORT).mean())
    pl=np.sqrt(psq.rolling(PARK_LONG).mean())
    pr=ps/pl
    d["comp_pctile"]=pr.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    return d

# ━━━ Method 2: Garman-Klass ━━━
def compute_garman_klass(d):
    """GK estimator: 0.5*ln(H/L)^2 - (2ln2-1)*ln(C/O)^2
    More efficient than Parkinson (uses OHLC, not just HL).
    Parameters: same short/long/window as Parkinson for controlled comparison."""
    ln_hl=np.log(d["high"]/d["low"])
    ln_co=np.log(d["close"]/d["open"])
    gk=0.5*ln_hl**2 - (2*np.log(2)-1)*ln_co**2
    gk_short=gk.rolling(PARK_SHORT).mean()
    gk_long=gk.rolling(PARK_LONG).mean()
    gk_ratio=gk_short/gk_long
    d["comp_pctile"]=gk_ratio.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    return d

# ━━━ Method 3: Keltner Channel Width ━━━
def compute_keltner(d):
    """KC Width = 2 * ATR_mult * ATR / EMA. Standard params: EMA20, ATR14, mult 1.5.
    Narrow channel = compression. Uses ATR (includes gaps) vs Parkinson (HL only)."""
    prev_close=d["close"].shift(1)
    tr=pd.concat([
        d["high"]-d["low"],
        (d["high"]-prev_close).abs(),
        (d["low"]-prev_close).abs()
    ], axis=1).max(axis=1)
    atr14=tr.rolling(14).mean()
    ema20=d["close"].ewm(span=20).mean()
    kc_width=2*1.5*atr14/ema20  # normalized width
    # Short/long ratio approach (consistent with Parkinson)
    kc_short=kc_width.rolling(PARK_SHORT).mean()
    kc_long=kc_width.rolling(PARK_LONG).mean()
    kc_ratio=kc_short/kc_long
    d["comp_pctile"]=kc_ratio.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    return d

# ━━━ Method 4: Choppiness Index ━━━
def compute_choppiness(d):
    """CI = 100 * log10(sum(TR, n) / (HH-LL)) / log10(n)
    High CI = choppy/range = compression. We INVERT so low pctile = compression.
    Standard param n=14."""
    prev_close=d["close"].shift(1)
    tr=pd.concat([
        d["high"]-d["low"],
        (d["high"]-prev_close).abs(),
        (d["low"]-prev_close).abs()
    ], axis=1).max(axis=1)
    sum_tr=tr.rolling(14).sum()
    hh=d["high"].rolling(14).max()
    ll=d["low"].rolling(14).min()
    denom=hh-ll
    denom=denom.replace(0,np.nan)
    ci=100*np.log10(sum_tr/denom)/np.log10(14)
    # High CI = choppy = compression. Invert: use (100-CI) so low value = compression
    ci_inv=100-ci
    ci_short=ci_inv.rolling(PARK_SHORT).mean()
    ci_long=ci_inv.rolling(PARK_LONG).mean()
    ci_ratio=ci_short/ci_long
    d["comp_pctile"]=ci_ratio.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    return d

# ━━━ Method 5: Multi-scale Vol Cone ━━━
def compute_multiscale(d):
    """Novel: Compression detected when realized vol is low at MULTIPLE timeframes.
    RV at 5h, 10h, 20h windows, each percentiled over 100 bars.
    Compression = average of 3 percentiles. Low = multi-scale compression."""
    log_ret=np.log(d["close"]/d["close"].shift(1))
    rv5=log_ret.rolling(5).std()
    rv10=log_ret.rolling(10).std()
    rv20=log_ret.rolling(20).std()
    p5=rv5.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    p10=rv10.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    p20=rv20.shift(1).rolling(PARK_WIN).apply(pctile_func,raw=False)
    d["comp_pctile"]=(p5+p10+p20)/3
    return d

# ━━━ Method 6: Large Bar Continuation (non-compression) ━━━
def compute_largebar(d):
    """Event-driven: After a large bar (range > 2x 20-bar avg range),
    enter in the bar's direction. Not compression — impulse continuation.
    'Compression flag' is always True (no compression gate), but direction
    comes from the large bar, not from close breakout."""
    bar_range=d["high"]-d["low"]
    avg_range=bar_range.rolling(20).mean()
    # Large bar: range > 2x average, shifted by 1 (use previous bar)
    large=bar_range.shift(1)>2*avg_range.shift(1)
    # Direction from large bar: bullish if close > open
    large_bull=(d["close"].shift(1)>d["open"].shift(1)) & large
    large_bear=(d["close"].shift(1)<d["open"].shift(1)) & large
    # We set comp_pctile=0 when large bar detected (always passes < 30 threshold)
    # and override breakout with large bar direction
    d["comp_pctile"]=pd.Series(50.0, index=d.index)  # default: no compression
    d["comp_pctile"]=d["comp_pctile"].where(~large, 0)  # large bar → pctile=0
    d["bl_override"]=large_bull
    d["bs_override"]=large_bear
    return d

# ━━━ Unified Backtest Engine ━━━
def backtest(df, method_name, use_override=False):
    """Run backtest with the computed comp_pctile + breakout/session/exit."""
    COMP_THRESH=30
    w=PARK_WIN+PARK_LONG+20

    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; D=df["datetime"].values; N=len(df)
    PP=df["comp_pctile"].values
    BL=df["bl"].values; BS=df["bs"].values
    SO=df["sok"].values

    # For method 6, use override directions
    if use_override and "bl_override" in df.columns:
        BL_DIR=df["bl_override"].values
        BS_DIR=df["bs_override"].values
    else:
        BL_DIR=BL; BS_DIR=BS

    # Freshness: need previous bar's conditions
    PP_P=np.roll(PP,1); PP_P[0]=np.nan
    BL_P=np.roll(BL_DIR,1); BL_P[0]=False
    BS_P=np.roll(BS_DIR,1); BS_P[0]=False
    SO_P=np.roll(SO,1); SO_P[0]=False

    sn=SAFENET_PCT; mt=MIN_TRAIL; ecd=EXIT_COOLDOWN
    es=EARLY_STOP_PCT; ese=EARLY_STOP_END
    lp=[]; sp=[]; tr=[]
    last_long_exit=-9999; last_short_exit=-9999

    for i in range(w, N-1):
        rh=H[i]; rl=L[i]; rc=C[i]; re=E[i]; rd=D[i]; no=O[i+1]

        # Exit logic (identical to R10)
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

        # Entry logic
        pp_v=PP[i]
        if np.isnan(pp_v): continue
        co=pp_v<COMP_THRESH

        blo=bool(BL_DIR[i]) if not np.isnan(BL_DIR[i]) else False
        bso=bool(BS_DIR[i]) if not np.isnan(BS_DIR[i]) else False
        s=bool(SO[i])

        pp_p=PP_P[i]
        if not np.isnan(pp_p):
            pc=pp_p<COMP_THRESH
            pbl=bool(BL_P[i]) if not isinstance(BL_P[i], (bool, np.bool_)) and np.isnan(BL_P[i]) else bool(BL_P[i])
            pbs=bool(BS_P[i]) if not isinstance(BS_P[i], (bool, np.bool_)) and np.isnan(BS_P[i]) else bool(BS_P[i])
            ps=bool(SO_P[i])
        else:
            pc=pbl=pbs=ps=False
        fl=not(pc and pbl and ps)
        fs=not(pc and pbs and ps)

        long_cool=(i-last_long_exit)>=ecd
        short_cool=(i-last_short_exit)>=ecd

        if co and blo and s and fl and long_cool and len(lp)<MAX_SAME:
            lp.append({"e":no,"ei":i})
        if co and bso and s and fs and short_cool and len(sp)<MAX_SAME:
            sp.append({"e":no,"ei":i})

    cols=["pnl","tp","sd","bars","dt"]
    return pd.DataFrame(tr,columns=cols) if tr else pd.DataFrame(columns=cols)

def calc_stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0,"sharpe":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum(); l=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l if l>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=dd.min(); mp=abs(mdd)/ACCOUNT*100
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mp,1)}

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

# ━━━ Main ━━━
if __name__=="__main__":
    print("="*70)
    print("  Phase 1: Theory Exploration — 6 Methods")
    print("="*70)

    df_raw=load()
    print(f"  Loaded {len(df_raw)} bars ({df_raw['datetime'].min()} to {df_raw['datetime'].max()})")
    df_base=compute_common(df_raw)

    methods=[
        ("1_Parkinson", compute_parkinson, False),
        ("2_GarmanKlass", compute_garman_klass, False),
        ("3_Keltner", compute_keltner, False),
        ("4_Choppiness", compute_choppiness, False),
        ("5_MultiScale", compute_multiscale, False),
        ("6_LargeBar", compute_largebar, True),
    ]

    results=[]

    for name, compute_fn, use_override in methods:
        print(f"\n  ── {name} ──")
        df=compute_fn(df_base.copy())
        trades=backtest(df, name, use_override)
        trades["dt"]=pd.to_datetime(trades["dt"])

        oos=trades[trades["dt"]>=MID_TS].reset_index(drop=True)
        ist=trades[trades["dt"]<MID_TS].reset_index(drop=True)
        is_s=calc_stats(ist); oos_s=calc_stats(oos)
        is_m=(MID_DATE-START_DATE).days/30.44
        oos_m=(END_DATE-MID_DATE).days/30.44
        oos_annual=oos_s["pnl"]/(oos_m/12) if oos_m>0 else 0
        oos_monthly=oos_s["n"]/oos_m if oos_m>0 else 0
        pos_m, tot_m, consec=monthly_stats(trades)
        pos_rate=pos_m/tot_m*100 if tot_m>0 else 0

        print(f"    IS:  {is_s['n']:>3d}t  ${is_s['pnl']:>+8,.0f}  PF {is_s['pf']:.2f}  WR {is_s['wr']:.0f}%  MDD {is_s['mdd_pct']:.1f}%")
        print(f"    OOS: {oos_s['n']:>3d}t  ${oos_s['pnl']:>+8,.0f}  PF {oos_s['pf']:.2f}  WR {oos_s['wr']:.0f}%  MDD {oos_s['mdd_pct']:.1f}%")
        print(f"    OOS annual: ${oos_annual:>+8,.0f}  monthly avg: {oos_monthly:.1f}t")
        print(f"    Positive months: {pos_m}/{tot_m} ({pos_rate:.0f}%)  Max consec losing: {consec}")

        # Exit type breakdown
        for period, subset, plabel in [("IS",ist,"IS"), ("OOS",oos,"OOS")]:
            types=[]
            for tp in ["SafeNet","EarlyStop","Trail"]:
                sub=subset[subset["tp"]==tp]
                if len(sub)>0:
                    types.append(f"{tp}:{len(sub)}(${sub['pnl'].sum():+,.0f})")
            print(f"    {plabel} exits: {', '.join(types)}")

        hold_breakdown(oos, "OOS")

        results.append({
            "name":name, "is_s":is_s, "oos_s":oos_s,
            "oos_annual":oos_annual, "oos_monthly":oos_monthly,
            "pos_rate":pos_rate, "consec":consec,
            "pos_m":pos_m, "tot_m":tot_m
        })

    # ━━━ Summary Table ━━━
    print("\n" + "="*70)
    print("  SUMMARY — All Methods Ranked by OOS PnL")
    print("="*70)
    results.sort(key=lambda x: x["oos_s"]["pnl"], reverse=True)

    print(f"  {'#':<3s} {'Method':<18s} {'OOS PnL':>10s} {'OOS PF':>8s} {'OOS MDD':>8s} {'IS PnL':>10s} {'月均':>6s} {'正月%':>6s} {'連虧':>4s}")
    print(f"  {'-'*72}")
    for i,r in enumerate(results):
        print(f"  {i+1:<3d} {r['name']:<18s} ${r['oos_s']['pnl']:>+9,.0f} {r['oos_s']['pf']:>8.2f} {r['oos_s']['mdd_pct']:>7.1f}% ${r['is_s']['pnl']:>+9,.0f} {r['oos_monthly']:>5.1f} {r['pos_rate']:>5.0f}% {r['consec']:>4d}")

    best=results[0]
    print(f"\n  Best: {best['name']} — OOS ${best['oos_s']['pnl']:+,.0f} (annual ${best['oos_annual']:+,.0f})")
    print(f"  vs R10 baseline: method 1_Parkinson")
