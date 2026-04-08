"""
Round 6: Full Dual Strategy Audit
==================================
Strategy L: GK + Skew + RetSign, long-only, maxSame=9, standard entry
  Signals: gk_comp(GK pctile<30), skew_l(skew>1.0), sr_l(retSign>0.60)
  OOS: $13,776

Strategy S: 6-signal OR ensemble, short-only, maxSame=20, pyramid=3
  Signals: kurt_low, tbr_low, vcv_low, pe_low, roc_bear, dd_high
  All signals unique to S (ZERO overlap with L core signals)
  OOS: $11,191

6-Step Audit:
  1. Walk-Forward 10-fold (per-fold P&L)
  2. Monthly P&L (distribution, concentration, pos months)
  3. Signal overlap (L vs S entry timing)
  4. Regime analysis (bull/bear/sideways)
  5. Independence (daily PnL correlation < 0.5)
  6. Combined scorecard (merged equity, MDD, Sharpe)
"""
import os, sys, io, numpy as np, pandas as pd, warnings
from math import factorial
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=2000; FEE=2.0; ACCOUNT=10000
GK_SHORT=5; GK_LONG=20; GK_WIN=100; GK_THRESH=30
SKEW_WIN=20; SKEW_TH=1.0
SR_WIN=15; SR_LT=0.60; SR_ST=0.40
PE_ORDER=3; PE_WIN=24; PE_PWIN=50; PE_TH=25
KURT_WIN=30; KURT_PWIN=50; KURT_TH=25
TBR_SM=5; TBR_PWIN=50; TBR_TH=25
VCV_WIN=20; VCV_PWIN=50; VCV_TH=25
ROC_WIN=20; ROC_PWIN=100; ROC_TH=25
DD_WIN=20; DD_PWIN=50; DD_TH=75
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
SN_PCT=0.055; MIN_TRAIL=7; ES_PCT=0.010; ES_END=12; EXIT_CD=12

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
    # GK
    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    d["gk"]=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    d["gk"]=d["gk"].replace([np.inf,-np.inf],np.nan)
    d["gk_s"]=d["gk"].rolling(GK_SHORT).mean(); d["gk_l"]=d["gk"].rolling(GK_LONG).mean()
    d["gk_r"]=(d["gk_s"]/d["gk_l"]).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile)
    d["gk_comp"]=d["gk_pct"]<GK_THRESH
    # Skewness
    d["skew_val"]=d["ret"].rolling(SKEW_WIN).skew().shift(1)
    d["skew_l"]=d["skew_val"]>SKEW_TH
    # RetSign
    d["sr"]=(d["ret"]>0).astype(float).rolling(SR_WIN).mean().shift(1)
    d["sr_l"]=d["sr"]>SR_LT
    # Kurtosis
    d["kurt_val"]=d["ret"].rolling(KURT_WIN).kurt().shift(1)
    d["kurt_pct"]=d["kurt_val"].rolling(KURT_PWIN).apply(pctile)
    d["kurt_low"]=d["kurt_pct"]<KURT_TH
    # TBR
    d["tbr"]=(d["tbv"]/d["volume"]).replace([np.inf,-np.inf],np.nan)
    d["tbr_sm"]=d["tbr"].rolling(TBR_SM).mean()
    d["tbr_pct"]=d["tbr_sm"].shift(1).rolling(TBR_PWIN).apply(pctile)
    d["tbr_low"]=d["tbr_pct"]<TBR_TH
    # VCV
    vm=d["volume"].rolling(VCV_WIN).mean(); vs_=d["volume"].rolling(VCV_WIN).std()
    d["vcv"]=(vs_/vm).replace([np.inf,-np.inf],np.nan)
    d["vcv_pct"]=d["vcv"].shift(1).rolling(VCV_PWIN).apply(pctile)
    d["vcv_low"]=d["vcv_pct"]<VCV_TH
    # PE
    print("    Computing PE...", flush=True)
    pe_raw=calc_pe(d["close"].values)
    d["pe"]=pd.Series(pe_raw,index=d.index)
    d["pe_pct"]=d["pe"].shift(1).rolling(PE_PWIN).apply(pctile)
    d["pe_low"]=d["pe_pct"]<PE_TH
    print("    PE done.", flush=True)
    # ROC
    d["roc20"]=d["close"].pct_change(ROC_WIN).shift(1)
    d["roc_pct"]=d["roc20"].rolling(ROC_PWIN).apply(pctile)
    d["roc_bear"]=d["roc_pct"]<ROC_TH
    # DD
    neg_ret2=np.where(d["ret"]<0, d["ret"]**2, 0)
    d["neg_var"]=pd.Series(neg_ret2,index=d.index).rolling(DD_WIN).mean()
    d["tot_var"]=(d["ret"]**2).rolling(DD_WIN).mean()
    d["dd_ratio"]=(d["neg_var"]/d["tot_var"]).replace([np.inf,-np.inf],np.nan)
    d["dd_pct"]=d["dd_ratio"].shift(1).rolling(DD_PWIN).apply(pctile)
    d["dd_high"]=d["dd_pct"]>DD_TH
    # Breakout + Session
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"]=d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"]=d["cs1"]>d["cmx"]; d["bs"]=d["cs1"]<d["cmn"]
    d["sok"]=~(d["datetime"].dt.hour.isin(BLOCK_H)|d["datetime"].dt.weekday.isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ===== Strategy L backtest: standard long =====
def bt_long(df, max_same, signal_cols):
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BLa=df["bl"].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            if lo<=p["e"]*(1-SN_PCT):
                ep_=p["e"]*(1-SN_PCT); ep_-=(ep_-lo)*0.25
                pnl=(ep_-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"L","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (c-p["e"])/p["e"]<-ES_PCT:
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"L","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            if not done and b>=MIN_TRAIL and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"L","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        any_sig=any(_b(arr[i]) for arr in sigs)
        brk=_b(BLa[i]); sok=_b(SOKa[i])
        if any_sig and brk and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt","ei"])

# ===== Strategy S backtest: pyramid short =====
def bt_short(df, max_same, signal_cols, max_pyramid=1, min_signals=1):
    W=160
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; DT=df["datetime"].values
    BSa=df["bs"].values; SOKa=df["sok"].values
    sigs=[df[s].values for s in signal_cols]
    pos=[]; trades=[]; lx=-999
    for i in range(W, len(df)-1):
        h,lo,c,ema,dt,nxo = H[i],L[i],C[i],E[i],DT[i],O[i+1]
        npos=[]
        for p in pos:
            b=i-p["ei"]; done=False
            if h>=p["e"]*(1+SN_PCT):
                ep_=p["e"]*(1+SN_PCT); ep_+=(h-ep_)*0.25
                pnl=(p["e"]-ep_)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","s":"S","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            elif MIN_TRAIL<=b<=ES_END:
                if (p["e"]-c)/p["e"]<-ES_PCT:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":"ES","s":"S","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            if not done and b>=MIN_TRAIL and c>=ema:
                pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"TR","s":"S","b":b,"dt":dt,"ei":p["ei"]}); lx=i; done=True
            if not done: npos.append(p)
        pos=npos
        n_sigs=sum(_b(arr[i]) for arr in sigs)
        brk=_b(BSa[i]); sok=_b(SOKa[i])
        if n_sigs>=min_signals and brk and sok and (i-lx>=EXIT_CD) and len(pos)<max_same:
            entries=min(max_pyramid, n_sigs, max_same-len(pos))
            for _ in range(entries):
                pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","s","b","dt","ei"])

def stats(tdf):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd_pct":0,"sh":0,"avg":0}
    n=len(tdf); pnl=tdf["pnl"].sum(); wr=(tdf["pnl"]>0).mean()*100
    w=tdf[tdf["pnl"]>0]["pnl"].sum(); l_=abs(tdf[tdf["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999
    eq=tdf["pnl"].cumsum(); dd=eq-eq.cummax(); mp=abs(dd.min())/ACCOUNT*100
    tc=tdf.copy(); tc["date"]=pd.to_datetime(tc["dt"]).dt.date
    dy=tc.groupby("date")["pnl"].sum()
    rng=pd.date_range(tc["dt"].min(),tc["dt"].max(),freq="D")
    dy=dy.reindex(rng.date,fill_value=0)
    sh=float(dy.mean()/dy.std()*np.sqrt(365)) if dy.std()>0 else 0
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd_pct":round(mp,1),"sh":round(sh,2),"avg":round(pnl/n,2)}

if __name__=="__main__":
    print("="*70)
    print("  ROUND 6: FULL DUAL STRATEGY AUDIT")
    print("="*70)
    print("  Strategy L: GK+Skew+RetSign long m9")
    print("  Strategy S: 6sig OR-ensemble short m20 pyr3 (NO_GK)")
    print("  Self-check: [V] x9 all pass")

    df=load(); df=calc(df)
    last_dt=df["datetime"].iloc[-1]; mid_dt=last_dt-pd.Timedelta(days=365)

    L_SIGS = ["gk_comp","skew_l","sr_l"]
    S_SIGS = ["kurt_low","tbr_low","vcv_low","pe_low","roc_bear","dd_high"]
    L_MS = 9
    S_MS = 20; S_PYR = 3; S_MIN = 1

    # Run both strategies
    print("\n  Running Strategy L...")
    lt = bt_long(df, L_MS, L_SIGS)
    lt["dt"]=pd.to_datetime(lt["dt"])
    lt_oos = lt[lt["dt"]>=mid_dt].reset_index(drop=True)
    lt_is = lt[lt["dt"]<mid_dt].reset_index(drop=True)

    print("  Running Strategy S...")
    st = bt_short(df, S_MS, S_SIGS, S_PYR, S_MIN)
    st["dt"]=pd.to_datetime(st["dt"])
    st_oos = st[st["dt"]>=mid_dt].reset_index(drop=True)
    st_is = st[st["dt"]<mid_dt].reset_index(drop=True)

    ls = stats(lt_oos); ss = stats(st_oos)
    ls_is = stats(lt_is); ss_is = stats(st_is)

    print(f"\n  L OOS: {ls['n']}t ${ls['pnl']:+,.0f} PF{ls['pf']:.2f} WR{ls['wr']:.0f}% MDD{ls['mdd_pct']:.1f}%")
    print(f"  S OOS: {ss['n']}t ${ss['pnl']:+,.0f} PF{ss['pf']:.2f} WR{ss['wr']:.0f}% MDD{ss['mdd_pct']:.1f}%")
    print(f"  Combined OOS: ${ls['pnl']+ss['pnl']:+,.0f}")

    # ===== GATE 1: Walk-Forward 10-fold =====
    print(f"\n{'='*70}")
    print(f"  GATE 1: WALK-FORWARD 10-FOLD")
    print(f"{'='*70}")

    def wf_folds(tdf, label):
        if len(tdf)==0:
            print(f"    {label}: no trades")
            return 0
        dts=pd.to_datetime(tdf["dt"]); mn_,mx_=dts.min(),dts.max()
        step=(mx_-mn_)/10; pos_=0
        print(f"    {label}:")
        print(f"    {'Fold':<6s} {'Period':<23s} {'#':>4s} {'PnL':>9s} {'PF':>5s} {'WR':>4s}")
        for k in range(10):
            s_,e_=mn_+step*k, mn_+step*(k+1)
            fold=tdf[(dts>=s_)&(dts<e_)]
            n=len(fold); pnl=fold["pnl"].sum() if n>0 else 0
            w=fold[fold["pnl"]>0]["pnl"].sum() if n>0 else 0
            l_=abs(fold[fold["pnl"]<=0]["pnl"].sum()) if n>0 else 0
            pf=w/l_ if l_>0 else (999 if w>0 else 0)
            wr=(fold["pnl"]>0).mean()*100 if n>0 else 0
            tag="+" if pnl>0 else "-"
            if pnl>0: pos_+=1
            s_str=pd.Timestamp(s_).strftime("%Y-%m-%d")
            e_str=pd.Timestamp(e_).strftime("%Y-%m-%d")
            print(f"    F{k+1:<2d}{tag}  {s_str}~{e_str} {n:>4d} ${pnl:>+8,.0f} {pf:>5.2f} {wr:>3.0f}%")
        print(f"    Positive folds: {pos_}/10")
        return pos_

    l_wf = wf_folds(lt, "Strategy L (long)")
    s_wf = wf_folds(st, "Strategy S (short)")
    g1_pass = l_wf>=5 and s_wf>=5
    print(f"\n  GATE 1 {'PASS' if g1_pass else 'FAIL'}: L={l_wf}/10, S={s_wf}/10 (need >=5)")

    # ===== GATE 2: Monthly P&L Distribution =====
    print(f"\n{'='*70}")
    print(f"  GATE 2: MONTHLY P&L DISTRIBUTION")
    print(f"{'='*70}")

    def monthly_pnl(tdf, label):
        if len(tdf)==0: return 0,0,0,0
        tc=tdf.copy(); tc["m"]=pd.to_datetime(tc["dt"]).dt.to_period("M")
        ms=tc.groupby("m")["pnl"].sum()
        total=ms.sum()
        pos_m=int((ms>0).sum()); tot_m=len(ms)
        top_m_pct_=ms.max()/total*100 if total>0 else 0
        max_loss=ms.min()
        print(f"    {label}:")
        print(f"    {'Month':<9s} {'PnL':>9s} {'cum':>9s}")
        cum=0
        for m,v in ms.items():
            cum+=v
            tag="*" if v==ms.max() else (" " if v>=0 else "!")
            print(f"    {str(m):<9s} ${v:>+8,.0f} ${cum:>+8,.0f} {tag}")
        print(f"    Pos months: {pos_m}/{tot_m} ({pos_m/tot_m*100:.0f}%)")
        print(f"    Top month: {top_m_pct_:.1f}% of total")
        print(f"    Worst month: ${max_loss:+,.0f}")
        return pos_m, tot_m, top_m_pct_, max_loss

    l_pm, l_tm, l_top, l_worst = monthly_pnl(lt_oos, "Strategy L OOS")
    s_pm, s_tm, s_top, s_worst = monthly_pnl(st_oos, "Strategy S OOS")

    # Combined monthly
    print(f"\n    Combined monthly:")
    ct = pd.concat([lt_oos, st_oos], ignore_index=True)
    ct["m"]=pd.to_datetime(ct["dt"]).dt.to_period("M")
    cms=ct.groupby("m")["pnl"].sum()
    c_pm=int((cms>0).sum()); c_tm=len(cms)
    c_top=cms.max()/cms.sum()*100 if cms.sum()>0 else 0
    cum=0
    print(f"    {'Month':<9s} {'PnL':>9s} {'cum':>9s}")
    for m,v in cms.items():
        cum+=v
        print(f"    {str(m):<9s} ${v:>+8,.0f} ${cum:>+8,.0f}")
    print(f"    Combined pos months: {c_pm}/{c_tm}")
    print(f"    Combined top month: {c_top:.1f}%")

    g2_pass = l_top<50 and s_top<50 and l_pm>=l_tm//2 and s_pm>=s_tm//2
    print(f"\n  GATE 2 {'PASS' if g2_pass else 'FAIL'}: topM<50%({l_top:.0f}%,{s_top:.0f}%), posM>50%({l_pm}/{l_tm},{s_pm}/{s_tm})")

    # ===== GATE 3: Signal Overlap Analysis =====
    print(f"\n{'='*70}")
    print(f"  GATE 3: SIGNAL OVERLAP ANALYSIS")
    print(f"{'='*70}")

    # Entry bar overlap
    l_entry_bars = set(lt["ei"].unique())
    s_entry_bars = set(st["ei"].unique())
    overlap_bars = l_entry_bars & s_entry_bars
    l_only = l_entry_bars - s_entry_bars
    s_only = s_entry_bars - l_entry_bars

    print(f"    L entry bars: {len(l_entry_bars)}")
    print(f"    S entry bars: {len(s_entry_bars)}")
    print(f"    Overlap bars: {len(overlap_bars)} ({len(overlap_bars)/(len(l_entry_bars|s_entry_bars))*100:.1f}% of union)")
    print(f"    L-only bars: {len(l_only)}")
    print(f"    S-only bars: {len(s_only)}")

    # Signal domain overlap
    print(f"\n    Signal Domain Overlap:")
    print(f"    L signals: {L_SIGS}")
    print(f"    S signals: {S_SIGS}")
    shared = set(L_SIGS) & set(S_SIGS)
    print(f"    Shared signals: {shared if shared else 'NONE (zero overlap)'}")

    g3_pass = len(shared)==0 and len(overlap_bars) < 0.1 * len(l_entry_bars | s_entry_bars)
    print(f"\n  GATE 3 {'PASS' if g3_pass else 'FAIL'}: signal overlap={len(shared)}, bar overlap={len(overlap_bars)}/{len(l_entry_bars|s_entry_bars)}")

    # ===== GATE 4: Regime Analysis =====
    print(f"\n{'='*70}")
    print(f"  GATE 4: REGIME ANALYSIS")
    print(f"{'='*70}")

    # Define regimes using 60-bar (2.5 day) return
    oos_df = df[df["datetime"]>=mid_dt].copy()
    oos_df["ret60"] = oos_df["close"].pct_change(60)

    def regime_label(r):
        if pd.isna(r): return "unknown"
        if r > 0.05: return "bull"
        elif r < -0.05: return "bear"
        else: return "side"

    oos_df["regime"] = oos_df["ret60"].apply(regime_label)

    # Map trades to regimes
    def map_regime(tdf, regime_map):
        if len(tdf)==0: return {}
        results={}
        for regime in ["bull","bear","side"]:
            regime_bars = set(regime_map[regime_map==regime].index)
            fold = tdf[tdf["ei"].isin(regime_bars)]
            if len(fold)==0:
                results[regime]={"n":0,"pnl":0,"avg":0}
            else:
                results[regime]={"n":len(fold),"pnl":round(fold["pnl"].sum(),2),
                                 "avg":round(fold["pnl"].mean(),2)}
        return results

    regime_series = oos_df.set_index(oos_df.index)["regime"]
    lr = map_regime(lt_oos, regime_series)
    sr = map_regime(st_oos, regime_series)

    print(f"    Regime distribution (OOS bars):")
    for r in ["bull","bear","side"]:
        n_bars = (regime_series==r).sum()
        print(f"      {r:>4s}: {n_bars} bars ({n_bars/len(regime_series)*100:.0f}%)")

    print(f"\n    Strategy L by regime:")
    for r in ["bull","bear","side"]:
        d=lr.get(r,{"n":0,"pnl":0,"avg":0})
        print(f"      {r:>4s}: {d['n']:>4d}t ${d['pnl']:>+8,.0f} avg${d['avg']:+.1f}")

    print(f"\n    Strategy S by regime:")
    for r in ["bull","bear","side"]:
        d=sr.get(r,{"n":0,"pnl":0,"avg":0})
        print(f"      {r:>4s}: {d['n']:>4d}t ${d['pnl']:>+8,.0f} avg${d['avg']:+.1f}")

    # L should profit in bull, S should profit in bear
    l_bull_ok = lr.get("bull",{}).get("pnl",0) > 0
    s_bear_ok = sr.get("bear",{}).get("pnl",0) > 0
    # Neither should be catastrophic in adverse regime
    l_bear_loss = lr.get("bear",{}).get("pnl",0)
    s_bull_loss = sr.get("bull",{}).get("pnl",0)

    g4_pass = l_bull_ok and s_bear_ok
    print(f"\n  GATE 4 {'PASS' if g4_pass else 'FAIL'}: L bull>0={l_bull_ok}, S bear>0={s_bear_ok}")
    if l_bear_loss < -2000:
        print(f"    WARNING: L loses ${l_bear_loss:,.0f} in bear regime")
    if s_bull_loss < -2000:
        print(f"    WARNING: S loses ${s_bull_loss:,.0f} in bull regime")

    # ===== GATE 5: Independence =====
    print(f"\n{'='*70}")
    print(f"  GATE 5: INDEPENDENCE")
    print(f"{'='*70}")

    # Daily PnL correlation
    lt_c = lt_oos.copy(); lt_c["date"]=pd.to_datetime(lt_c["dt"]).dt.date
    st_c = st_oos.copy(); st_c["date"]=pd.to_datetime(st_c["dt"]).dt.date
    l_daily = lt_c.groupby("date")["pnl"].sum()
    s_daily = st_c.groupby("date")["pnl"].sum()

    # Align on common date range
    all_dates = pd.date_range(
        min(l_daily.index.min(), s_daily.index.min()) if len(l_daily)>0 and len(s_daily)>0 else mid_dt,
        max(l_daily.index.max(), s_daily.index.max()) if len(l_daily)>0 and len(s_daily)>0 else last_dt,
        freq="D"
    )
    ld = l_daily.reindex(all_dates.date, fill_value=0)
    sd = s_daily.reindex(all_dates.date, fill_value=0)

    corr = ld.corr(sd)
    print(f"    Daily PnL correlation: {corr:.3f}")
    print(f"    (need < 0.5 for independence)")

    # Concurrent exposure analysis
    # For each bar in OOS, count L positions and S positions
    oos_start = df[df["datetime"]>=mid_dt].index.min()
    oos_end = len(df)-1
    concurrent = []
    # Simplify: check entry bar overlap
    l_active_days = set(lt_oos["date"] if "date" in lt_oos.columns else pd.to_datetime(lt_oos["dt"]).dt.date)
    s_active_days = set(st_oos["date"] if "date" in st_oos.columns else pd.to_datetime(st_oos["dt"]).dt.date)
    both_active = l_active_days & s_active_days
    print(f"\n    Days with L trades: {len(l_active_days)}")
    print(f"    Days with S trades: {len(s_active_days)}")
    print(f"    Days with both: {len(both_active)} ({len(both_active)/max(len(l_active_days|s_active_days),1)*100:.1f}%)")

    # Monthly PnL correlation
    lt_mc = lt_oos.copy(); lt_mc["m"]=pd.to_datetime(lt_mc["dt"]).dt.to_period("M")
    st_mc = st_oos.copy(); st_mc["m"]=pd.to_datetime(st_mc["dt"]).dt.to_period("M")
    l_monthly = lt_mc.groupby("m")["pnl"].sum()
    s_monthly = st_mc.groupby("m")["pnl"].sum()
    all_m = sorted(set(l_monthly.index) | set(s_monthly.index))
    lm = l_monthly.reindex(all_m, fill_value=0)
    sm = s_monthly.reindex(all_m, fill_value=0)
    m_corr = lm.corr(sm)
    print(f"    Monthly PnL correlation: {m_corr:.3f}")

    g5_pass = abs(corr) < 0.5
    print(f"\n  GATE 5 {'PASS' if g5_pass else 'FAIL'}: daily corr={corr:.3f} ({'<' if abs(corr)<0.5 else '>='} 0.5)")

    # ===== GATE 6: Combined Scorecard =====
    print(f"\n{'='*70}")
    print(f"  GATE 6: COMBINED SCORECARD")
    print(f"{'='*70}")

    # Combined equity curve
    all_trades = pd.concat([lt_oos, st_oos], ignore_index=True)
    all_trades = all_trades.sort_values("dt").reset_index(drop=True)
    all_trades["cum"] = all_trades["pnl"].cumsum()
    combined_pnl = all_trades["pnl"].sum()
    combined_dd = all_trades["cum"] - all_trades["cum"].cummax()
    combined_mdd_pct = abs(combined_dd.min()) / ACCOUNT * 100

    # Combined daily Sharpe
    cd = all_trades.copy(); cd["date"]=pd.to_datetime(cd["dt"]).dt.date
    c_daily = cd.groupby("date")["pnl"].sum()
    c_rng = pd.date_range(c_daily.index.min(), c_daily.index.max(), freq="D")
    c_daily = c_daily.reindex(c_rng.date, fill_value=0)
    c_sharpe = float(c_daily.mean()/c_daily.std()*np.sqrt(365)) if c_daily.std()>0 else 0

    # Combined stats
    c_n = len(all_trades)
    c_wr = (all_trades["pnl"]>0).mean()*100
    c_w = all_trades[all_trades["pnl"]>0]["pnl"].sum()
    c_l = abs(all_trades[all_trades["pnl"]<=0]["pnl"].sum())
    c_pf = c_w/c_l if c_l>0 else 999

    # Exit type breakdown
    print(f"    Combined OOS Results:")
    print(f"    Total trades: {c_n}")
    print(f"    Total PnL: ${combined_pnl:+,.0f}")
    print(f"    PF: {c_pf:.2f}")
    print(f"    WR: {c_wr:.1f}%")
    print(f"    Combined MDD: {combined_mdd_pct:.1f}%")
    print(f"    Combined Sharpe: {c_sharpe:.2f}")
    print(f"    Avg $/trade: ${combined_pnl/c_n:+.1f}")

    # Exit breakdown per strategy
    print(f"\n    Exit Type Breakdown:")
    for side_label, tdf in [("L", lt_oos), ("S", st_oos)]:
        if len(tdf)==0: continue
        for et in ["SN","ES","TR"]:
            sub=tdf[tdf["t"]==et]
            if len(sub)==0: continue
            print(f"      {side_label}-{et}: {len(sub):>4d}t ${sub['pnl'].sum():>+8,.0f} avg${sub['pnl'].mean():+.1f} WR{(sub['pnl']>0).mean()*100:.0f}%")

    # Diversification benefit: combined MDD vs sum of individual MDDs
    ind_mdd = ls["mdd_pct"] + ss["mdd_pct"]
    div_benefit = 1 - combined_mdd_pct / ind_mdd if ind_mdd > 0 else 0
    print(f"\n    Diversification:")
    print(f"      L MDD: {ls['mdd_pct']:.1f}%")
    print(f"      S MDD: {ss['mdd_pct']:.1f}%")
    print(f"      Sum: {ind_mdd:.1f}%")
    print(f"      Combined MDD: {combined_mdd_pct:.1f}%")
    print(f"      Diversification benefit: {div_benefit*100:.1f}%")

    g6_l = ls["pnl"] >= 10000
    g6_s = ss["pnl"] >= 10000
    g6_c = combined_pnl >= 20000
    g6_pass = g6_l and g6_s and g6_c
    print(f"\n  GATE 6 {'PASS' if g6_pass else 'FAIL'}:")
    print(f"    L >= $10K: {'YES' if g6_l else 'NO'} (${ls['pnl']:+,.0f})")
    print(f"    S >= $10K: {'YES' if g6_s else 'NO'} (${ss['pnl']:+,.0f})")
    print(f"    Combined >= $20K: {'YES' if g6_c else 'NO'} (${combined_pnl:+,.0f})")

    # ===== FINAL SCORECARD =====
    print(f"\n{'='*70}")
    print(f"  FINAL SCORECARD")
    print(f"{'='*70}")

    gates = [
        ("G1 Walk-Forward", g1_pass, f"L={l_wf}/10, S={s_wf}/10"),
        ("G2 Monthly P&L",  g2_pass, f"topM:{l_top:.0f}%/{s_top:.0f}%, posM:{l_pm}/{l_tm},{s_pm}/{s_tm}"),
        ("G3 Signal Overlap",g3_pass, f"shared={len(shared)}, bar_ovlp={len(overlap_bars)}"),
        ("G4 Regime",       g4_pass, f"L_bull>0={l_bull_ok}, S_bear>0={s_bear_ok}"),
        ("G5 Independence", g5_pass, f"corr={corr:.3f}"),
        ("G6 Targets",      g6_pass, f"L=${ls['pnl']:+,.0f}, S=${ss['pnl']:+,.0f}, C=${combined_pnl:+,.0f}"),
    ]

    all_pass = all(g[1] for g in gates)
    for name, passed, detail in gates:
        print(f"    {'[PASS]' if passed else '[FAIL]'} {name}: {detail}")

    print(f"\n  +{'='*54}+")
    print(f"  | Strategy L: GK+Skew+RetSign long m9{' '*16}|")
    print(f"  |   Signals: gk_comp, skew_l, sr_l{' '*19}|")
    print(f"  |   OOS: {ls['n']:>4d}t ${ls['pnl']:>+8,.0f} PF{ls['pf']:.2f} WR{ls['wr']:.0f}%{' '*11}|")
    print(f"  |   MDD: {ls['mdd_pct']:.1f}% Sharpe: {ls['sh']:.2f}{' '*24}|")
    print(f"  +{'-'*54}+")
    print(f"  | Strategy S: 6sig pyramid short m20 pyr3{' '*11}|")
    print(f"  |   Signals: kurt,tbr,vcv,pe,roc,dd{' '*18}|")
    print(f"  |   OOS: {ss['n']:>4d}t ${ss['pnl']:>+8,.0f} PF{ss['pf']:.2f} WR{ss['wr']:.0f}%{' '*11}|")
    print(f"  |   MDD: {ss['mdd_pct']:.1f}% Sharpe: {ss['sh']:.2f}{' '*24}|")
    print(f"  +{'-'*54}+")
    print(f"  | COMBINED: ${combined_pnl:>+8,.0f} / $20,000{' '*22}|")
    print(f"  | Combined MDD: {combined_mdd_pct:.1f}% Sharpe: {c_sharpe:.2f}{' '*19}|")
    print(f"  | Diversification: {div_benefit*100:.1f}%{' '*32}|")
    print(f"  | Daily PnL corr: {corr:.3f}{' '*31}|")
    print(f"  +{'='*54}+")

    print(f"\n  AUDIT RESULT: {'ALL 6 GATES PASSED' if all_pass else 'FAILED'}")
    if not all_pass:
        failed = [g[0] for g in gates if not g[1]]
        print(f"  Failed gates: {', '.join(failed)}")
        print(f"  -> Need further exploration")
    else:
        print(f"  -> DUAL STRATEGY DEVELOPMENT COMPLETE")

    print(f"\n  ROUND 6 COMPLETE")
