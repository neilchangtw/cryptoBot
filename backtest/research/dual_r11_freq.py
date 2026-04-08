"""
Round 11: Frequency Unlocking for CMP Short
=============================================
R10 found: CMP ceiling ~$3-4.5K. Key bottleneck = EXIT_CD=12.
  With max_hold=12, effective cycle = 24 bars per trade.
  Reducing EXIT_CD could unlock 2-4x more trades.

Exploration plan:
  A. EXIT_CD variations (0,3,6,9,12) on gk40 best config
  B. Breakout lookback (5,8,10,15,20) — different lookbacks = different entries
  C. GK_thresh × EXIT_CD × BRK_LOOK grid on promising combos
  D. Portfolio: stack 2 independent CMP sub-strategies (different brk_look)
  E. Best combo with pyramid
  F. Trailing TP: ATR-based trailing stop instead of fixed TP
  G. Combined best L+S portfolio summary
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055
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
    d["atr30"]=d["atr_pct"]<30

    # Breakout for multiple lookbacks
    d["cs1"]=d["close"].shift(1)
    for bl in [5,8,10,15,20]:
        d[f"cmn{bl}"]=d["close"].shift(2).rolling(bl-1).min()
        d[f"bs{bl}"]=d["cs1"]<d[f"cmn{bl}"]
    d["bs"]=d["bs10"]  # default

    # Session filter
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))

    # ATR for trailing TP
    d["atr_val"]=d["atr14"]
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_cmp(df, max_same, signal_cols, tp_pct=0.02, max_hold=12,
           sn_pct=SN_PCT, exit_cd=12, brk_look=10, max_pyramid=1,
           trail_atr=None):
    """CMP: Compression + Breakout + Quick TP short strategy
    trail_atr: if set, use ATR-based trailing TP instead of fixed TP
    """
    W=160; H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    DT=df["datetime"].values
    bs_col=f"bs{brk_look}"; BSa=df[bs_col].values
    SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    atr_vals=df["atr_val"].values if trail_atr else None
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,dt,nxo = H[i],L[i],C[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            # SafeNet (buy-to-cover above)
            if h>=p["e"]*(1+sn_pct):
                ep_=p["e"]*(1+sn_pct); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":b,"dt":dt}); lx=i; done=True

            if trail_atr and not done:
                # Trailing TP: track best (lowest) price, exit when price rises by trail_atr * ATR
                best_low = p.get("best_low", p["e"])
                if lo < best_low:
                    best_low = lo
                    p["best_low"] = best_low
                trail_dist = trail_atr * atr_vals[i] if not np.isnan(atr_vals[i]) else p["e"]*tp_pct
                if h >= best_low + trail_dist and b >= 1:
                    ep_ = best_low + trail_dist
                    ep_ = min(ep_, h)
                    pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl":pnl,"t":"TR","b":b,"dt":dt}); lx=i; done=True
                # Max hold
                if not done and b >= max_hold:
                    pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                    trades.append({"pnl":pnl,"t":"MH","b":b,"dt":dt}); lx=i; done=True
            elif not done:
                # Fixed Take Profit
                if lo<=p["e"]*(1-tp_pct):
                    ep_=p["e"]*(1-tp_pct)
                    pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TP","b":b,"dt":dt}); lx=i; done=True
                # Max hold
                if not done and b>=max_hold:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"MH","b":b,"dt":dt}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        # Entry
        n_sigs=sum(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if n_sigs>=1 and brk and sok and (i-lx>=exit_cd) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
                pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def bt_portfolio(df, strats):
    """Run multiple independent sub-strategies and merge trades"""
    all_trades = []
    for cfg in strats:
        t = bt_cmp(df, **cfg)
        all_trades.append(t)
    if not all_trades:
        return pd.DataFrame(columns=["pnl","t","b","dt"])
    merged = pd.concat(all_trades, ignore_index=True)
    merged = merged.sort_values("dt").reset_index(drop=True)
    return merged

def evaluate(tdf, mid_dt, label):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    oos=tdf[tdf["dt"]>=mid_dt].reset_index(drop=True)
    is_=tdf[tdf["dt"]<mid_dt].reset_index(drop=True)
    if len(oos)==0:
        print(f"  {label}: no OOS trades"); return None

    pnl=oos["pnl"].sum(); n=len(oos)
    w=oos[oos["pnl"]>0]["pnl"].sum(); l_=abs(oos[oos["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    wr=(oos["pnl"]>0).mean()*100
    eq=oos["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100

    oos["m"]=oos["dt"].dt.to_period("M")
    ms=oos.groupby("m")["pnl"].sum()
    top_m=ms.max(); top_pct=top_m/pnl*100 if pnl>0 else 999
    no_best=pnl-top_m
    nb_oos=oos[oos["m"]!=ms.idxmax()]
    nb_w=nb_oos[nb_oos["pnl"]>0]["pnl"].sum() if len(nb_oos)>0 else 0
    nb_l=abs(nb_oos[nb_oos["pnl"]<=0]["pnl"].sum()) if len(nb_oos)>0 else 0
    nb_pf=nb_w/nb_l if nb_l>0 else 999

    is_pnl=is_["pnl"].sum() if len(is_)>0 else 0
    is_n=len(is_)

    mirage=top_pct>60 or no_best<=0 or nb_pf<1.0
    tag="MIRAGE!" if mirage else ("TARGET!" if pnl>=10000 else f"${pnl:+,.0f}")

    print(f"\n  {label} [{tag}]")
    print(f"    IS:{is_n:>4d}t ${is_pnl:>+7,.0f}  OOS:{n:>4d}t ${pnl:>+7,.0f} PF{pf:.2f} WR{wr:.0f}% MDD{mdd:.1f}%")
    print(f"    topM:{top_pct:.0f}%  -best:${no_best:>+6,.0f} PF{nb_pf:.2f}  avg${pnl/n:+.1f}")
    return {"pnl":pnl,"pf":pf,"top_pct":top_pct,"no_best":no_best,"nb_pf":nb_pf,
            "mdd":mdd,"n":n,"wr":wr,"mirage":mirage,"is_pnl":is_pnl,"avg":pnl/n,"label":label}

if __name__=="__main__":
    print("="*70)
    print("  ROUND 11: FREQUENCY UNLOCKING (EXIT_CD + BREAKOUT + PORTFOLIO)")
    print("="*70)

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    results = []

    # ===== Phase A: EXIT_CD variations on gk40 (R10 best) =====
    print(f"\n  PHASE A: EXIT_CD VARIATIONS (gk40, TP2%, MH12)")
    for cd in [0, 3, 6, 9, 12]:
        t = bt_cmp(df, 10, ["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd)
        r = evaluate(t, mid_dt, f"gk40 m10 CD{cd}")
        if r: results.append(r)

    # ===== Phase B: Breakout lookback variations =====
    print(f"\n  PHASE B: BREAKOUT LOOKBACK (gk40, TP2%, MH12, CD6)")
    for bl in [5, 8, 10, 15, 20]:
        t = bt_cmp(df, 10, ["gk40"], tp_pct=0.02, max_hold=12, exit_cd=6, brk_look=bl)
        r = evaluate(t, mid_dt, f"gk40 BL{bl} CD6")
        if r: results.append(r)

    # ===== Phase C: GK_thresh × CD × BRK grid =====
    print(f"\n  PHASE C: GK × CD × BRK GRID (best combos)")
    for gk in ["gk30","gk35","gk40","gk50"]:
        for cd in [3, 6]:
            for bl in [8, 10, 15]:
                t = bt_cmp(df, 10, [gk], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=bl)
                r = evaluate(t, mid_dt, f"{gk} BL{bl} CD{cd}")
                if r: results.append(r)

    # ===== Phase D: Portfolio of 2 independent CMP sub-strategies =====
    print(f"\n  PHASE D: PORTFOLIO (stacked independent sub-strategies)")

    # Portfolio: gk40 BL10 + gk40 BL20 (different breakout lookbacks)
    for bl_a, bl_b in [(8,15), (8,20), (10,20), (5,15)]:
        for cd in [6, 9]:
            strats = [
                dict(max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=bl_a),
                dict(max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=bl_b),
            ]
            t = bt_portfolio(df, strats)
            r = evaluate(t, mid_dt, f"PORT gk40 BL{bl_a}+{bl_b} CD{cd}")
            if r: results.append(r)

    # Portfolio: gk30 + gk40 (different thresholds, same brk)
    for cd in [6, 9]:
        strats = [
            dict(max_same=5, signal_cols=["gk30"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=10),
            dict(max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=10),
        ]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid_dt, f"PORT gk30+gk40 BL10 CD{cd}")
        if r: results.append(r)

    # Portfolio: different brk + different gk
    for cd in [6]:
        strats = [
            dict(max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=8),
            dict(max_same=5, signal_cols=["gk40"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=15),
            dict(max_same=5, signal_cols=["gk30"], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=10),
        ]
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid_dt, f"PORT 3x (gk40BL8 + gk40BL15 + gk30BL10) CD{cd}")
        if r: results.append(r)

    # ===== Phase E: Pyramid on best grid configs =====
    print(f"\n  PHASE E: PYRAMID on grid winners")
    for gk in ["gk40","gk35"]:
        for cd in [3, 6]:
            for bl in [10, 15]:
                for ms, pyr in [(15,3),(20,5)]:
                    t = bt_cmp(df, ms, [gk], tp_pct=0.02, max_hold=12, exit_cd=cd, brk_look=bl, max_pyramid=pyr)
                    r = evaluate(t, mid_dt, f"{gk} BL{bl} CD{cd} m{ms}p{pyr}")
                    if r: results.append(r)

    # ===== Phase F: Trailing TP (ATR-based) =====
    print(f"\n  PHASE F: TRAILING TP (ATR-based)")
    for atr_mult in [0.5, 0.8, 1.0, 1.5, 2.0]:
        for mh in [12, 24, 48]:
            t = bt_cmp(df, 10, ["gk40"], tp_pct=0.02, max_hold=mh, exit_cd=6,
                       brk_look=10, trail_atr=atr_mult)
            r = evaluate(t, mid_dt, f"gk40 TRAIL{atr_mult} MH{mh} CD6")
            if r: results.append(r)

    # ===== Phase G: TP × MH on best CD =====
    print(f"\n  PHASE G: TP × MH on best CD")
    for tp in [0.015, 0.02, 0.025, 0.03, 0.04]:
        for mh in [12, 16, 24]:
            t = bt_cmp(df, 10, ["gk40"], tp_pct=tp, max_hold=mh, exit_cd=6)
            r = evaluate(t, mid_dt, f"gk40 TP{tp*100:.1f} MH{mh} CD6")
            if r: results.append(r)

    # ===== Summary: Top 15 non-mirage configs =====
    print(f"\n{'='*70}")
    print(f"  TOP 15 NON-MIRAGE CONFIGS (sorted by OOS PnL)")
    print(f"{'='*70}")
    valid = [r for r in results if not r["mirage"]]
    valid.sort(key=lambda x: x["pnl"], reverse=True)
    for i, r in enumerate(valid[:15]):
        print(f"  {i+1:>2}. {r['label']:<45s} ${r['pnl']:>+7,.0f} PF{r['pf']:.2f} WR{r['wr']:.0f}% "
              f"MDD{r['mdd']:.1f}% topM{r['top_pct']:.0f}% avg${r['avg']:+.1f}")

    print(f"\n  Total configs tested: {len(results)}")
    print(f"  Non-mirage: {len(valid)}")
    print(f"  ROUND 11 COMPLETE")
