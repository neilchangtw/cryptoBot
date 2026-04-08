"""
Round 9: Anti-Mirage Short Strategy Exploration
=================================================
R8 audit revealed: ALL prior S configs are mirages (93% from Feb 2026).

NEW RULE: Every candidate must pass the "remove best month" test.
  go_topM_pct < 60% after removing best month → still must be positive

Key insight from audit:
  - Per-trade edge is ~$12/t but ONLY during crash months
  - Non-crash months: avg $1.0/t (zero edge after fees)
  - Problem is structural to "compression breakout + EMA trail" on shorts

POSSIBLE NEW DIRECTIONS (not yet tried):
  1. MEAN REVERSION shorts — NOT trend-following
     Sell overbought conditions, take profit quickly
     Opposite of compression-breakout paradigm
     Risk: "ETH 均值回歸 -$320" was tried but that was long+short combined
     Maybe SHORT-ONLY mean reversion could work differently

  2. DIFFERENT EXIT for shorts — quick TP instead of trailing
     Current trail holds 24-48h → works great in crashes, bleeds in normal
     What if: TP at fixed %, hold max 12-24h?
     Prior R2 said "all exit mods hurt" but that was with compression entry

  3. VOLATILITY EXPANSION shorts — enter AFTER vol expands (not before)
     Current: enter during low-vol compression → wait for breakout
     New: enter when vol has just spiked → fade the spike
     GK percentile > 70 (high vol) + downward breakout

  4. MULTI-TIMEFRAME — use 4h or daily signals for direction
     1h for execution, higher TF for bias
     E.g., daily EMA bearish + 1h short breakout

  5. FUNDING RATE SIGNAL — negative funding as short signal
     But: "資金費率套利（全部虧損）" was tried
     Maybe as a FILTER, not a standalone strategy?

APPROACH: Test directions 1-4 quickly. Skip funding rate (proven dead).
For each: compute OOS PnL AND "remove best month" PnL.

Anti-mirage check: every config must show:
  (a) remove_best_month_pnl > 0
  (b) topMonth < 60% of total
  (c) PF > 1.2 after removing best month
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; MIN_TRAIL=7; ES_PCT=0.010; ES_END=12; EXIT_CD=12
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
    d["ema10"]=d["close"].ewm(span=10).mean()
    d["ema50"]=d["close"].ewm(span=50).mean()
    d["ret"]=d["close"].pct_change()

    # === Compression signals (original) ===
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    d["gk_comp"]=d["gk_pct"]<30  # compression
    d["gk_exp"]=d["gk_pct"]>70   # expansion (NEW - opposite!)

    # === Mean Reversion signals (NEW) ===
    # RSI-like overbought
    delta=d["ret"]
    gain=delta.clip(lower=0).rolling(14).mean()
    loss=(-delta.clip(upper=0)).rolling(14).mean()
    rs=(gain/loss).replace([np.inf,-np.inf],np.nan)
    d["rsi14"]=100-100/(1+rs)
    d["rsi_ob"]=d["rsi14"].shift(1)>70  # overbought
    d["rsi_os"]=d["rsi14"].shift(1)<30  # oversold

    # Bollinger Band position
    d["bb_mid"]=d["close"].rolling(20).mean()
    d["bb_std"]=d["close"].rolling(20).std()
    d["bb_up"]=d["bb_mid"]+2*d["bb_std"]
    d["bb_dn"]=d["bb_mid"]-2*d["bb_std"]
    d["bb_pct"]=((d["close"]-d["bb_dn"])/(d["bb_up"]-d["bb_dn"])).replace([np.inf,-np.inf],np.nan)
    d["bb_high"]=d["bb_pct"].shift(1)>0.9  # near upper band
    d["bb_low"]=d["bb_pct"].shift(1)<0.1   # near lower band

    # Rate of change overbought/oversold
    d["roc5"]=d["close"].pct_change(5).shift(1)
    d["roc5_pct"]=d["roc5"].rolling(100).apply(pctile)
    d["roc5_high"]=d["roc5_pct"]>75  # overbought (short signal)
    d["roc5_low"]=d["roc5_pct"]<25   # oversold

    # Distance from EMA
    d["dist_ema20"]=((d["close"]-d["ema20"])/d["ema20"]*100).shift(1)
    d["dist_high"]=d["dist_ema20"]>3.0  # >3% above EMA (overbought)
    d["dist_low"]=d["dist_ema20"]<-3.0  # >3% below EMA (oversold)

    # === Vol expansion signals (NEW) ===
    d["atr14"]=(d["high"]-d["low"]).rolling(14).mean()
    d["atr_pct"]=d["atr14"].shift(1).rolling(100).apply(pctile)
    d["atr_high"]=d["atr_pct"]>70  # high vol

    # === Trend filter (multi-timeframe proxy) ===
    d["ema_bear"]=d["ema10"].shift(1)<d["ema50"].shift(1)  # EMA10 below EMA50
    d["ema_bull"]=d["ema10"].shift(1)>d["ema50"].shift(1)

    # 20-bar return direction
    d["roc20"]=d["close"].pct_change(20).shift(1)
    d["roc20_pct"]=d["roc20"].rolling(100).apply(pctile)
    d["roc20_bear"]=d["roc20_pct"]<25

    # === Breakout ===
    d["cs1"]=d["close"].shift(1)
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bs"]=d["cs1"]<d["cmn"]  # downward breakout

    # No-breakout entry for mean reversion (just session + signal)
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

def bt_short(df, max_same, signal_cols, require_breakout=True,
             tp_pct=None, max_hold=None, sn_pct=SN_PCT):
    """
    Flexible short backtest.
    tp_pct: if set, take profit at this % (mean reversion style)
    max_hold: if set, force exit after N bars
    require_breakout: if False, enter without breakout (mean reversion)
    """
    W=160; H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BSa=df["bs"].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            # SafeNet
            if h>=p["e"]*(1+sn_pct):
                ep_=p["e"]*(1+sn_pct); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":b,"dt":dt}); lx=i; done=True
            # Take Profit (mean reversion)
            if not done and tp_pct and lo<=p["e"]*(1-tp_pct):
                ep_=p["e"]*(1-tp_pct)
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TP","b":b,"dt":dt}); lx=i; done=True
            # Max hold time exit
            if not done and max_hold and b>=max_hold:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":b,"dt":dt}); lx=i; done=True
            # EarlyStop
            if not done and not tp_pct:  # only for trend-following
                if MIN_TRAIL<=b<=ES_END:
                    if (p["e"]-c)/p["e"]<-ES_PCT:
                        pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                        trades.append({"pnl":pnl,"t":"ES","b":b,"dt":dt}); lx=i; done=True
            # Trail (only for trend-following)
            if not done and not tp_pct:
                if b>=MIN_TRAIL and c>=ema:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"TR","b":b,"dt":dt}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        # Entry
        any_sig=any(_b(arr[i]) for arr in sigs)
        brk_ok = _b(BSa[i]) if require_breakout else True
        sok=_b(SOKa[i])
        if any_sig and brk_ok and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])

def evaluate(tdf, mid_dt, label):
    """Evaluate with anti-mirage check"""
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    oos=tdf[tdf["dt"]>=mid_dt].reset_index(drop=True)
    is_=tdf[tdf["dt"]<mid_dt].reset_index(drop=True)
    if len(oos)==0:
        print(f"  {label}: no OOS trades"); return

    pnl=oos["pnl"].sum(); n=len(oos)
    w=oos[oos["pnl"]>0]["pnl"].sum(); l_=abs(oos[oos["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    wr=(oos["pnl"]>0).mean()*100
    eq=oos["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=abs(dd.min())/ACCOUNT*100

    # Monthly
    oos["m"]=oos["dt"].dt.to_period("M")
    ms=oos.groupby("m")["pnl"].sum()
    top_m=ms.max(); top_m_name=str(ms.idxmax())
    top_pct=top_m/pnl*100 if pnl>0 else 999
    # Remove best month
    no_best = pnl - top_m
    no_best_n = n - len(oos[oos["m"]==ms.idxmax()])
    no_best_pf = 0
    if no_best_n > 0:
        nb_oos = oos[oos["m"]!=ms.idxmax()]
        nb_w = nb_oos[nb_oos["pnl"]>0]["pnl"].sum()
        nb_l = abs(nb_oos[nb_oos["pnl"]<=0]["pnl"].sum())
        no_best_pf = nb_w/nb_l if nb_l>0 else 999

    # IS stats
    is_pnl = is_["pnl"].sum() if len(is_)>0 else 0
    is_n = len(is_)

    # Anti-mirage verdict
    mirage = top_pct > 60 or no_best <= 0 or no_best_pf < 1.0
    tag = "MIRAGE!" if mirage else ("TARGET" if pnl>=10000 else f"${pnl:+,.0f}")

    print(f"\n  {label} [{tag}]")
    print(f"    IS: {is_n}t ${is_pnl:>+,.0f}  OOS: {n}t ${pnl:>+,.0f} PF{pf:.2f} WR{wr:.0f}% MDD{mdd:.1f}%")
    print(f"    topM: {top_m_name} ${top_m:+,.0f} ({top_pct:.0f}%)")
    print(f"    Remove best month: ${no_best:+,.0f} PF{no_best_pf:.2f}")
    if mirage:
        if top_pct > 60:
            print(f"    !! topM {top_pct:.0f}% > 60% threshold")
        if no_best <= 0:
            print(f"    !! Negative after removing best month")
        if no_best_pf < 1.0:
            print(f"    !! PF {no_best_pf:.2f} < 1.0 after removing best month")

if __name__=="__main__":
    print("="*70)
    print("  ROUND 9: ANTI-MIRAGE SHORT STRATEGY EXPLORATION")
    print("="*70)
    print("  Anti-mirage rule: remove best month → still positive, PF>1.0, topM<60%")

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    # ===== DIRECTION 1: Mean Reversion Shorts =====
    print(f"\n{'='*70}")
    print("  DIR 1: MEAN REVERSION SHORTS (sell overbought, TP quickly)")
    print("="*70)

    mr_configs = [
        # (label, maxSame, signals, require_breakout, tp_pct, max_hold, sn_pct)
        # TP 1.5% with various signals
        ("MR rsi_ob TP1.5 MH24",      5, ["rsi_ob"],    False, 0.015, 24, 0.055),
        ("MR bb_high TP1.5 MH24",     5, ["bb_high"],   False, 0.015, 24, 0.055),
        ("MR roc5_high TP1.5 MH24",   5, ["roc5_high"], False, 0.015, 24, 0.055),
        ("MR dist_high TP1.5 MH24",   5, ["dist_high"], False, 0.015, 24, 0.055),
        # TP 2%
        ("MR rsi_ob TP2 MH24",        5, ["rsi_ob"],    False, 0.020, 24, 0.055),
        ("MR bb_high TP2 MH24",       5, ["bb_high"],   False, 0.020, 24, 0.055),
        ("MR dist_high TP2 MH24",     5, ["dist_high"], False, 0.020, 24, 0.055),
        # TP 1% (tighter)
        ("MR rsi_ob TP1 MH12",        5, ["rsi_ob"],    False, 0.010, 12, 0.055),
        ("MR bb_high TP1 MH12",       5, ["bb_high"],   False, 0.010, 12, 0.055),
        # Combo signals
        ("MR rsi+bb TP1.5 MH24",      5, ["rsi_ob","bb_high"],   False, 0.015, 24, 0.055),
        ("MR rsi+dist TP1.5 MH24",    5, ["rsi_ob","dist_high"], False, 0.015, 24, 0.055),
        ("MR rsi+roc5 TP1.5 MH24",    5, ["rsi_ob","roc5_high"], False, 0.015, 24, 0.055),
        # With breakout required
        ("MR rsi_ob brk TP1.5 MH24",  5, ["rsi_ob"],    True, 0.015, 24, 0.055),
        ("MR bb_high brk TP2 MH24",   5, ["bb_high"],   True, 0.020, 24, 0.055),
    ]

    for label, ms, sigs, brk, tp, mh, sn in mr_configs:
        t = bt_short(df, ms, sigs, require_breakout=brk, tp_pct=tp, max_hold=mh, sn_pct=sn)
        evaluate(t, mid_dt, label)

    # ===== DIRECTION 2: Compression + Quick Exit =====
    print(f"\n{'='*70}")
    print("  DIR 2: COMPRESSION ENTRY + QUICK EXIT (TP instead of trail)")
    print("="*70)

    qe_configs = [
        ("CMP gk brk TP2 MH24",   5, ["gk_comp"], True, 0.020, 24, 0.055),
        ("CMP gk brk TP3 MH48",   5, ["gk_comp"], True, 0.030, 48, 0.055),
        ("CMP gk brk TP1.5 MH12", 5, ["gk_comp"], True, 0.015, 12, 0.055),
        ("CMP gk brk TP2 MH12",   5, ["gk_comp"], True, 0.020, 12, 0.055),
    ]

    for label, ms, sigs, brk, tp, mh, sn in qe_configs:
        t = bt_short(df, ms, sigs, require_breakout=brk, tp_pct=tp, max_hold=mh, sn_pct=sn)
        evaluate(t, mid_dt, label)

    # ===== DIRECTION 3: Vol Expansion Shorts =====
    print(f"\n{'='*70}")
    print("  DIR 3: VOL EXPANSION SHORTS (fade the spike)")
    print("="*70)

    ve_configs = [
        # High vol + breakout + trend following
        ("VE gk_exp brk trail m5",     5, ["gk_exp"],   True, None, None, 0.055),
        ("VE atr_high brk trail m5",   5, ["atr_high"],  True, None, None, 0.055),
        # High vol + quick exit
        ("VE gk_exp brk TP2 MH24",    5, ["gk_exp"],   True, 0.020, 24, 0.055),
        ("VE atr_high brk TP2 MH24",  5, ["atr_high"],  True, 0.020, 24, 0.055),
        # High vol + mean reversion (fade)
        ("VE gk_exp nobrk TP1.5 MH12",5, ["gk_exp"],   False, 0.015, 12, 0.055),
        ("VE atr_high+rsi nobrk TP2",  5, ["atr_high","rsi_ob"], False, 0.020, 24, 0.055),
    ]

    for label, ms, sigs, brk, tp, mh, sn in ve_configs:
        t = bt_short(df, ms, sigs, require_breakout=brk, tp_pct=tp, max_hold=mh, sn_pct=sn)
        evaluate(t, mid_dt, label)

    # ===== DIRECTION 4: Trend Filter + Compression =====
    print(f"\n{'='*70}")
    print("  DIR 4: TREND FILTER (EMA bearish context + short entry)")
    print("="*70)

    tf_configs = [
        # EMA bearish + breakout + trail
        ("TF ema_bear brk trail m5",    5, ["ema_bear"],   True, None, None, 0.055),
        ("TF ema_bear+gk brk trail m5", 5, ["ema_bear","gk_comp"], True, None, None, 0.055),
        # EMA bearish + roc20_bear + breakout
        ("TF roc20_bear brk trail m5",  5, ["roc20_bear"], True, None, None, 0.055),
        # EMA bearish + mean reversion
        ("TF ema_bear+rsi nobrk TP2",   5, ["ema_bear","rsi_ob"], False, 0.020, 24, 0.055),
        ("TF ema_bear+dist nobrk TP2",  5, ["ema_bear","dist_high"], False, 0.020, 24, 0.055),
    ]

    for label, ms, sigs, brk, tp, mh, sn in tf_configs:
        t = bt_short(df, ms, sigs, require_breakout=brk, tp_pct=tp, max_hold=mh, sn_pct=sn)
        evaluate(t, mid_dt, label)

    print(f"\n{'='*70}")
    print("  ROUND 9 SUMMARY")
    print("="*70)
    print("  Configs that pass anti-mirage check:")
    print("  (remove best month → positive, PF>1.0, topM<60%)")
    print("  -> Listed above with [TARGET] or [$X,XXX] tags")
    print("  -> [MIRAGE!] tags indicate failed anti-mirage check")
    print(f"\n  ROUND 9 COMPLETE")
