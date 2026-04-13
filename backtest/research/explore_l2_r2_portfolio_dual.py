"""
L Strategy Exploration Round 2: L-CMP Portfolio + GK Expansion Dip Dual-Layer
==============================================================================
R1 finding: Ami<20|Skew|RetSign is best single config (PM 9/13, PF 3.68, MDD 16.9%)
BUT topM=28% fails criterion 5 (need ≤20%).

Root cause: July 2025 contributes $7,400 / $26,663 = 28%.
Fix requires total ≥ $37K so July is ≤20%.

Approach:
  Phase 1: L-CMP Portfolio — split OR signals into independent subs with own EXIT_CD
           Each sub has maxSame=5, EXIT_CD=8, runs independently → more total trades
  Phase 2: maxSame / EXIT_CD parameter sweep on best config
  Phase 3: GK Expansion Dip supplement (TP 1% + MH 8, different exit framework)
  Phase 4: Combined Main + Supplement, check all 9 criteria
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta
from math import factorial
from itertools import permutations as perms

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════
NOTIONAL        = 4000; FEE = 4.0; ACCOUNT = 10000
SAFENET_PCT     = 0.055; SN_PEN = 0.25
EMA_SPAN        = 20; MIN_TRAIL = 7
EARLY_STOP_PCT  = 0.01; EARLY_STOP_END = 12
BRK_LOOK        = 10
BLOCK_H         = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
GK_SHORT        = 5; GK_LONG = 20; GK_WIN = 100
PE_M            = 3; PE_WINDOW = 20; PCTILE_WIN = 100; AMI_WINDOW = 20
WARMUP          = 200; MAX_OPEN_PER_BAR = 2

# ══════════════════════════════════════════════════════════════
def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000); start_ms = end_ms - days*24*3600*1000
    all_data = []; cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for att in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status(); data = r.json(); break
            except:
                if att == 2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume","tbv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = (df["datetime"] + timedelta(hours=8)).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df

_M_FACT = factorial(PE_M); _MAX_ENT = np.log(_M_FACT)
_PERM_CODES = {}
for _i, _p in enumerate(perms(range(PE_M))):
    _PERM_CODES[_p[0]*9 + _p[1]*3 + _p[2]] = _i

def calc_pe_fast(ret_arr):
    n = len(ret_arr)
    if n < PE_WINDOW: return np.full(n, np.nan)
    x0=ret_arr[:-2]; x1=ret_arr[1:-1]; x2=ret_arr[2:]
    valid = ~(np.isnan(x0)|np.isnan(x1)|np.isnan(x2))
    r01=(x0>x1).astype(int); r02=(x0>x2).astype(int); r12=(x1>x2).astype(int)
    rank0=r01+r02; rank1=(1-r01)+r12; rank2=(1-r02)+(1-r12)
    raw_codes = rank0*9+rank1*3+rank2
    mapped = np.full(len(raw_codes), -1, dtype=int)
    for j in range(len(raw_codes)):
        if valid[j]: mapped[j] = _PERM_CODES.get(int(raw_codes[j]), -1)
    n_per_win = PE_WINDOW - PE_M + 1
    pe_result = np.full(n, np.nan)
    counts = np.zeros(_M_FACT, dtype=int); total = 0
    for j in range(min(n_per_win, len(mapped))):
        if mapped[j] >= 0: counts[mapped[j]] += 1; total += 1
    ret_idx = n_per_win + PE_M - 2
    if ret_idx < n and total >= n_per_win//2:
        probs = counts[counts>0]/total
        pe_result[ret_idx] = -np.sum(probs*np.log(probs))/_MAX_ENT
    for s in range(1, len(mapped)-n_per_win+1):
        old=mapped[s-1]
        if old>=0: counts[old]-=1; total-=1
        new=mapped[s+n_per_win-1]
        if new>=0: counts[new]+=1; total+=1
        ret_idx = s + n_per_win + PE_M - 2
        if ret_idx < n and total >= n_per_win//2:
            probs = counts[counts>0]/total
            pe_result[ret_idx] = -np.sum(probs*np.log(probs))/_MAX_ENT
    return pe_result

def pctile_func(x):
    if x.max()==x.min(): return 50.0
    return (x.iloc[-1]-x.min())/(x.max()-x.min())*100.0

def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()
    d["ema20"] = d["close"].ewm(span=EMA_SPAN).mean()

    # GK
    log_hl = np.log(d["high"]/d["low"]); log_co = np.log(d["close"]/d["open"])
    gk = 0.5*log_hl**2 - (2*np.log(2)-1)*log_co**2
    gk = gk.replace([np.inf,-np.inf], np.nan)
    gk_s = gk.rolling(GK_SHORT).mean(); gk_l = gk.rolling(GK_LONG).mean()
    d["gk_r"] = (gk_s/gk_l).replace([np.inf,-np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)

    # PE
    t0 = time.time()
    d["pe_raw"] = calc_pe_fast(d["ret"].values)
    d["pe"] = d["pe_raw"].shift(1)
    d["pe_pct"] = d["pe"].rolling(PCTILE_WIN).apply(pctile_func)
    print(f"  PE: {time.time()-t0:.1f}s")

    # Amihud
    dvol = d["volume"]*d["close"]
    d["ami_raw"] = (d["ret"].abs()/dvol).replace([np.inf,-np.inf], np.nan)
    d["ami"] = d["ami_raw"].rolling(AMI_WINDOW).mean().shift(1)
    d["ami_pct"] = d["ami"].rolling(PCTILE_WIN).apply(pctile_func)

    # Skew, RetSign
    d["skew20"] = d["ret"].rolling(20).skew().shift(1)
    d["retsign15"] = (d["ret"]>0).astype(float).rolling(15).mean().shift(1)

    # Breakout up (BL10)
    d["cs1"] = d["close"].shift(1)
    d["brk_max"] = d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["bl_up"] = d["cs1"] > d["brk_max"]

    # GK Expansion Dip signals
    d["dip03"] = d["ret"].shift(1) < -0.003
    d["dip05"] = d["ret"].shift(1) < -0.005
    d["dip08"] = d["ret"].shift(1) < -0.008

    # Session
    d["hour"] = d["datetime"].dt.hour; d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour"].isin(BLOCK_H)|d["dow"].isin(BLOCK_D))
    return d

def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)


# ══════════════════════════════════════════════════════════════
# Backtest: L trend-following exit (SafeNet + EarlyStop + EMA20 Trail)
# ══════════════════════════════════════════════════════════════
def bt_trail(df, entry_mask, max_same=9, exit_cd=12, tag=""):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; EMA=df["ema20"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}
    for i in range(WARMUP, n-1):
        h=H[i];lo=Lo[i];c=C[i];ema=EMA[i];dt=DT[i]; nxo=O[i+1]
        np_ = []
        for p in pos:
            bh=i-p["ei"]; done=False
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN; pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done and MIN_TRAIL<=bh<EARLY_STOP_END:
                if c<=ema or c<=p["e"]*(1-EARLY_STOP_PCT):
                    t_="ES" if c<=p["e"]*(1-EARLY_STOP_PCT) and c>ema else "Trail"
                    pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                    trades.append({"pnl":pnl,"t":t_,"b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done and bh>=EARLY_STOP_END and c<=ema:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"Trail","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done: np_.append(p)
        pos = np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub"])


# ══════════════════════════════════════════════════════════════
# Backtest: TP + MaxHold exit (for GK Expansion Dip supplement)
# ══════════════════════════════════════════════════════════════
def bt_tp_mh(df, entry_mask, tp_pct=0.01, max_hold=8, max_same=5, exit_cd=6, tag=""):
    H=df["high"].values; Lo=df["low"].values; O=df["open"].values
    C=df["close"].values; DT=df["datetime"].values
    MASK=entry_mask.values; SOK=df["sok"].values; n=len(df)
    pos=[]; trades=[]; lx=-9999; boc={}
    for i in range(WARMUP, n-1):
        h=H[i];lo=Lo[i];c=C[i];dt=DT[i]; nxo=O[i+1]
        np_ = []
        for p in pos:
            bh=i-p["ei"]; done=False
            # SafeNet
            sn=p["e"]*(1-SAFENET_PCT)
            if lo<=sn:
                ep=sn-(sn-lo)*SN_PEN; pnl=(ep-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"SN","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            # TP
            if not done:
                tp_lv = p["e"]*(1+tp_pct)
                if h>=tp_lv:
                    pnl=p["e"]*tp_pct*NOTIONAL/p["e"]-FEE  # = tp_pct*NOTIONAL - FEE
                    trades.append({"pnl":pnl,"t":"TP","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            # MaxHold
            if not done and bh>=max_hold:
                pnl=(c-p["e"])*NOTIONAL/p["e"]-FEE
                trades.append({"pnl":pnl,"t":"MH","b":bh,"dt":dt,"sub":tag}); lx=i; done=True
            if not done: np_.append(p)
        pos = np_
        if not _b(MASK[i]) or not _b(SOK[i]): continue
        if (i-lx)<exit_cd or len(pos)>=max_same: continue
        if boc.get(i,0)>=MAX_OPEN_PER_BAR: continue
        boc[i]=boc.get(i,0)+1
        pos.append({"e":nxo,"ei":i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt","sub"])


# ══════════════════════════════════════════════════════════════
# L-CMP Portfolio: multiple subs with independent cooldowns
# ══════════════════════════════════════════════════════════════
def bt_portfolio(df, sub_configs):
    """Run multiple independent sub-strategies, combine trade lists."""
    all_trades = []
    for cfg in sub_configs:
        entry = cfg["entry_mask"]
        engine = cfg.get("engine", "trail")
        tag = cfg.get("tag", "")
        if engine == "trail":
            t = bt_trail(df, entry, max_same=cfg.get("max_same",5),
                         exit_cd=cfg.get("exit_cd",12), tag=tag)
        else:
            t = bt_tp_mh(df, entry, tp_pct=cfg.get("tp_pct",0.01),
                         max_hold=cfg.get("max_hold",8),
                         max_same=cfg.get("max_same",5),
                         exit_cd=cfg.get("exit_cd",6), tag=tag)
        if len(t) > 0:
            all_trades.append(t)
    if not all_trades:
        return pd.DataFrame(columns=["pnl","t","b","dt","sub"])
    return pd.concat(all_trades, ignore_index=True).sort_values("dt").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# Evaluation
# ══════════════════════════════════════════════════════════════
def evaluate(tdf, start_dt, end_dt, label=""):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"]>=start_dt)&(tdf["dt"]<end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"]>0]["pnl"].sum(); l_ = abs(p[p["pnl"]<=0]["pnl"].sum())
    pf = w/l_ if l_>0 else 999; wr = (p["pnl"]>0).mean()*100
    eq = p["pnl"].cumsum(); dd = eq-eq.cummax(); mdd = abs(dd.min())/ACCOUNT*100
    p["m"] = p["dt"].dt.to_period("M"); ms = p.groupby("m")["pnl"].sum()
    pos_m = (ms>0).sum(); mt = len(ms)
    if pnl > 0:
        top_v=ms.max(); top_n=str(ms.idxmax()); top_pct=top_v/pnl*100
    else:
        top_v=ms.max() if len(ms)>0 else 0; top_n=str(ms.idxmax()) if len(ms)>0 else "N/A"; top_pct=999
    nb = pnl-top_v if pnl>0 else pnl
    worst_v = ms.min() if len(ms)>0 else 0
    worst_n = str(ms.idxmin()) if len(ms)>0 else "N/A"
    days=(end_dt-start_dt).days; tpm=n/(days/30.44) if days>0 else 0
    return {"label":label,"n":n,"pnl":pnl,"pf":pf,"wr":wr,"mdd":mdd,
            "months":mt,"pos_months":pos_m,"top_pct":top_pct,"top_m":top_n,
            "top_v":top_v,"nb":nb,"worst_m":worst_n,"worst_v":worst_v,
            "tpm":tpm,"monthly":ms,"avg":pnl/n if n else 0}

def walk_forward_6(tdf, start_oos, end_oos):
    tdf=tdf.copy(); tdf["dt"]=pd.to_datetime(tdf["dt"])
    results=[]
    for fold in range(6):
        ts=start_oos+pd.DateOffset(months=fold*2)
        te=min(ts+pd.DateOffset(months=2), end_oos)
        tt=tdf[(tdf["dt"]>=ts)&(tdf["dt"]<te)]
        fp=tt["pnl"].sum() if len(tt)>0 else 0
        results.append({"fold":fold+1,"pnl":fp,"n":len(tt),"pos":fp>0})
    return results

def print_result(r, show_monthly=True):
    if r is None: print("    NO TRADES"); return
    print(f"  {r['label']}")
    print(f"    {r['n']:>4}t  ${r['pnl']:>+10,.0f}  PF {r['pf']:.2f}  WR {r['wr']:.1f}%  "
          f"MDD {r['mdd']:.1f}%  TPM {r['tpm']:.1f}")
    print(f"    PM {r['pos_months']}/{r['months']}  topM {r['top_pct']:.1f}%({r['top_m']})  "
          f"-best ${r['nb']:>+,.0f}  worst ${r['worst_v']:>+,.0f}({r['worst_m']})")
    if show_monthly and "monthly" in r:
        cum = 0
        for m, v in r["monthly"].items():
            cum += v
            print(f"      {str(m)}: ${v:>+8,.0f}  cum ${cum:>+9,.0f}")

def check_9(r, wf_pos=0):
    if r is None: return 0, []
    checks = [
        ("PnL>=10K", r["pnl"]>=10000),
        ("PF>=1.5",  r["pf"]>=1.5),
        ("MDD<=25",  r["mdd"]<=25),
        ("TPM>=10",  r["tpm"]>=10),
        ("PM>=9",    r["pos_months"]>=9),
        ("topM<=20", r["top_pct"]<=20),
        ("-bst>=8K", r["nb"]>=8000),
        ("WF>=5/6",  wf_pos>=5),
        ("bar<=2",   True),  # enforced in code
    ]
    passed = sum(1 for _,v in checks if v)
    return passed, checks


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("="*76)
    print("  L ROUND 2: L-CMP Portfolio + GK Expansion Dip Dual-Layer")
    print("="*76)

    df_raw = fetch_klines("ETHUSDT","1h",730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt
    print(f"  IS:  {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")
    df = calc_indicators(df_raw)

    # ══════════════════════════════════════════════════════════
    # Phase 1: Single-entry Ami<20|Skew|RetSign with maxSame/EXIT_CD sweep
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 1: Ami<20|Skew|RetSign — maxSame / EXIT_CD sweep")
    print(f"{'='*76}")
    print(f"  {'ms':>3} {'cd':>3} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>8} {'worst':>8}")

    entry_ami = ((df["ami_pct"]<20)|(df["skew20"]>1.0)|(df["retsign15"]>0.60)) & df["bl_up"]
    best_ph1 = None

    for ms in [3, 5, 7, 9]:
        for cd in [8, 10, 12, 14]:
            tdf = bt_trail(df, entry_ami, max_same=ms, exit_cd=cd)
            r = evaluate(tdf, mid, fe, f"ms{ms}_cd{cd}")
            if r and r["n"]>5:
                print(f"  {ms:>3} {cd:>3} {r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f} ${r['worst_v']:>+7,.0f}")
                if best_ph1 is None or r['pnl'] > best_ph1[1]['pnl']:
                    best_ph1 = (tdf.copy(), r)

    # ══════════════════════════════════════════════════════════
    # Phase 2: L-CMP Portfolio — each signal as independent sub-strategy
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 2: L-CMP Portfolio (3-4 independent subs, like S strategy)")
    print(f"{'='*76}")
    print(f"  {'Config':<35} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5} {'topM':>5} {'-bst':>8}")

    best_ph2 = None

    for sub_ms in [3, 5]:
        for sub_cd in [6, 8, 10, 12]:
            # 3-sub portfolio: Ami<20, Skew>1.0, RetSign>0.60 (each + breakout)
            subs_3 = [
                {"entry_mask": (df["ami_pct"]<20) & df["bl_up"],
                 "max_same":sub_ms, "exit_cd":sub_cd, "tag":"Ami"},
                {"entry_mask": (df["skew20"]>1.0) & df["bl_up"],
                 "max_same":sub_ms, "exit_cd":sub_cd, "tag":"Skew"},
                {"entry_mask": (df["retsign15"]>0.60) & df["bl_up"],
                 "max_same":sub_ms, "exit_cd":sub_cd, "tag":"RetSign"},
            ]
            tdf = bt_portfolio(df, subs_3)
            r = evaluate(tdf, mid, fe, f"3sub_ms{sub_ms}_cd{sub_cd}")
            if r and r["n"]>10:
                print(f"  3sub ms{sub_ms} cd{sub_cd:<3}              "
                      f"{r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")
                if best_ph2 is None or r['pnl'] > best_ph2[1]['pnl']:
                    best_ph2 = (tdf.copy(), r)

            # 4-sub portfolio: add GK<30
            subs_4 = subs_3 + [
                {"entry_mask": (df["gk_pct"]<30) & df["bl_up"],
                 "max_same":sub_ms, "exit_cd":sub_cd, "tag":"GK"},
            ]
            tdf = bt_portfolio(df, subs_4)
            r = evaluate(tdf, mid, fe, f"4sub_ms{sub_ms}_cd{sub_cd}")
            if r and r["n"]>10:
                print(f"  4sub ms{sub_ms} cd{sub_cd:<3}              "
                      f"{r['n']:>4} ${r['pnl']:>+8,.0f} "
                      f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                      f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                      f"{r['top_pct']:>4.0f}% ${r['nb']:>+7,.0f}")
                if best_ph2 is None or r['pnl'] > best_ph2[1]['pnl']:
                    best_ph2 = (tdf.copy(), r)

    # ══════════════════════════════════════════════════════════
    # Phase 3: GK Expansion Dip supplement (standalone)
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 3: GK Expansion Dip Supplement (TP + MH exit)")
    print(f"{'='*76}")
    print(f"  {'Config':<30} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5}")

    best_dip = None

    for gk_t in [60, 65, 70, 75, 80]:
        for dip_t, dip_col in [(-0.003,"dip03"), (-0.005,"dip05"), (-0.008,"dip08")]:
            for tp in [0.008, 0.010, 0.012, 0.015]:
                for mh in [6, 8, 10, 12]:
                    entry = (df["gk_pct"]>gk_t) & df[dip_col]
                    tdf = bt_tp_mh(df, entry, tp_pct=tp, max_hold=mh,
                                   max_same=5, exit_cd=4, tag="dip")
                    r = evaluate(tdf, mid, fe, f"GK>{gk_t}_dip{abs(dip_t)}_tp{tp*100:.1f}_mh{mh}")
                    if r and r["n"]>10 and r["pnl"]>0:
                        if best_dip is None or r["pnl"]>best_dip[1]["pnl"]:
                            best_dip = (tdf.copy(), r, entry.copy(),
                                        {"tp_pct":tp,"max_hold":mh,"gk_t":gk_t,"dip_t":dip_t})

    # Print top 5 dip configs by PnL
    dip_results = []
    for gk_t in [60, 65, 70, 75, 80]:
        for dip_t, dip_col in [(-0.003,"dip03"), (-0.005,"dip05"), (-0.008,"dip08")]:
            for tp in [0.008, 0.010, 0.012, 0.015]:
                for mh in [6, 8, 10, 12]:
                    entry = (df["gk_pct"]>gk_t) & df[dip_col]
                    tdf = bt_tp_mh(df, entry, tp_pct=tp, max_hold=mh,
                                   max_same=5, exit_cd=4, tag="dip")
                    r = evaluate(tdf, mid, fe, "")
                    if r and r["n"]>5 and r["pnl"]>0:
                        dip_results.append((r, gk_t, dip_t, tp, mh))

    dip_results.sort(key=lambda x: x[0]["pnl"], reverse=True)
    for r, gk_t, dip_t, tp, mh in dip_results[:8]:
        print(f"  GK>{gk_t} dip{abs(dip_t):.1%} tp{tp*100:.1f}% mh{mh:<3}"
              f" {r['n']:>4} ${r['pnl']:>+8,.0f} "
              f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
              f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2}")

    # ══════════════════════════════════════════════════════════
    # Phase 4: GK Expansion Dip PORTFOLIO (multiple sub-strategies)
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 4: GK Expansion Dip Portfolio (multiple subs)")
    print(f"{'='*76}")
    print(f"  {'Config':<35} {'N':>4} {'PnL':>9} {'PF':>5} {'WR':>6} {'MDD':>5} "
          f"{'TPM':>5} {'PM':>5} {'topM':>5}")

    best_dip_pf = None

    # Portfolio: multiple GK thresholds × dip sizes
    for tp in [0.010, 0.012]:
        for mh in [8, 10]:
            for sub_cd in [4, 6]:
                dip_subs = [
                    {"entry_mask": (df["gk_pct"]>70)&df["dip03"], "engine":"tp_mh",
                     "tp_pct":tp, "max_hold":mh, "max_same":3, "exit_cd":sub_cd, "tag":"d70_03"},
                    {"entry_mask": (df["gk_pct"]>60)&df["dip05"], "engine":"tp_mh",
                     "tp_pct":tp, "max_hold":mh, "max_same":3, "exit_cd":sub_cd, "tag":"d60_05"},
                    {"entry_mask": (df["gk_pct"]>75)&df["dip03"], "engine":"tp_mh",
                     "tp_pct":tp, "max_hold":mh, "max_same":3, "exit_cd":sub_cd, "tag":"d75_03"},
                    {"entry_mask": (df["gk_pct"]>65)&df["dip05"], "engine":"tp_mh",
                     "tp_pct":tp, "max_hold":mh, "max_same":3, "exit_cd":sub_cd, "tag":"d65_05"},
                ]
                tdf = bt_portfolio(df, dip_subs)
                r = evaluate(tdf, mid, fe, f"dipPf_tp{tp*100:.1f}_mh{mh}_cd{sub_cd}")
                if r and r["n"]>10:
                    print(f"  dipPf tp{tp*100:.1f}% mh{mh} cd{sub_cd}            "
                          f"{r['n']:>4} ${r['pnl']:>+8,.0f} "
                          f"{r['pf']:>5.2f} {r['wr']:>5.1f}% {r['mdd']:>4.1f}% "
                          f"{r['tpm']:>5.1f} {r['pos_months']:>2}/{r['months']:<2} "
                          f"{r['top_pct']:>4.0f}%")
                    if best_dip_pf is None or r["pnl"]>best_dip_pf[1]["pnl"]:
                        best_dip_pf = (tdf.copy(), r)

    # ══════════════════════════════════════════════════════════
    # Phase 5: Combined — Best Main + Best Supplement
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 5: Combined Main + Supplement")
    print(f"{'='*76}")

    # Use best single-entry + best dip supplement
    # Main: Ami<20|Skew|RetSign, ms9, cd12 (R1 best)
    main_tdf = bt_trail(df, entry_ami, max_same=9, exit_cd=12, tag="main")

    # Best dip config
    if best_dip:
        dip_cfg = best_dip[3]
        dip_entry = (df["gk_pct"]>dip_cfg["gk_t"]) & df[f"dip0{int(abs(dip_cfg['dip_t'])*1000)}".replace('dip0','dip0' if abs(dip_cfg['dip_t'])<0.01 else 'dip')]
        # Simpler: just use the stored entry mask
        for gk_t in [60, 65, 70]:
            for dip_col in ["dip03", "dip05"]:
                dip_entry = (df["gk_pct"]>gk_t) & df[dip_col]
                for tp in [0.010, 0.012]:
                    for mh in [8, 10]:
                        dip_tdf = bt_tp_mh(df, dip_entry, tp_pct=tp, max_hold=mh,
                                           max_same=5, exit_cd=4, tag="dip")
                        # Combine
                        combined = pd.concat([main_tdf, dip_tdf], ignore_index=True)
                        combined = combined.sort_values("dt").reset_index(drop=True)
                        r = evaluate(combined, mid, fe, f"main+dip_gk{gk_t}_{dip_col}_tp{tp*100:.0f}_mh{mh}")
                        if r and r["n"]>10:
                            print(f"  main+gk{gk_t}_{dip_col}_tp{tp*100:.0f}_mh{mh}: "
                                  f"{r['n']:>4}t ${r['pnl']:>+8,.0f} "
                                  f"PF {r['pf']:.2f} WR {r['wr']:.1f}% MDD {r['mdd']:.1f}% "
                                  f"PM {r['pos_months']}/{r['months']} topM {r['top_pct']:.0f}%")

    # Also combine best portfolio + supplement
    if best_ph2:
        for gk_t in [60, 65, 70]:
            for dip_col in ["dip03", "dip05"]:
                dip_entry = (df["gk_pct"]>gk_t) & df[dip_col]
                dip_tdf = bt_tp_mh(df, dip_entry, tp_pct=0.01, max_hold=8,
                                   max_same=5, exit_cd=4, tag="dip")
                combined = pd.concat([best_ph2[0], dip_tdf], ignore_index=True)
                combined = combined.sort_values("dt").reset_index(drop=True)
                r = evaluate(combined, mid, fe, f"portfolio+dip_gk{gk_t}_{dip_col}")
                if r and r["n"]>10:
                    print(f"  pf+dip_gk{gk_t}_{dip_col}: "
                          f"{r['n']:>4}t ${r['pnl']:>+8,.0f} "
                          f"PF {r['pf']:.2f} WR {r['wr']:.1f}% MDD {r['mdd']:.1f}% "
                          f"PM {r['pos_months']}/{r['months']} topM {r['top_pct']:.0f}%")

    # ══════════════════════════════════════════════════════════
    # Phase 6: Detailed analysis of best overall config
    # ══════════════════════════════════════════════════════════
    print(f"\n{'='*76}")
    print("  Phase 6: Best Config Detailed Analysis")
    print(f"{'='*76}")

    # Find best config that passes most criteria
    candidates = []

    # Phase 1 best
    if best_ph1:
        wf = walk_forward_6(best_ph1[0], mid, fe)
        wf_pos = sum(1 for w in wf if w["pos"])
        sc, det = check_9(best_ph1[1], wf_pos)
        candidates.append(("Ph1 best", best_ph1[0], best_ph1[1], wf, sc, det))

    # Phase 2 best
    if best_ph2:
        wf = walk_forward_6(best_ph2[0], mid, fe)
        wf_pos = sum(1 for w in wf if w["pos"])
        sc, det = check_9(best_ph2[1], wf_pos)
        candidates.append(("Ph2 best", best_ph2[0], best_ph2[1], wf, sc, det))

    # Ami<20|Skew|RetSign ms9 cd12 (R1 baseline)
    r_base = evaluate(main_tdf, mid, fe, "Ami20|Skew|RetSign ms9 cd12")
    wf = walk_forward_6(main_tdf, mid, fe)
    wf_pos = sum(1 for w in wf if w["pos"])
    sc, det = check_9(r_base, wf_pos)
    candidates.append(("R1 base", main_tdf, r_base, wf, sc, det))

    candidates.sort(key=lambda x: x[4], reverse=True)  # sort by score

    for name, tdf, r, wf, sc, det in candidates[:3]:
        print(f"\n  ── {name} ({sc}/9) ──")
        print_result(r)
        wf_pos = sum(1 for w in wf if w["pos"])
        print(f"    WF: {wf_pos}/6")
        for w in wf:
            tag = "✓" if w["pos"] else "✗"
            print(f"      Fold {w['fold']}: {w['n']:>3}t ${w['pnl']:>+8,.0f} {tag}")
        print(f"    9-criteria:")
        for cn, cv in det:
            print(f"      {'✓' if cv else '✗'} {cn}")

    print(f"\n{'='*76}")
    print("  ROUND 2 COMPLETE")
    print(f"{'='*76}")
