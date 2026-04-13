"""
L+S Combined Validation
========================
L champion: 2-of-4 Signal Filter + ATR Sizing (bin_a76_s0.60, cap12, cd10)
  OOS $13,763, PF 5.87, WR 55.9%, MDD 8.3%, topM 19.4%, 9/9 PASS

S production: CMP-Portfolio v3 Mixed-TP (4 sub-strategies)
  OOS $10,113, PF 1.73, WR 65%

Combined target: ≥$20K, ≥10/12 positive months, worst month ≥ -$1K

This script runs both L and S backtests on the same data,
then combines their trade streams for joint evaluation.
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL=4000; FEE=4.0; ACCOUNT=10000
SAFENET_PCT=0.055; SN_PEN=0.25
EMA_SPAN=20; MIN_TRAIL=7; EARLY_STOP_PCT=0.01; EARLY_STOP_END=12
BRK_LOOK=10; BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
GK_SHORT=5; GK_LONG=20; GK_WIN=100; AMI_WINDOW=20; PCTILE_WIN=100
WARMUP=200; MAX_OPEN_PER_BAR=2

# S strategy parameters (CMP-Portfolio v3 from CLAUDE.md)
S_SUBS = [
    {"name":"S1","gk_thresh":40,"bl":8, "max_same":5,"exit_cd":6},
    {"name":"S2","gk_thresh":40,"bl":15,"max_same":5,"exit_cd":6},
    {"name":"S3","gk_thresh":30,"bl":10,"max_same":5,"exit_cd":6},
    {"name":"S4","gk_thresh":40,"bl":12,"max_same":5,"exit_cd":6},
]
S_TP=0.02; S_MAX_HOLD=12; S_SAFENET_PCT=0.055

def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url="https://fapi.binance.com/fapi/v1/klines"
    end_ms=int(time.time()*1000); start_ms=end_ms-days*24*3600*1000
    all_data=[]; cur=start_ms
    print(f"  Fetching {symbol} {interval}...")
    while cur<end_ms:
        params={"symbol":symbol,"interval":interval,"startTime":cur,"endTime":end_ms,"limit":1500}
        for att in range(3):
            try: r=requests.get(url,params=params,timeout=30); r.raise_for_status(); data=r.json(); break
            except:
                if att==2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur=data[-1][0]+1
        if len(data)<1500: break
        time.sleep(0.1)
    df=pd.DataFrame(all_data,columns=["open_time","open","high","low","close","volume","close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume","tbv"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["datetime"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    df["datetime"]=(df["datetime"]+timedelta(hours=8)).dt.tz_localize(None)
    df=df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df

def pctile_func(x):
    if x.max()==x.min(): return 50.0
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100.0

def calc_indicators(df):
    d=df.copy(); d["ret"]=d["close"].pct_change()
    d["ema20"]=d["close"].ewm(span=EMA_SPAN).mean()

    log_hl=np.log(d["high"]/d["low"]); log_co=np.log(d["close"]/d["open"])
    gk=0.5*log_hl**2-(2*np.log(2)-1)*log_co**2
    gk=gk.replace([np.inf,-np.inf],np.nan)
    gk_s=gk.rolling(GK_SHORT).mean(); gk_l=gk.rolling(GK_LONG).mean()
    d["gk_r"]=(gk_s/gk_l).replace([np.inf,-np.inf],np.nan)
    d["gk_pct"]=d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    dvol=d["volume"]*d["close"]
    d["ami_raw"]=(d["ret"].abs()/dvol).replace([np.inf,-np.inf],np.nan)
    d["ami"]=d["ami_raw"].rolling(AMI_WINDOW).mean().shift(1)
    d["ami_pct"]=d["ami"].rolling(PCTILE_WIN).apply(pctile_func)

    d["skew20"]=d["ret"].rolling(20).skew().shift(1)
    d["retsign15"]=(d["ret"]>0).astype(float).rolling(15).mean().shift(1)

    d["cs1"]=d["close"].shift(1)
    # L breakout (10 bar up)
    d["brk_max"]=d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["bl_up"]=d["cs1"]>d["brk_max"]
    # S breakout (multiple lookbacks, down)
    d["brk_min_8"]=d["close"].shift(2).rolling(7).min()
    d["bl_dn_8"]=d["cs1"]<d["brk_min_8"]
    d["brk_min_10"]=d["close"].shift(2).rolling(9).min()
    d["bl_dn_10"]=d["cs1"]<d["brk_min_10"]
    d["brk_min_12"]=d["close"].shift(2).rolling(11).min()
    d["bl_dn_12"]=d["cs1"]<d["brk_min_12"]
    d["brk_min_15"]=d["close"].shift(2).rolling(14).min()
    d["bl_dn_15"]=d["cs1"]<d["brk_min_15"]

    d["hour"]=d["datetime"].dt.hour; d["dow"]=d["datetime"].dt.weekday
    d["sok"]=~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    d["ym"]=d["datetime"].dt.to_period("M")

    # ATR
    tr = pd.concat([
        d["high"]-d["low"],
        (d["high"]-d["close"].shift(1)).abs(),
        (d["low"]-d["close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean().shift(1)
    d["atr_pct"] = d["atr14"].rolling(PCTILE_WIN).apply(pctile_func)

    # Signal flags for L
    d["sig_ami"]=(d["ami_pct"]<20)
    d["sig_skew"]=(d["skew20"]>1.0)
    d["sig_ret"]=(d["retsign15"]>0.60)
    d["sig_gk"]=(d["gk_pct"]<30)
    d["sig_count4"]=(d["sig_ami"].astype(int)+d["sig_skew"].astype(int)+
                     d["sig_ret"].astype(int)+d["sig_gk"].astype(int))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)

# ─── L backtest: 2-of-4 + ATR sizing ───

def bt_long_atr(df, atr_thresh=76, scale_down=0.60, max_same=9,
                exit_cd=10, monthly_entry_cap=12):
    entry_mask = (df["sig_count4"]>=2) & df["bl_up"]
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;EMA=df["ema20"].values;DT=df["datetime"].values
    MASK=entry_mask.values;SOK=df["sok"].values;YM=df["ym"].values
    ATR_P=df["atr_pct"].values;n=len(df)
    pos=[];trades=[];lx=-9999;boc={};month_entries={}
    for i in range(WARMUP,n-1):
        lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i];nxo=O[i+1];cur_ym=YM[i]
        np_=[]
        for p in pos:
            bh=i-p["ei"];done=False;not_=p["not"]
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN;pnl=(ep-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":"L"});lx=i;done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"side":"L"});lx=i;done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*not_/p["e"]-FEE*(not_/NOTIONAL)
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"side":"L"});lx=i;done=True
            if not done: np_.append(p)
        pos=np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        cur_ent=month_entries.get(cur_ym,0)
        if cur_ent>=monthly_entry_cap: continue
        atr_p=ATR_P[i] if not np.isnan(ATR_P[i]) else 50
        not_size = NOTIONAL * scale_down if atr_p > atr_thresh else NOTIONAL
        boc[i]=boc.get(i,0)+1; month_entries[cur_ym]=cur_ent+1
        pos.append({"e":nxo,"ei":i,"not":not_size})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","side"])

# ─── S backtest: CMP-Portfolio v3 (TP + MaxHold) ───

def bt_short_cmp(df):
    """Run all 4 S sub-strategies independently, combine trade streams."""
    H=df["high"].values;Lo=df["low"].values;O=df["open"].values
    C=df["close"].values;DT=df["datetime"].values
    SOK=df["sok"].values;YM=df["ym"].values;GK_P=df["gk_pct"].values;n=len(df)

    # Pre-compute breakout arrays for each sub
    bl_arrays = {}
    for sub in S_SUBS:
        bl_col = f"bl_dn_{sub['bl']}"
        bl_arrays[sub["name"]] = df[bl_col].values

    all_trades = []
    for sub in S_SUBS:
        sname=sub["name"]; gk_t=sub["gk_thresh"]; ms=sub["max_same"]; ecd=sub["exit_cd"]
        BL_DN=bl_arrays[sname]
        pos=[];lx=-9999;boc={}
        for i in range(WARMUP,n-1):
            h=H[i];lo=Lo[i];c=C[i];dt=DT[i];nxo=O[i+1]
            np_=[]
            for p in pos:
                bh=i-p["ei"];done=False
                # SafeNet (short: price goes UP is bad)
                sn=p["e"]*(1+S_SAFENET_PCT)
                if h>=sn:
                    ep=sn+(h-sn)*SN_PEN
                    pnl=(p["e"]-ep)*NOTIONAL/p["e"]-FEE
                    all_trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"side":sname});lx=i;done=True
                # TP: fixed 2%
                if not done:
                    tp_price=p["e"]*(1-S_TP)
                    if lo<=tp_price:
                        pnl=(p["e"]-tp_price)*NOTIONAL/p["e"]-FEE
                        all_trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt,"side":sname});lx=i;done=True
                # MaxHold
                if not done and bh>=S_MAX_HOLD:
                    pnl=(p["e"]-c)*NOTIONAL/p["e"]-FEE
                    all_trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt,"side":sname});lx=i;done=True
                if not done: np_.append(p)
            pos=np_
            if not _b(SOK[i]) or not _b(BL_DN[i]): continue
            gk_v=GK_P[i]
            if np.isnan(gk_v) or gk_v>=gk_t: continue
            if (i-lx)<ecd or len(pos)>=ms: continue
            if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
            boc[i]=boc.get(i,0)+1
            pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(all_trades) if all_trades else pd.DataFrame(columns=["pnl","t","b","dt","side"])

def evaluate(tdf, start_dt, end_dt, label=""):
    tdf=tdf.copy();tdf["dt"]=pd.to_datetime(tdf["dt"])
    p=tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].reset_index(drop=True)
    n=len(p)
    if n==0: return None
    pnl=p["pnl"].sum()
    w=p[p["pnl"]>0]["pnl"].sum();l_=abs(p[p["pnl"]<=0]["pnl"].sum())
    pf=w/l_ if l_>0 else 999;wr=(p["pnl"]>0).mean()*100
    eq=p["pnl"].cumsum();dd=eq-eq.cummax();mdd=abs(dd.min())/ACCOUNT*100
    p["m"]=p["dt"].dt.to_period("M");ms=p.groupby("m")["pnl"].sum()
    pos_m=(ms>0).sum();mt=len(ms)
    if pnl>0:
        top_v=ms.max();top_n=str(ms.idxmax());top_pct=top_v/pnl*100
    else:
        top_v=ms.max() if len(ms)>0 else 0;top_n="N/A";top_pct=999
    nb=pnl-top_v if pnl>0 else pnl
    worst_v=ms.min() if len(ms)>0 else 0;worst_n=str(ms.idxmin()) if len(ms)>0 else "N/A"
    days=(end_dt-start_dt).days;tpm=n/(days/30.44) if days>0 else 0
    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "months":mt,"pos_months":pos_m,"top_pct":top_pct,"top_m":top_n,
            "top_v":top_v,"nb":nb,"worst_m":worst_n,"worst_v":worst_v,
            "tpm":tpm,"monthly":ms,"avg":pnl/n if n else 0}

def walk_forward_6(tdf, s, e):
    tdf=tdf.copy();tdf["dt"]=pd.to_datetime(tdf["dt"])
    results=[]
    for f in range(6):
        ts=s+pd.DateOffset(months=f*2);te=min(ts+pd.DateOffset(months=2),e)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        results.append({"fold":f+1,"pnl":fp,"n":len(tt),"pos":fp>0})
    return results

def score9(r, wf_pos):
    checks=[
        ("PnL>=10K",r["pnl"]>=10000),("PF>=1.5",r["pf"]>=1.5),
        ("MDD<=25",r["mdd"]<=25),("TPM>=10",r["tpm"]>=10),
        ("PM>=9",r["pos_months"]>=9),("topM<=20",r["top_pct"]<=20),
        ("-bst>=8K",r["nb"]>=8000),("WF>=5/6",wf_pos>=5),("bar<=2",True)]
    return checks, sum(1 for _,v in checks if v)

def pr(r, label=""):
    if r is None: return
    print(f"  {label:>6} {r['n']:>4}t ${r['pnl']:>+9,.0f} PF{r['pf']:.2f} WR{r['wr']:.1f}% "
          f"MDD{r['mdd']:.1f}% TPM{r['tpm']:.1f} PM{r['pos_months']}/{r['months']} "
          f"topM{r['top_pct']:.1f}% -bst${r['nb']:>+,.0f}")


if __name__=="__main__":
    print("="*76)
    print("  L+S COMBINED VALIDATION")
    print("="*76)

    df_raw=fetch_klines("ETHUSDT","1h",730)
    last_dt=df_raw["datetime"].iloc[-1]
    fs=last_dt-pd.Timedelta(days=730);mid=last_dt-pd.Timedelta(days=365);fe=last_dt
    print(f"  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df=calc_indicators(df_raw)

    # ── Run L backtest (champion config) ──
    print(f"\n{'─'*76}")
    print("  L Strategy: 2-of-4 + ATR Sizing (atr>76, s=0.60, cap12, cd10)")
    print(f"{'─'*76}")
    l_trades = bt_long_atr(df, atr_thresh=76, scale_down=0.60, max_same=9,
                           exit_cd=10, monthly_entry_cap=12)
    l_oos = evaluate(l_trades, mid, fe, "L_OOS")
    l_is  = evaluate(l_trades, fs, mid, "L_IS")
    l_wf  = walk_forward_6(l_trades, mid, fe)
    l_wf_pos = sum(1 for w in l_wf if w["pos"])
    print(f"  OOS:"); pr(l_oos, "L")
    print(f"  IS: "); pr(l_is, "L_IS")
    l_checks, l_score = score9(l_oos, l_wf_pos)
    print(f"  Score: {l_score}/9")
    for cn,cv in l_checks:
        print(f"    {'✓' if cv else '✗'} {cn}")
    print(f"  WF: {l_wf_pos}/6")
    for w in l_wf:
        print(f"    F{w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {'✓' if w['pos'] else '✗'}")

    # ── Run S backtest (production CMP-Portfolio) ──
    print(f"\n{'─'*76}")
    print("  S Strategy: CMP-Portfolio v3 (4 subs, TP 2%, MH 12)")
    print(f"{'─'*76}")
    s_trades = bt_short_cmp(df)
    s_oos = evaluate(s_trades, mid, fe, "S_OOS")
    s_is  = evaluate(s_trades, fs, mid, "S_IS")
    s_wf  = walk_forward_6(s_trades, mid, fe)
    s_wf_pos = sum(1 for w in s_wf if w["pos"])
    print(f"  OOS:"); pr(s_oos, "S")
    print(f"  IS: "); pr(s_is, "S_IS")

    # ── Combined L+S ──
    print(f"\n{'='*76}")
    print("  COMBINED L+S VALIDATION")
    print(f"{'='*76}")

    combined = pd.concat([l_trades, s_trades], ignore_index=True)
    combined["dt"] = pd.to_datetime(combined["dt"])
    combined = combined.sort_values("dt").reset_index(drop=True)

    c_oos = evaluate(combined, mid, fe, "L+S_OOS")
    c_is  = evaluate(combined, fs, mid, "L+S_IS")
    c_wf  = walk_forward_6(combined, mid, fe)
    c_wf_pos = sum(1 for w in c_wf if w["pos"])

    print(f"\n  OOS:"); pr(c_oos, "L+S")
    print(f"  IS: "); pr(c_is, "L+S")

    # Monthly breakdown
    print(f"\n  Monthly PnL (OOS):")
    print(f"  {'Month':>8} {'L':>9} {'S':>9} {'Total':>9} {'Cum':>9}")
    l_m = l_oos["monthly"] if l_oos else pd.Series(dtype=float)
    s_m = s_oos["monthly"] if s_oos else pd.Series(dtype=float)
    all_months = sorted(set(list(l_m.index) + list(s_m.index)))
    cum = 0
    pos_count = 0; neg_count = 0; worst_m_val = 9999; worst_m_name = ""
    for m in all_months:
        lv = l_m.get(m, 0); sv = s_m.get(m, 0); tv = lv + sv; cum += tv
        if tv > 0: pos_count += 1
        else: neg_count += 1
        if tv < worst_m_val: worst_m_val = tv; worst_m_name = str(m)
        print(f"  {str(m):>8} ${lv:>+8,.0f} ${sv:>+8,.0f} ${tv:>+8,.0f} ${cum:>+8,.0f}")

    # Combined gate checks
    print(f"\n  ═══ COMBINED GATE CHECKS ═══")
    total_pnl = c_oos["pnl"] if c_oos else 0
    print(f"  Total PnL:       ${total_pnl:>+,.0f}  {'✓' if total_pnl>=20000 else '✗'} (≥$20K)")
    print(f"  Positive Months: {pos_count}/{pos_count+neg_count}  {'✓' if pos_count>=10 else '✗'} (≥10/12)")
    print(f"  Worst Month:     ${worst_m_val:>+,.0f} ({worst_m_name})  {'✓' if worst_m_val>=-1000 else '✗'} (≥-$1K)")
    print(f"  L PF:            {l_oos['pf']:.2f}  {'✓' if l_oos['pf']>=1.5 else '✗'} (≥1.5)")
    print(f"  S PF:            {s_oos['pf']:.2f}  {'✓' if s_oos['pf']>=1.5 else '✗'} (≥1.5)")
    print(f"  L MDD:           {l_oos['mdd']:.1f}%  {'✓' if l_oos['mdd']<=25 else '✗'} (≤25%)")
    print(f"  Combined MDD:    {c_oos['mdd']:.1f}%  {'✓' if c_oos['mdd']<=25 else '✗'} (≤25%)")
    print(f"  Combined WF:     {c_wf_pos}/6")
    for w in c_wf:
        print(f"    F{w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {'✓' if w['pos'] else '✗'}")

    all_pass = (total_pnl>=20000 and pos_count>=10 and worst_m_val>=-1000)
    print(f"\n  ═══ {'ALL GATES PASS ✓✓✓' if all_pass else 'GATES NOT ALL PASSED'} ═══")

    # Also test with other L configs for robustness
    print(f"\n{'='*76}")
    print("  ROBUSTNESS: Multiple L ATR configs combined with S")
    print(f"{'='*76}")
    configs = [
        ("a76_s0.60", 76, 0.60),
        ("a76_s0.55", 76, 0.55),
        ("a73_s0.60", 73, 0.60),
        ("a75_s0.60", 75, 0.60),
        ("a78_s0.65", 78, 0.65),
    ]
    print(f"  {'Config':>12} {'L_PnL':>8} {'S_PnL':>8} {'Total':>8} {'PM+':>4} {'Worst':>8} {'Pass':>4}")
    for name, at, sc in configs:
        lt = bt_long_atr(df, atr_thresh=at, scale_down=sc)
        lr = evaluate(lt, mid, fe)
        comb_t = pd.concat([lt, s_trades], ignore_index=True)
        cr = evaluate(comb_t, mid, fe)
        if lr and cr:
            lm = lr["monthly"]; sm = s_oos["monthly"]
            am = sorted(set(list(lm.index) + list(sm.index)))
            pm = 0; wst = 9999
            for m in am:
                tv = lm.get(m, 0) + sm.get(m, 0)
                if tv > 0: pm += 1
                if tv < wst: wst = tv
            ok = cr["pnl"]>=20000 and pm>=10 and wst>=-1000
            print(f"  {name:>12} ${lr['pnl']:>+7,.0f} ${s_oos['pnl']:>+7,.0f} "
                  f"${cr['pnl']:>+7,.0f} {pm:>3} ${wst:>+7,.0f} "
                  f"{'✓✓✓' if ok else '✗'}")

    print(f"\n{'='*76}")
    print("  VALIDATION COMPLETE")
    print(f"{'='*76}")
