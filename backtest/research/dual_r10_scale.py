"""
Round 10: Scale CMP Short Strategy to $10K
============================================
R9 found: CMP (GK compression + breakout + TP2% + maxHold12) passes anti-mirage.
  OOS $2,261, PF 1.78, topM 20%, remove-best $1,807 PF 1.64
  BUT: only ~$2K/year. Need ~5x more.

Scaling levers (must preserve anti-mirage profile):
  1. More signals (OR-ensemble) → more entry bars
  2. Higher maxSame → more concurrent positions
  3. Pyramid → more entries per bar
  4. Looser TP/MH → larger per-trade profit (but riskier)
  5. Multiple TP levels (partial close)

Key advantage of CMP over trend-following:
  - WR 66-72% (vs 42%) → small consistent wins
  - topM 18-21% (vs 93%) → distributed
  - Holds 12h (vs 24-96h) → more turnover capacity
  - IS positive (vs near-zero) → consistent edge

Signal overlap consideration:
  L uses gk_comp for LONG entries. S uses gk_comp for SHORT entries.
  Same compression regime, opposite directions, different exit logic.
  Argument: session filter + EMA trail + GK are shared infrastructure.
  But to be safe, also test non-GK compression signals.
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; EXIT_CD=12
GK_SHORT=5; GK_LONG=20; GK_WIN=100
PE_ORDER=3; PE_WIN=24

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

def calc_pe(vals, order=PE_ORDER, win=PE_WIN):
    n=len(vals); result=np.full(n,np.nan)
    max_h=np.log(factorial(order))
    if max_h==0: return result
    n_pat=win-order+1
    mult=np.array([order**k for k in range(order-1,-1,-1)])
    for i in range(win-1, n):
        seg=vals[i-win+1:i+1]
        if np.isnan(seg).any(): continue
        idx_arr=np.arange(order)[None,:]+np.arange(n_pat)[:,None]
        windows=seg[idx_arr]
        pats=np.argsort(windows,axis=1)
        ids=(pats*mult[None,:]).sum(axis=1)
        _,counts=np.unique(ids,return_counts=True)
        probs=counts/counts.sum()
        h=-np.sum(probs*np.log(probs))
        result[i]=h/max_h
    return result

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
    d["gk30"]=d["gk_pct"]<30
    d["gk40"]=d["gk_pct"]<40  # looser threshold
    d["gk50"]=d["gk_pct"]<50  # even looser

    # ATR compression (alternative to GK)
    d["atr14"]=(d["high"]-d["low"]).rolling(14).mean()
    d["atr_pct"]=d["atr14"].shift(1).rolling(100).apply(pctile)
    d["atr30"]=d["atr_pct"]<30
    d["atr40"]=d["atr_pct"]<40

    # VCV compression
    vm=d["volume"].rolling(20).mean(); vs_=d["volume"].rolling(20).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_pct"]=d["vcv"].shift(1).rolling(50).apply(pctile)
    d["vcv30"]=d["vcv_pct"]<30

    # Kurtosis low (calm regime)
    d["kurt_val"]=d["ret"].rolling(30).kurt().shift(1)
    d["kurt_pct"]=d["kurt_val"].rolling(50).apply(pctile)
    d["kurt30"]=d["kurt_pct"]<30

    # PE low
    print("    Computing PE...", flush=True)
    pe_raw=calc_pe(d["close"].values)
    d["pe"]=pd.Series(pe_raw,index=d.index)
    d["pe_pct"]=d["pe"].shift(1).rolling(50).apply(pctile)
    d["pe30"]=d["pe_pct"]<30
    print("    PE done.", flush=True)

    # TBR low
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(5).mean()
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(50).apply(pctile)
    d["tbr30"]=d["tbr_pct"]<30

    # Body ratio (NEW: small body = indecision)
    d["body_ratio"]=abs(d["close"]-d["open"])/(d["high"]-d["low"]).replace(0,np.nan)
    d["br_pct"]=d["body_ratio"].shift(1).rolling(50).apply(pctile)
    d["br_low"]=d["br_pct"]<25  # small body candles

    # Breakout + Session
    d["cs1"]=d["close"].shift(1)
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_cmp(df, max_same, signal_cols, tp_pct=0.02, max_hold=12,
           sn_pct=SN_PCT, max_pyramid=1):
    """CMP: Compression + Breakout + Quick TP short strategy"""
    W=160; H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    DT=df["datetime"].values; BSa=df["bs"].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,dt,nxo = H[i],L[i],C[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            # SafeNet
            if h>=p["e"]*(1+sn_pct):
                ep_=p["e"]*(1+sn_pct); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":b,"dt":dt}); lx=i; done=True
            # Take Profit
            if not done and lo<=p["e"]*(1-tp_pct):
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
        if n_sigs>=1 and brk and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
                pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

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
            "mdd":mdd,"n":n,"wr":wr,"mirage":mirage,"is_pnl":is_pnl,"avg":pnl/n}

if __name__=="__main__":
    print("="*70)
    print("  ROUND 10: SCALE CMP SHORT STRATEGY")
    print("="*70)

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # ===== Phase A: Scaling maxSame =====
    print(f"\n  PHASE A: SCALE maxSame (GK30, TP2%, MH12)")
    for ms in [3, 5, 7, 10, 15, 20]:
        t = bt_cmp(df, ms, ["gk30"], tp_pct=0.02, max_hold=12)
        evaluate(t, mid_dt, f"gk30 m{ms} TP2 MH12")

    # ===== Phase B: Looser GK threshold =====
    print(f"\n  PHASE B: LOOSER GK THRESHOLD (m10)")
    for gk_sig in ["gk30","gk40","gk50"]:
        t = bt_cmp(df, 10, [gk_sig], tp_pct=0.02, max_hold=12)
        evaluate(t, mid_dt, f"{gk_sig} m10 TP2 MH12")

    # ===== Phase C: OR-ensemble (more signals = more entries) =====
    print(f"\n  PHASE C: OR-ENSEMBLE (add signals for more entry bars)")
    combos = [
        ("gk30+atr30",    ["gk30","atr30"]),
        ("gk30+vcv30",    ["gk30","vcv30"]),
        ("gk30+kurt30",   ["gk30","kurt30"]),
        ("gk30+pe30",     ["gk30","pe30"]),
        ("gk30+tbr30",    ["gk30","tbr30"]),
        ("gk30+br_low",   ["gk30","br_low"]),
        ("atr30+vcv30",   ["atr30","vcv30"]),  # no-GK alternative
        ("atr30+kurt30",  ["atr30","kurt30"]), # no-GK alternative
        ("gk30+atr30+vcv30", ["gk30","atr30","vcv30"]),
        ("gk30+atr30+kurt30+pe30", ["gk30","atr30","kurt30","pe30"]),
        ("all6", ["gk30","atr30","vcv30","kurt30","pe30","tbr30"]),
        # No-GK versions
        ("noGK: atr30+vcv30+kurt30", ["atr30","vcv30","kurt30"]),
        ("noGK: atr30+vcv30+kurt30+pe30", ["atr30","vcv30","kurt30","pe30"]),
        ("noGK: atr30+vcv30+kurt30+pe30+tbr30", ["atr30","vcv30","kurt30","pe30","tbr30"]),
    ]
    for name, sigs in combos:
        t = bt_cmp(df, 10, sigs, tp_pct=0.02, max_hold=12)
        evaluate(t, mid_dt, f"{name} m10 TP2 MH12")

    # ===== Phase D: Pyramid on best combos =====
    print(f"\n  PHASE D: PYRAMID on promising combos")
    for name, sigs in [
        ("gk30+atr30+vcv30", ["gk30","atr30","vcv30"]),
        ("all6", ["gk30","atr30","vcv30","kurt30","pe30","tbr30"]),
        ("noGK 5sig", ["atr30","vcv30","kurt30","pe30","tbr30"]),
    ]:
        for ms, pyr in [(15,3),(20,3),(20,5),(30,5)]:
            t = bt_cmp(df, ms, sigs, tp_pct=0.02, max_hold=12, max_pyramid=pyr)
            evaluate(t, mid_dt, f"{name} m{ms} p{pyr} TP2 MH12")

    # ===== Phase E: TP/MH variations on best =====
    print(f"\n  PHASE E: TP/MH VARIATIONS")
    best_sigs = ["gk30","atr30","vcv30","kurt30","pe30","tbr30"]  # all6
    for tp, mh in [(0.015,12),(0.02,12),(0.025,12),(0.02,8),(0.02,16),(0.02,24),(0.03,24)]:
        t = bt_cmp(df, 10, best_sigs, tp_pct=tp, max_hold=mh)
        evaluate(t, mid_dt, f"all6 m10 TP{tp*100:.1f} MH{mh}")

    print(f"\n  ROUND 10 COMPLETE")
