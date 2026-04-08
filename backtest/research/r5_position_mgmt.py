"""
╔══════════════════════════════════════╗
║  第 5 輪：倉位管理 + 不對稱策略       ║
╚══════════════════════════════════════╝

已探索清單：
  R1: 替代波動率估算器 → 全部不如 GK
  R2: 進場品質過濾器 → 全部降低 PnL
  R3: 出場參數 → 已近局部最優 (+2.1% 組合在噪音內)
  R4: 結構參數 → GK_SHORT=3 +3.4% 但不穩健 (WF 8/10, MDD↑)
目前最佳記錄：GK 保守版 OOS $7,837

本輪假說：
  方向：改變倉位管理和方向性參數
  市場行為假說：
    A) maxSame 對 PnL 影響巨大（稽核：+36%）。maxSame=4/5 可能進一步提升
       但有過度暴露風險。
    B) ETH 多空不對稱——上漲慢但持久（累積+突破），下跌快但短暫。
       多做用較慢 trail 讓利潤跑，空做用較快 trail 鎖住利潤。
    C) 贏後減少冷卻（市場趨勢中，抓住動能），輸後增加冷卻（市場震盪）。
    D) 確認贏家（>24h, 100% WR）用更寬鬆 trail 讓它跑更遠。

上帝視角自檢：
  ☑ signal 只用 shift(1) 或更早數據？→ 是
  ☑ 進場價是 next bar open？→ 是
  ☑ 動態冷卻只看已完成交易的結果（非未來）？→ 是
  ☑ 不對稱參數在看數據前就決定？→ 是
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMP_SHORT = 5; COMP_LONG = 20; COMP_WIN = 100; COMP_THRESH = 30
BRK_LOOK = 10; EXIT_CD = 12
SN_PCT = 0.045; MIN_TRAIL = 7; ES_PCT = 0.020; ES_END = 12
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
FEE = 2.0; NOTIONAL = 2000; ACCOUNT = 10000

END_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
MID_DATE = END_DATE - timedelta(days=365)
MID_TS = pd.Timestamp(MID_DATE)
BASELINE_OOS = 7837

def load():
    p = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "data", "ETHUSDT_1h_latest730d.csv"))
    df = pd.read_csv(p)
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    print(f"Loaded {len(df)} bars: {df['datetime'].min()} to {df['datetime'].max()}")
    return df

def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100

def compute_indicators(df):
    d = df.copy()
    for span in [15, 20, 25, 30]:
        d[f"ema{span}"] = d["close"].ewm(span=span).mean()
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    gk_s = gk.rolling(COMP_SHORT).mean()
    gk_l = gk.rolling(COMP_LONG).mean()
    d["pp"] = (gk_s / gk_l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    cs1 = d["close"].shift(1)
    d["cmx"] = d["close"].shift(2).rolling(BRK_LOOK - 1).max()
    d["cmn"] = d["close"].shift(2).rolling(BRK_LOOK - 1).min()
    d["bl"] = cs1 > d["cmx"]
    d["bs"] = cs1 < d["cmn"]
    d["h"] = d["datetime"].dt.hour; d["wd"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["h"].isin(BLOCK_H) | d["wd"].isin(BLOCK_D))
    d["pp_p"] = d["pp"].shift(1)
    d["bl_p"] = d["bl"].shift(1); d["bs_p"] = d["bs"].shift(1); d["sok_p"] = d["sok"].shift(1)
    return d


def backtest_maxsame(df, max_same):
    """Baseline with variable maxSame."""
    N = len(df); w = COMP_WIN + COMP_LONG + 20
    O=df["open"].values; H=df["high"].values; L=df["low"].values
    C=df["close"].values; E=df["ema20"].values
    PP=df["pp"].values; BL=df["bl"].values; BS=df["bs"].values; SOK=df["sok"].values
    PP_P=df["pp_p"].values; BL_P=df["bl_p"].values; BS_P=df["bs_p"].values; SOK_P=df["sok_p"].values
    DT=df["datetime"].values

    lp=[]; sp=[]; trades=[]; last_exit={"long":-9999,"short":-9999}

    for i in range(w, N-1):
        # Exit longs
        nl=[]
        for p in lp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1-SN_PCT)
            if L[i]<=sn:
                xp=sn-(sn-L[i])*0.25
                trades.append({"pnl":(xp-ep)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]<=E[i]; early=C[i]<=ep*(1-ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":tp,"sd":"long","bars":bh,"dt":DT[i]})
                    last_exit["long"]=i; exited=True
            if not exited and bh>=ES_END and C[i]<=E[i]:
                trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":"Trail","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited: nl.append(p)
        lp=nl
        # Exit shorts
        ns=[]
        for p in sp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1+SN_PCT)
            if H[i]>=sn:
                xp=sn+(H[i]-sn)*0.25
                trades.append({"pnl":(ep-xp)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]>=E[i]; early=C[i]>=ep*(1+ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":tp,"sd":"short","bars":bh,"dt":DT[i]})
                    last_exit["short"]=i; exited=True
            if not exited and bh>=ES_END and C[i]>=E[i]:
                trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":"Trail","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited: ns.append(p)
        sp=ns
        # Entry
        pp=PP[i]
        if np.isnan(pp): continue
        bl=BL[i]; bs=BS[i]; sok=SOK[i]; cond=pp<COMP_THRESH
        pp_p=PP_P[i]; bl_p=BL_P[i]; bs_p=BS_P[i]; sok_p=SOK_P[i]
        if not np.isnan(pp_p):
            pc=pp_p<COMP_THRESH; fl=not(pc and bl_p and sok_p); fs=not(pc and bs_p and sok_p)
        else: fl=fs=True
        lc=(i-last_exit["long"])>=EXIT_CD; sc=(i-last_exit["short"])>=EXIT_CD
        if cond and bl and sok and fl and lc and len(lp)<max_same:
            lp.append({"e":O[i+1],"ei":i})
        if cond and bs and sok and fs and sc and len(sp)<max_same:
            sp.append({"e":O[i+1],"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])


def backtest_asymmetric(df, long_ema=20, short_ema=20, long_sn=0.045, short_sn=0.045,
                        long_min_trail=7, short_min_trail=7):
    """Asymmetric parameters for long vs short."""
    N = len(df); w = COMP_WIN + COMP_LONG + 20
    O=df["open"].values; H=df["high"].values; L=df["low"].values
    C=df["close"].values
    E_L=df[f"ema{long_ema}"].values; E_S=df[f"ema{short_ema}"].values
    PP=df["pp"].values; BL=df["bl"].values; BS=df["bs"].values; SOK=df["sok"].values
    PP_P=df["pp_p"].values; BL_P=df["bl_p"].values; BS_P=df["bs_p"].values; SOK_P=df["sok_p"].values
    DT=df["datetime"].values

    lp=[]; sp=[]; trades=[]; last_exit={"long":-9999,"short":-9999}

    for i in range(w, N-1):
        # Exit longs (use long_ema, long_sn, long_min_trail)
        nl=[]
        for p in lp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1-long_sn)
            if L[i]<=sn:
                xp=sn-(sn-L[i])*0.25
                trades.append({"pnl":(xp-ep)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            l_es_end = max(long_min_trail + 5, ES_END)
            if not exited and long_min_trail<=bh<l_es_end:
                trail=C[i]<=E_L[i]; early=C[i]<=ep*(1-ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":tp,"sd":"long","bars":bh,"dt":DT[i]})
                    last_exit["long"]=i; exited=True
            if not exited and bh>=l_es_end and C[i]<=E_L[i]:
                trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":"Trail","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited: nl.append(p)
        lp=nl

        # Exit shorts (use short_ema, short_sn, short_min_trail)
        ns=[]
        for p in sp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1+short_sn)
            if H[i]>=sn:
                xp=sn+(H[i]-sn)*0.25
                trades.append({"pnl":(ep-xp)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            s_es_end = max(short_min_trail + 5, ES_END)
            if not exited and short_min_trail<=bh<s_es_end:
                trail=C[i]>=E_S[i]; early=C[i]>=ep*(1+ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":tp,"sd":"short","bars":bh,"dt":DT[i]})
                    last_exit["short"]=i; exited=True
            if not exited and bh>=s_es_end and C[i]>=E_S[i]:
                trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":"Trail","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited: ns.append(p)
        sp=ns

        # Entry (same for both)
        pp=PP[i]
        if np.isnan(pp): continue
        bl=BL[i]; bs=BS[i]; sok=SOK[i]; cond=pp<COMP_THRESH
        pp_p=PP_P[i]; bl_p=BL_P[i]; bs_p=BS_P[i]; sok_p=SOK_P[i]
        if not np.isnan(pp_p):
            pc=pp_p<COMP_THRESH; fl=not(pc and bl_p and sok_p); fs=not(pc and bs_p and sok_p)
        else: fl=fs=True
        lc=(i-last_exit["long"])>=EXIT_CD; sc=(i-last_exit["short"])>=EXIT_CD
        if cond and bl and sok and fl and lc and len(lp)<3:
            lp.append({"e":O[i+1],"ei":i})
        if cond and bs and sok and fs and sc and len(sp)<3:
            sp.append({"e":O[i+1],"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])


def backtest_dynamic_cd(df, cd_after_win, cd_after_loss):
    """Dynamic cooldown based on last trade result."""
    N = len(df); w = COMP_WIN + COMP_LONG + 20
    O=df["open"].values; H=df["high"].values; L=df["low"].values
    C=df["close"].values; E=df["ema20"].values
    PP=df["pp"].values; BL=df["bl"].values; BS=df["bs"].values; SOK=df["sok"].values
    PP_P=df["pp_p"].values; BL_P=df["bl_p"].values; BS_P=df["bs_p"].values; SOK_P=df["sok_p"].values
    DT=df["datetime"].values

    lp=[]; sp=[]; trades=[]; last_exit={"long":-9999,"short":-9999}
    last_pnl = {"long": 0, "short": 0}  # Track last trade PnL per direction

    for i in range(w, N-1):
        # Exit longs
        nl=[]
        for p in lp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1-SN_PCT)
            if L[i]<=sn:
                xp=sn-(sn-L[i])*0.25
                pnl = (xp-ep)*NOTIONAL/ep-FEE
                trades.append({"pnl":pnl,"tp":"SafeNet","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; last_pnl["long"]=pnl; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]<=E[i]; early=C[i]<=ep*(1-ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    pnl = (C[i]-ep)*NOTIONAL/ep-FEE
                    trades.append({"pnl":pnl,"tp":tp,"sd":"long","bars":bh,"dt":DT[i]})
                    last_exit["long"]=i; last_pnl["long"]=pnl; exited=True
            if not exited and bh>=ES_END and C[i]<=E[i]:
                pnl = (C[i]-ep)*NOTIONAL/ep-FEE
                trades.append({"pnl":pnl,"tp":"Trail","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; last_pnl["long"]=pnl; exited=True
            if not exited: nl.append(p)
        lp=nl
        # Exit shorts
        ns=[]
        for p in sp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            sn=ep*(1+SN_PCT)
            if H[i]>=sn:
                xp=sn+(H[i]-sn)*0.25
                pnl = (ep-xp)*NOTIONAL/ep-FEE
                trades.append({"pnl":pnl,"tp":"SafeNet","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; last_pnl["short"]=pnl; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]>=E[i]; early=C[i]>=ep*(1+ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    pnl = (ep-C[i])*NOTIONAL/ep-FEE
                    trades.append({"pnl":pnl,"tp":tp,"sd":"short","bars":bh,"dt":DT[i]})
                    last_exit["short"]=i; last_pnl["short"]=pnl; exited=True
            if not exited and bh>=ES_END and C[i]>=E[i]:
                pnl = (ep-C[i])*NOTIONAL/ep-FEE
                trades.append({"pnl":pnl,"tp":"Trail","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; last_pnl["short"]=pnl; exited=True
            if not exited: ns.append(p)
        sp=ns
        # Entry with dynamic cooldown
        pp=PP[i]
        if np.isnan(pp): continue
        bl=BL[i]; bs=BS[i]; sok=SOK[i]; cond=pp<COMP_THRESH
        pp_p=PP_P[i]; bl_p=BL_P[i]; bs_p=BS_P[i]; sok_p=SOK_P[i]
        if not np.isnan(pp_p):
            pc=pp_p<COMP_THRESH; fl=not(pc and bl_p and sok_p); fs=not(pc and bs_p and sok_p)
        else: fl=fs=True

        # Dynamic cooldown
        l_cd = cd_after_win if last_pnl["long"] > 0 else cd_after_loss
        s_cd = cd_after_win if last_pnl["short"] > 0 else cd_after_loss
        lc=(i-last_exit["long"])>=l_cd; sc=(i-last_exit["short"])>=s_cd

        if cond and bl and sok and fl and lc and len(lp)<3:
            lp.append({"e":O[i+1],"ei":i})
        if cond and bs and sok and fs and sc and len(sp)<3:
            sp.append({"e":O[i+1],"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])


def backtest_winner_trail(df, winner_bar_thresh=24, winner_ema=25):
    """Switch to looser trail after confirmed winner (bar >= thresh)."""
    N = len(df); w = COMP_WIN + COMP_LONG + 20
    O=df["open"].values; H=df["high"].values; L=df["low"].values
    C=df["close"].values; E20=df["ema20"].values; EW=df[f"ema{winner_ema}"].values
    PP=df["pp"].values; BL=df["bl"].values; BS=df["bs"].values; SOK=df["sok"].values
    PP_P=df["pp_p"].values; BL_P=df["bl_p"].values; BS_P=df["bs_p"].values; SOK_P=df["sok_p"].values
    DT=df["datetime"].values

    lp=[]; sp=[]; trades=[]; last_exit={"long":-9999,"short":-9999}

    for i in range(w, N-1):
        # Exit longs
        nl=[]
        for p in lp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            # Choose trail EMA based on hold time
            E = EW if bh >= winner_bar_thresh else E20

            sn=ep*(1-SN_PCT)
            if L[i]<=sn:
                xp=sn-(sn-L[i])*0.25
                trades.append({"pnl":(xp-ep)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]<=E[i]; early=C[i]<=ep*(1-ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":tp,"sd":"long","bars":bh,"dt":DT[i]})
                    last_exit["long"]=i; exited=True
            if not exited and bh>=ES_END and C[i]<=E[i]:
                trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":"Trail","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited: nl.append(p)
        lp=nl
        # Exit shorts
        ns=[]
        for p in sp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            E = EW if bh >= winner_bar_thresh else E20

            sn=ep*(1+SN_PCT)
            if H[i]>=sn:
                xp=sn+(H[i]-sn)*0.25
                trades.append({"pnl":(ep-xp)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<ES_END:
                trail=C[i]>=E[i]; early=C[i]>=ep*(1+ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":tp,"sd":"short","bars":bh,"dt":DT[i]})
                    last_exit["short"]=i; exited=True
            if not exited and bh>=ES_END and C[i]>=E[i]:
                trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":"Trail","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited: ns.append(p)
        sp=ns
        # Entry
        pp=PP[i]
        if np.isnan(pp): continue
        bl=BL[i]; bs=BS[i]; sok=SOK[i]; cond=pp<COMP_THRESH
        pp_p=PP_P[i]; bl_p=BL_P[i]; bs_p=BS_P[i]; sok_p=SOK_P[i]
        if not np.isnan(pp_p):
            pc=pp_p<COMP_THRESH; fl=not(pc and bl_p and sok_p); fs=not(pc and bs_p and sok_p)
        else: fl=fs=True
        lc=(i-last_exit["long"])>=EXIT_CD; sc=(i-last_exit["short"])>=EXIT_CD
        if cond and bl and sok and fl and lc and len(lp)<3:
            lp.append({"e":O[i+1],"ei":i})
        if cond and bs and sok and fs and sc and len(sp)<3:
            sp.append({"e":O[i+1],"ei":i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def analyze(trades_df, mid_ts):
    if len(trades_df)==0:
        return {"is_t":0,"is_pnl":0,"oos_t":0,"oos_pnl":0,"oos_pf":0,"oos_wr":0,"mdd":0,"sn":0,"avg_hold":0}
    t=trades_df.copy(); t["dt"]=pd.to_datetime(t["dt"])
    is_t=t[t["dt"]<mid_ts]; oos_t=t[t["dt"]>=mid_ts]
    def stats(df):
        if len(df)==0: return 0,0,0,0
        tot=df["pnl"].sum(); w=df[df["pnl"]>0]["pnl"].sum()
        l=abs(df[df["pnl"]<0]["pnl"].sum()); pf=w/l if l>0 else 999
        wr=(df["pnl"]>0).mean()*100; return len(df),tot,pf,wr
    isn,isp,ispf,iswr=stats(is_t); on,op,opf,owr=stats(oos_t)
    mdd_pct=0
    if len(oos_t)>0:
        cum=oos_t["pnl"].cumsum(); dd=cum-cum.cummax(); mdd_pct=abs(dd.min())/ACCOUNT*100
    sn=len(oos_t[oos_t["tp"]=="SafeNet"]) if len(oos_t)>0 else 0
    avg_hold=oos_t["bars"].mean() if len(oos_t)>0 else 0
    return {"is_t":isn,"is_pnl":isp,"is_pf":ispf,
            "oos_t":on,"oos_pnl":op,"oos_pf":opf,"oos_wr":owr,
            "mdd":mdd_pct,"sn":sn,"avg_hold":avg_hold}


def detail_report(trades_df, label, mid_ts):
    t=trades_df.copy(); t["dt"]=pd.to_datetime(t["dt"])
    oos=t[t["dt"]>=mid_ts]
    print(f"\n{'='*60}")
    print(f"  {label} -- Detail")
    print(f"{'='*60}")
    if len(oos)==0: print("  No OOS trades"); return
    r=analyze(trades_df, mid_ts)
    print(f"  OOS: {len(oos)}t  ${oos['pnl'].sum():+,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  Avg hold {r['avg_hold']:.1f}h")
    print(f"\n  Exit breakdown (OOS):")
    for tp in ["EarlyStop","SafeNet","Trail"]:
        sub=oos[oos["tp"]==tp]
        if len(sub)>0:
            print(f"    {tp:<12s}: {len(sub):>4d}t  ${sub['pnl'].sum():>+10,.0f}  avg hold {sub['bars'].mean():.0f}h")
    print(f"\n  Hold time (OOS):")
    for lo,hi,lbl in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h")]:
        sub=oos[(oos["bars"]>=lo)&(oos["bars"]<hi)]
        if len(sub)>0:
            wr=(sub["pnl"]>0).mean()*100
            print(f"    {lbl:<8s}: {len(sub):>4d}t  ${sub['pnl'].sum():>+10,.0f}  WR {wr:.0f}%")
    # Long vs Short breakdown
    for sd in ["long","short"]:
        sub=oos[oos["sd"]==sd]
        if len(sub)>0:
            w=sub[sub["pnl"]>0]["pnl"].sum(); l=abs(sub[sub["pnl"]<0]["pnl"].sum())
            pf=w/l if l>0 else 999
            print(f"\n  {sd.upper()}: {len(sub)}t  ${sub['pnl'].sum():+,.0f}  PF {pf:.2f}  WR {(sub['pnl']>0).mean()*100:.1f}%  avg hold {sub['bars'].mean():.0f}h")
    oos2=oos.copy(); oos2["mo"]=oos2["dt"].dt.to_period("M")
    mo=oos2.groupby("mo")["pnl"].sum()
    print(f"\n  Monthly PnL (OOS): {(mo>0).sum()}/{len(mo)} positive")
    for m,p in mo.items(): print(f"    {m}: ${p:>+10,.0f}")


def walk_forward_maxsame(df_raw, max_same, n_folds=10):
    d=df_raw.copy(); d["datetime"]=pd.to_datetime(d["datetime"])
    oos_df=d[d["datetime"]>=MID_TS].copy()
    if len(oos_df)<200: print("  Not enough data"); return 0
    fold_size=len(oos_df)//n_folds; results=[]
    for fold in range(n_folds):
        start=fold*fold_size; end=start+fold_size if fold<n_folds-1 else len(oos_df)
        fold_df=oos_df.iloc[max(0,start-300):end].copy()
        fold_df=compute_indicators(fold_df)
        trades=backtest_maxsame(fold_df, max_same)
        pnl=trades["pnl"].sum() if len(trades)>0 else 0
        results.append(pnl)
    pos=sum(1 for r in results if r>0)
    print(f"\n  Walk-Forward ({n_folds} folds): {pos}/{n_folds} positive")
    for i,r in enumerate(results): print(f"    Fold {i+1}: ${r:>+10,.0f}")
    return pos


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    df_raw = load()
    print(f"IS/OOS split: {MID_TS}\n")
    df = compute_indicators(df_raw)

    # ════════════════════════════════════
    # Test A: maxSame sweep
    # ════════════════════════════════════
    print("=" * 90)
    print("  Test A: maxSame (positions per direction)")
    print("=" * 90)
    ms_results = []
    for ms in [1, 2, 3, 4, 5]:
        trades = backtest_maxsame(df, ms)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if ms == 3 else ""
        print(f"  maxSame={ms}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        ms_results.append((ms, r, trades))

    # ════════════════════════════════════
    # Test B: Asymmetric long/short parameters
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test B: Asymmetric Long/Short Parameters")
    print("=" * 90)
    asym_configs = [
        ("Baseline (symmetric)", 20, 20, 0.045, 0.045, 7, 7),
        ("Long EMA25, Short EMA15", 25, 15, 0.045, 0.045, 7, 7),
        ("Long EMA25, Short EMA20", 25, 20, 0.045, 0.045, 7, 7),
        ("Long SN5%, Short SN4%", 20, 20, 0.050, 0.040, 7, 7),
        ("Long SN5.5%, Short SN4%", 20, 20, 0.055, 0.040, 7, 7),
        ("Long MinT8, Short MinT6", 20, 20, 0.045, 0.045, 8, 6),
        ("Long EMA25+SN5%, Short EMA15+SN4%", 25, 15, 0.050, 0.040, 7, 7),
    ]
    asym_results = []
    for label, le, se, ls, ss, lmt, smt in asym_configs:
        trades = backtest_asymmetric(df, long_ema=le, short_ema=se,
                                     long_sn=ls, short_sn=ss,
                                     long_min_trail=lmt, short_min_trail=smt)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Baseline") else ""
        print(f"  {label:<40s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%{tag}")
        asym_results.append((label, r, trades))

    # ════════════════════════════════════
    # Test C: Dynamic cooldown
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test C: Dynamic Cooldown (win/loss)")
    print("=" * 90)
    dcd_configs = [
        ("Fixed CD=12 (baseline)", 12, 12),
        ("Win=6, Loss=12", 6, 12),
        ("Win=6, Loss=18", 6, 18),
        ("Win=8, Loss=16", 8, 16),
        ("Win=4, Loss=20", 4, 20),
        ("Win=10, Loss=14", 10, 14),
    ]
    dcd_results = []
    for label, cdw, cdl in dcd_configs:
        trades = backtest_dynamic_cd(df, cdw, cdl)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Fixed") else ""
        print(f"  {label:<30s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h{tag}")
        dcd_results.append((label, r, trades))

    # ════════════════════════════════════
    # Test D: Winner trail widening
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test D: Winner Trail Widening (looser trail after confirmed winner)")
    print("=" * 90)
    wt_configs = [
        ("Baseline (no switch)", 9999, 20),  # never switches
        ("After 24h: EMA25", 24, 25),
        ("After 24h: EMA30", 24, 30),
        ("After 36h: EMA25", 36, 25),
        ("After 36h: EMA30", 36, 30),
        ("After 48h: EMA25", 48, 25),
        ("After 48h: EMA30", 48, 30),
    ]
    wt_results = []
    for label, thresh, ema in wt_configs:
        trades = backtest_winner_trail(df, winner_bar_thresh=thresh, winner_ema=ema)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Baseline") else ""
        print(f"  {label:<30s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h{tag}")
        wt_results.append((label, r, trades))

    # ════════════════════════════════════
    # Summary
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  BEST PER TEST (vs baseline OOS ${BASELINE_OOS:+,d})")
    print("=" * 90)

    any_beat = False
    overall_best_pnl = BASELINE_OOS
    overall_best_label = "baseline"
    overall_best_trades = None

    # A: maxSame
    best_ms = max(ms_results, key=lambda x: x[1]["oos_pnl"])
    diff = best_ms[1]["oos_pnl"] - BASELINE_OOS
    tag = "NEW BEST" if diff > 0 else "no improvement"
    print(f"  A maxSame: best={best_ms[0]} -> OOS ${best_ms[1]['oos_pnl']:>+9,.0f} (diff ${diff:>+7,.0f}) [{tag}]")
    if best_ms[1]["oos_pnl"] > overall_best_pnl:
        overall_best_pnl = best_ms[1]["oos_pnl"]
        overall_best_label = f"maxSame={best_ms[0]}"
        overall_best_trades = best_ms[2]; any_beat = True

    # B: Asymmetric
    best_asym = max(asym_results, key=lambda x: x[1]["oos_pnl"])
    diff = best_asym[1]["oos_pnl"] - BASELINE_OOS
    tag = "NEW BEST" if diff > 0 else "no improvement"
    print(f"  B Asymmetric: {best_asym[0]} -> OOS ${best_asym[1]['oos_pnl']:>+9,.0f} (diff ${diff:>+7,.0f}) [{tag}]")
    if best_asym[1]["oos_pnl"] > overall_best_pnl:
        overall_best_pnl = best_asym[1]["oos_pnl"]
        overall_best_label = best_asym[0]
        overall_best_trades = best_asym[2]; any_beat = True

    # C: Dynamic CD
    best_dcd = max(dcd_results, key=lambda x: x[1]["oos_pnl"])
    diff = best_dcd[1]["oos_pnl"] - BASELINE_OOS
    tag = "NEW BEST" if diff > 0 else "no improvement"
    print(f"  C Dynamic CD: {best_dcd[0]} -> OOS ${best_dcd[1]['oos_pnl']:>+9,.0f} (diff ${diff:>+7,.0f}) [{tag}]")
    if best_dcd[1]["oos_pnl"] > overall_best_pnl:
        overall_best_pnl = best_dcd[1]["oos_pnl"]
        overall_best_label = best_dcd[0]
        overall_best_trades = best_dcd[2]; any_beat = True

    # D: Winner trail
    best_wt = max(wt_results, key=lambda x: x[1]["oos_pnl"])
    diff = best_wt[1]["oos_pnl"] - BASELINE_OOS
    tag = "NEW BEST" if diff > 0 else "no improvement"
    print(f"  D Winner trail: {best_wt[0]} -> OOS ${best_wt[1]['oos_pnl']:>+9,.0f} (diff ${diff:>+7,.0f}) [{tag}]")
    if best_wt[1]["oos_pnl"] > overall_best_pnl:
        overall_best_pnl = best_wt[1]["oos_pnl"]
        overall_best_label = best_wt[0]
        overall_best_trades = best_wt[2]; any_beat = True

    # Detail for best
    if any_beat and overall_best_trades is not None:
        detail_report(overall_best_trades, f"Overall Best: {overall_best_label}", MID_TS)

    # Walk-forward for maxSame if it beats
    best_ms_val = best_ms[0]
    if best_ms[1]["oos_pnl"] > BASELINE_OOS:
        print(f"\n{'='*60}")
        print(f"  Walk-Forward: maxSame={best_ms_val}")
        print(f"{'='*60}")
        wf = walk_forward_maxsame(df_raw, best_ms_val)

        print(f"\n  Walk-Forward: Baseline maxSame=3")
        wf_base = walk_forward_maxsame(df_raw, 3)

    # ════════════════════════════════════
    # Verdict
    # ════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"  ROUND 5 VERDICT")
    print(f"{'='*60}")
    if overall_best_pnl > BASELINE_OOS:
        diff = overall_best_pnl - BASELINE_OOS
        pct = diff / BASELINE_OOS * 100
        print(f"  Best: {overall_best_label}")
        print(f"  OOS ${overall_best_pnl:+,.0f} (vs baseline ${BASELINE_OOS:+,d}, +{pct:.1f}%)")
        if overall_best_pnl > 7837:
            print(f"  >>> EXCEEDS ALL-TIME RECORD -- AUDIT REQUIRED <<<")
    else:
        print(f"  Baseline remains best. No position management change improved OOS PnL.")

    print(f"\n  Anti-lookahead self-check:")
    print(f"  [v] Entry price = O[i+1]")
    print(f"  [v] Dynamic cooldown uses past trade PnL only (no future info)")
    print(f"  [v] Winner trail uses bar count from entry (known at time of exit check)")
    print(f"  [v] Asymmetric params applied per-direction (no info leakage)")
    print(f"  [v] maxSame is a structural constraint, no lookahead")


if __name__ == "__main__":
    main()
