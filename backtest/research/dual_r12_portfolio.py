"""
Round 12: Portfolio Scaling to $10K
====================================
R11 best: 3x portfolio (gk40BL8 + gk40BL15 + gk30BL10) CD6 = $7,504 OOS.
  PF 1.68, topM 17%, MDD 13.6%. Passes anti-mirage.

Gap: $2,500 to reach $10K.

Plan:
  A. Add 4th/5th sub-strategy to the 3x portfolio
  B. Mix TP% across sub-strategies (some TP2%, some TP3%)
  C. Try non-GK compression signals as additional sub-strategy
  D. Best portfolio with pyramid on individual sub-strategies
  E. Check MDD and max concurrent positions
"""
import os, sys, io, numpy as np, pandas as pd, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; EXIT_CD_DEFAULT=6
GK_SHORT=5; GK_LONG=20; GK_WIN=100

BASE=os.path.dirname(os.path.abspath(__file__))
ETH_CSV=os.path.normpath(os.path.join(BASE,"..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CSV); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume","qv","trades","tbv"]:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).reset_index(drop=True)

def pctile(x):
    if x.max()==x.min(): return 50
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100

def calc(df):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ret"]=d["close"].pct_change()
    # GK compression
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    for t in [20,25,30,35,40,50]:
        d[f"gk{t}"]=d["gk_pct"]<t
    # ATR compression
    d["atr14"]=(d["high"]-d["low"]).rolling(14).mean()
    d["atr_pct"]=d["atr14"].shift(1).rolling(100).apply(pctile)
    for t in [30,40]:
        d[f"atr{t}"]=d["atr_pct"]<t
    # Breakout for multiple lookbacks
    d["cs1"]=d["close"].shift(1)
    for bl in [5,8,10,12,15,20,25]:
        d[f"cmn{bl}"]=d["close"].shift(2).rolling(bl-1).min()
        d[f"bs{bl}"]=d["cs1"]<d[f"cmn{bl}"]
    # Session filter
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_cmp(df, max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12,
           sn_pct=SN_PCT, exit_cd=EXIT_CD_DEFAULT, brk_look=10, max_pyramid=1):
    W=160; H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    DT=df["datetime"].values; bs_col=f"bs{brk_look}"; BSa=df[bs_col].values
    SOKa=df["sok"].values; sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,dt,nxo = H[i],L[i],C[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            if h>=p["e"]*(1+sn_pct):
                ep_=p["e"]*(1+sn_pct); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":b,"dt":dt}); lx=i; done=True
            if not done and lo<=p["e"]*(1-tp_pct):
                ep_=p["e"]*(1-tp_pct)
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TP","b":b,"dt":dt}); lx=i; done=True
            if not done and b>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":b,"dt":dt}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        n_sigs=sum(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if n_sigs>=1 and brk and sok and (i-lx>=exit_cd) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
                pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_portfolio(df, strats):
    all_trades = []
    for cfg in strats:
        t = bt_cmp(df, **cfg)
        all_trades.append(t)
    merged = pd.concat(all_trades, ignore_index=True)
    return merged.sort_values("dt").reset_index(drop=True)

def evaluate(tdf, mid_dt, label, show=True):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    oos=tdf[tdf["dt"]>=mid_dt].reset_index(drop=True)
    is_=tdf[tdf["dt"]<mid_dt].reset_index(drop=True)
    if len(oos)==0:
        if show: print(f"  {label}: no OOS trades"); return None
    pnl=oos["pnl"].sum(); n=len(oos)
    w=oos[oos["pnl"]>0]["pnl"].sum(); l_=abs(oos[oos["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999; wr=(oos["pnl"]>0).mean()*100
    eq=oos["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100
    oos["m"]=oos["dt"].dt.to_period("M")
    ms=oos.groupby("m")["pnl"].sum()
    top_m=ms.max(); top_pct=top_m/pnl*100 if pnl>0 else 999
    no_best=pnl-top_m
    nb_oos=oos[oos["m"]!=ms.idxmax()]
    nb_w=nb_oos[nb_oos["pnl"]>0]["pnl"].sum() if len(nb_oos)>0 else 0
    nb_l=abs(nb_oos[nb_oos["pnl"]<=0]["pnl"].sum()) if len(nb_oos)>0 else 0
    nb_pf=nb_w/nb_l if nb_l>0 else 999
    is_pnl=is_["pnl"].sum() if len(is_)>0 else 0; is_n=len(is_)
    mirage=top_pct>60 or no_best<=0 or nb_pf<1.0
    tag="MIRAGE!" if mirage else ("TARGET!" if pnl>=10000 else f"${pnl:+,.0f}")
    if show:
        print(f"\n  {label} [{tag}]")
        print(f"    IS:{is_n:>4d}t ${is_pnl:>+7,.0f}  OOS:{n:>4d}t ${pnl:>+7,.0f} PF{pf:.2f} WR{wr:.0f}% MDD{mdd:.1f}%")
        print(f"    topM:{top_pct:.0f}%  -best:${no_best:>+6,.0f} PF{nb_pf:.2f}  avg${pnl/n:+.1f}")
    return {"pnl":pnl,"pf":pf,"top_pct":top_pct,"no_best":no_best,"nb_pf":nb_pf,
            "mdd":mdd,"n":n,"wr":wr,"mirage":mirage,"is_pnl":is_pnl,"avg":pnl/n,"label":label}

if __name__=="__main__":
    print("="*70)
    print("  ROUND 12: PORTFOLIO SCALING TO $10K")
    print("="*70)
    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)
    results=[]

    # ===== Baseline: Reproduce R11 best =====
    print(f"\n  BASELINE: R11 best 3x portfolio")
    base3 = [
        dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6),
        dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6),
        dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6),
    ]
    t = bt_portfolio(df, base3)
    r = evaluate(t, mid_dt, "BASE 3x (gk40BL8 + gk40BL15 + gk30BL10)")
    if r: results.append(r)

    # ===== Phase A: Add 4th sub-strategy =====
    print(f"\n  PHASE A: ADD 4th SUB-STRATEGY to base 3x")
    extras = [
        ("gk50BL10",  dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6)),
        ("gk50BL12",  dict(max_same=5, signal_cols=["gk50"], brk_look=12, exit_cd=6)),
        ("gk50BL15",  dict(max_same=5, signal_cols=["gk50"], brk_look=15, exit_cd=6)),
        ("gk40BL12",  dict(max_same=5, signal_cols=["gk40"], brk_look=12, exit_cd=6)),
        ("gk40BL20",  dict(max_same=5, signal_cols=["gk40"], brk_look=20, exit_cd=6)),
        ("gk35BL10",  dict(max_same=5, signal_cols=["gk35"], brk_look=10, exit_cd=6)),
        ("gk35BL12",  dict(max_same=5, signal_cols=["gk35"], brk_look=12, exit_cd=6)),
        ("gk25BL10",  dict(max_same=5, signal_cols=["gk25"], brk_look=10, exit_cd=6)),
        ("gk20BL10",  dict(max_same=5, signal_cols=["gk20"], brk_look=10, exit_cd=6)),
        ("atr30BL10", dict(max_same=5, signal_cols=["atr30"], brk_look=10, exit_cd=6)),
        ("atr40BL10", dict(max_same=5, signal_cols=["atr40"], brk_look=10, exit_cd=6)),
        ("gk40BL25",  dict(max_same=5, signal_cols=["gk40"], brk_look=25, exit_cd=6)),
        ("gk30BL8",   dict(max_same=5, signal_cols=["gk30"], brk_look=8, exit_cd=6)),
        ("gk30BL15",  dict(max_same=5, signal_cols=["gk30"], brk_look=15, exit_cd=6)),
        ("gk30BL20",  dict(max_same=5, signal_cols=["gk30"], brk_look=20, exit_cd=6)),
    ]
    for name, cfg in extras:
        strats = base3 + [cfg]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid_dt, f"4x: base3+{name}")
        if r: results.append(r)

    # ===== Phase B: 5x portfolios with best 4th choices =====
    print(f"\n  PHASE B: 5x PORTFOLIOS")
    # Pick the 4-6 best 4th sub-strategies and try 5th combos
    fives = [
        ("5x: g40B8+g40B15+g30B10+g50B10+g35B12",
         base3 + [
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6),
             dict(max_same=5, signal_cols=["gk35"], brk_look=12, exit_cd=6),
         ]),
        ("5x: g40B8+g40B15+g30B10+g40B20+g50B12",
         base3 + [
             dict(max_same=5, signal_cols=["gk40"], brk_look=20, exit_cd=6),
             dict(max_same=5, signal_cols=["gk50"], brk_look=12, exit_cd=6),
         ]),
        ("5x: g40B8+g40B15+g30B10+g50B10+g40B20",
         base3 + [
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6),
             dict(max_same=5, signal_cols=["gk40"], brk_look=20, exit_cd=6),
         ]),
        ("5x: g40B8+g40B15+g30B10+g35B10+g50B15",
         base3 + [
             dict(max_same=5, signal_cols=["gk35"], brk_look=10, exit_cd=6),
             dict(max_same=5, signal_cols=["gk50"], brk_look=15, exit_cd=6),
         ]),
        ("5x: g40B8+g40B15+g30B10+g30B15+g50B10",
         base3 + [
             dict(max_same=5, signal_cols=["gk30"], brk_look=15, exit_cd=6),
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6),
         ]),
        ("5x: g40B8+g40B15+g30B10+atr30B10+g50B10",
         base3 + [
             dict(max_same=5, signal_cols=["atr30"], brk_look=10, exit_cd=6),
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6),
         ]),
    ]
    for name, strats in fives:
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid_dt, name)
        if r: results.append(r)

    # ===== Phase C: Mix TP% across sub-strategies =====
    print(f"\n  PHASE C: MIXED TP% in portfolio")
    # Some sub-strategies with TP3% MH24 (bigger wins), others TP2% MH12
    mixed_tps = [
        ("3x MIX: g40B8-TP2 + g40B15-TP3M24 + g30B10-TP2",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.02, max_hold=12),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.02, max_hold=12),
         ]),
        ("3x MIX: g40B8-TP3M24 + g40B15-TP2 + g30B10-TP3M24",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.02, max_hold=12),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
         ]),
        ("3x ALL-TP3M24: g40B8 + g40B15 + g30B10",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
         ]),
        ("3x ALL-TP4M24: g40B8 + g40B15 + g30B10",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.04, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.04, max_hold=24),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.04, max_hold=24),
         ]),
        ("4x MIX: g40B8-TP3M24 + g40B15-TP2 + g30B10-TP3M24 + g50B10-TP2",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.02, max_hold=12),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6, tp_pct=0.02, max_hold=12),
         ]),
        ("4x ALL-TP3M24: g40B8 + g40B15 + g30B10 + g50B10",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
         ]),
        ("5x ALL-TP3M24: g40B8+g40B15+g30B10+g50B10+g35B12",
         [
             dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk50"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
             dict(max_same=5, signal_cols=["gk35"], brk_look=12, exit_cd=6, tp_pct=0.03, max_hold=24),
         ]),
    ]
    for name, strats in mixed_tps:
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid_dt, name)
        if r: results.append(r)

    # ===== Phase D: Best portfolio + maxSame / CD tuning =====
    print(f"\n  PHASE D: maxSame and CD TUNING on portfolios")
    for ms in [3, 5, 7, 10]:
        for cd in [3, 6, 9]:
            strats = [
                dict(max_same=ms, signal_cols=["gk40"], brk_look=8, exit_cd=cd, tp_pct=0.03, max_hold=24),
                dict(max_same=ms, signal_cols=["gk40"], brk_look=15, exit_cd=cd, tp_pct=0.03, max_hold=24),
                dict(max_same=ms, signal_cols=["gk30"], brk_look=10, exit_cd=cd, tp_pct=0.03, max_hold=24),
            ]
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid_dt, f"3x-TP3M24 m{ms} CD{cd}")
            if r: results.append(r)

    # ===== Phase E: 6x and 7x mega-portfolios =====
    print(f"\n  PHASE E: MEGA-PORTFOLIOS (6-7 sub-strategies)")
    mega6 = [
        dict(max_same=3, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=0.03, max_hold=24),
        dict(max_same=3, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
        dict(max_same=3, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
        dict(max_same=3, signal_cols=["gk50"], brk_look=10, exit_cd=6, tp_pct=0.03, max_hold=24),
        dict(max_same=3, signal_cols=["gk35"], brk_look=12, exit_cd=6, tp_pct=0.03, max_hold=24),
        dict(max_same=3, signal_cols=["gk40"], brk_look=20, exit_cd=6, tp_pct=0.03, max_hold=24),
    ]
    t = bt_portfolio(df, mega6)
    r = evaluate(t, mid_dt, "6x-TP3M24 m3 CD6")
    if r: results.append(r)

    mega7 = mega6 + [
        dict(max_same=3, signal_cols=["gk50"], brk_look=15, exit_cd=6, tp_pct=0.03, max_hold=24),
    ]
    t = bt_portfolio(df, mega7)
    r = evaluate(t, mid_dt, "7x-TP3M24 m3 CD6")
    if r: results.append(r)

    # Also try larger maxSame on mega
    mega6_m5 = [dict(**s, **{"max_same": 5}) if "max_same" not in s else {**s, "max_same": 5} for s in mega6]
    # fix: rebuild properly
    mega6_m5 = []
    for s in mega6:
        s2 = dict(s); s2["max_same"] = 5; mega6_m5.append(s2)
    t = bt_portfolio(df, mega6_m5)
    r = evaluate(t, mid_dt, "6x-TP3M24 m5 CD6")
    if r: results.append(r)

    # ===== Phase F: TP/MH optimization on 3x portfolio =====
    print(f"\n  PHASE F: TP/MH SWEEP on 3x portfolio")
    for tp in [0.02, 0.025, 0.03, 0.035, 0.04, 0.05]:
        for mh in [12, 16, 24, 36]:
            strats = [
                dict(max_same=5, signal_cols=["gk40"], brk_look=8, exit_cd=6, tp_pct=tp, max_hold=mh),
                dict(max_same=5, signal_cols=["gk40"], brk_look=15, exit_cd=6, tp_pct=tp, max_hold=mh),
                dict(max_same=5, signal_cols=["gk30"], brk_look=10, exit_cd=6, tp_pct=tp, max_hold=mh),
            ]
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid_dt, f"3x TP{tp*100:.1f} MH{mh}", show=False)
            if r and not r["mirage"] and r["pnl"] >= 8000:
                evaluate(t, mid_dt, f"3x TP{tp*100:.1f} MH{mh}")  # re-print promising ones
                results.append(r)

    # ===== Summary =====
    print(f"\n{'='*70}")
    print(f"  TOP 20 NON-MIRAGE CONFIGS (sorted by OOS PnL)")
    print(f"{'='*70}")
    valid = [r for r in results if not r["mirage"]]
    valid.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(valid[:20]):
        print(f"  {i+1:>2}. {r['label']:<55s} ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.0f}% "
              f"MDD{r['mdd']:.1f}% topM{r['top_pct']:.0f}% avg${r['avg']:+.1f}")

    print(f"\n  Total configs tested: {len(results)}")
    print(f"  Non-mirage: {len(valid)}")
    target_hit = [r for r in valid if r["pnl"] >= 10000]
    print(f"  TARGET ($10K+): {len(target_hit)}")
    print(f"  ROUND 12 COMPLETE")
