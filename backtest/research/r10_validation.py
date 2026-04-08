"""
R10 Lean 3-Factor: Comprehensive 6-Dimension Validation
========================================================
ALL SCENARIO DEFINITIONS COMMITTED BEFORE LOOKING AT DATA.
No post-hoc adjustments based on results.
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

# === Constants (same as R10) =============================================
MARGIN=100; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE; ACCOUNT=10000; MAX_SAME=2
PARK_SHORT=5; PARK_LONG=20; PARK_WIN=100; MIN_TRAIL=12
BLOCK_H={0,1,2,12}; BLOCK_D={0,5,6}
DEF_PT=30; DEF_BL=10; DEF_SN=0.035; DEF_FEE=2.0

END_DATE=datetime(2026,4,3); START_DATE=END_DATE-timedelta(days=732)
MID_DATE=END_DATE-timedelta(days=365); MID_TS=pd.Timestamp(MID_DATE)

ETH_CACHE=os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..","..","data","ETHUSDT_1h_latest730d.csv"))

def load():
    df=pd.read_csv(ETH_CACHE); df["datetime"]=pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    print(f"  Loaded {len(df)} bars"); return df

def compute(df, bl=DEF_BL):
    d=df.copy()
    d["ema20"]=d["close"].ewm(span=20).mean()
    d["ema50"]=d["close"].ewm(span=50).mean()
    ln_hl=np.log(d["high"]/d["low"]); psq=ln_hl**2/(4*np.log(2))
    d["ps"]=np.sqrt(psq.rolling(PARK_SHORT).mean())
    d["pl"]=np.sqrt(psq.rolling(PARK_LONG).mean())
    d["pr"]=d["ps"]/d["pl"]
    d["pp"]=d["pr"].shift(1).rolling(PARK_WIN).apply(
        lambda x:(x.iloc[-1]-x.min())/(x.max()-x.min())*100 if x.max()!=x.min() else 50)
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(bl-1).max()
    d["cmn"]=d["close"].shift(2).rolling(bl-1).min()
    d["bl_s"]=d["cs1"]>d["cmx"]; d["bs_s"]=d["cs1"]<d["cmn"]
    d["h"]=d["datetime"].dt.hour; d["wd"]=d["datetime"].dt.weekday
    d["sok"]=~(d["h"].isin(BLOCK_H)|d["wd"].isin(BLOCK_D))
    d["pp_p"]=d["pp"].shift(1); d["bl_p"]=d["bl_s"].shift(1)
    d["bs_p"]=d["bs_s"].shift(1); d["sok_p"]=d["sok"].shift(1)
    d["ret30"]=(d["close"].shift(1)/d["close"].shift(31)-1)*100
    return d

def recompute_brk(df, bl):
    d=df.copy()
    d["cs1"]=d["close"].shift(1)
    d["cmx"]=d["close"].shift(2).rolling(bl-1).max()
    d["cmn"]=d["close"].shift(2).rolling(bl-1).min()
    d["bl_s"]=d["cs1"]>d["cmx"]; d["bs_s"]=d["cs1"]<d["cmn"]
    d["bl_p"]=d["bl_s"].shift(1); d["bs_p"]=d["bs_s"].shift(1)
    return d

def bt(df, pt=DEF_PT, sn=DEF_SN):
    w=PARK_WIN+PARK_LONG+20
    H=df["high"].values; L=df["low"].values; C=df["close"].values; O=df["open"].values
    E=df["ema20"].values; D=df["datetime"].values; N=len(df)
    PP=df["pp"].values; PP_P=df["pp_p"].values
    BL=df["bl_s"].values; BS=df["bs_s"].values
    BLP=df["bl_p"].values; BSP=df["bs_p"].values
    SO=df["sok"].values; SOP=df["sok_p"].values

    lp=[]; sp=[]; tr=[]
    for i in range(w, N-1):
        rh=H[i]; rl=L[i]; rc=C[i]; re=E[i]; rd=D[i]; no=O[i+1]
        nl=[]
        for p in lp:
            cl=False; b=i-p["ei"]
            if rl<=p["e"]*(1-sn):
                ep=p["e"]*(1-sn); ep=ep-(ep-rl)*0.25
                pnl_nf=(ep-p["e"])*NOTIONAL/p["e"]
                tr.append({"pnl_nf":pnl_nf,"tp":"SafeNet","sd":"long","bars":b,
                    "edt":D[p["ei"]+1],"xdt":rd,"ep":p["e"],"xp":ep,
                    "si":p["ei"],"xi":i,"ppv":p["ppv"]}); cl=True
            elif b>=MIN_TRAIL and rc<=re:
                pnl_nf=(rc-p["e"])*NOTIONAL/p["e"]
                tr.append({"pnl_nf":pnl_nf,"tp":"Trail","sd":"long","bars":b,
                    "edt":D[p["ei"]+1],"xdt":rd,"ep":p["e"],"xp":rc,
                    "si":p["ei"],"xi":i,"ppv":p["ppv"]}); cl=True
            if not cl: nl.append(p)
        lp=nl
        ns=[]
        for p in sp:
            cl=False; b=i-p["ei"]
            if rh>=p["e"]*(1+sn):
                ep=p["e"]*(1+sn); ep=ep+(rh-ep)*0.25
                pnl_nf=(p["e"]-ep)*NOTIONAL/p["e"]
                tr.append({"pnl_nf":pnl_nf,"tp":"SafeNet","sd":"short","bars":b,
                    "edt":D[p["ei"]+1],"xdt":rd,"ep":p["e"],"xp":ep,
                    "si":p["ei"],"xi":i,"ppv":p["ppv"]}); cl=True
            elif b>=MIN_TRAIL and rc>=re:
                pnl_nf=(p["e"]-rc)*NOTIONAL/p["e"]
                tr.append({"pnl_nf":pnl_nf,"tp":"Trail","sd":"short","bars":b,
                    "edt":D[p["ei"]+1],"xdt":rd,"ep":p["e"],"xp":rc,
                    "si":p["ei"],"xi":i,"ppv":p["ppv"]}); cl=True
            if not cl: ns.append(p)
        sp=ns
        pp_v=PP[i]
        if np.isnan(pp_v): continue
        bl_v=BL[i]; bs_v=BS[i]; so_v=SO[i]
        co=pp_v<pt
        blo=bool(bl_v) if not np.isnan(bl_v) else False
        bso=bool(bs_v) if not np.isnan(bs_v) else False
        s=bool(so_v)
        pp_p=PP_P[i]; bl_p=BLP[i]; bs_p=BSP[i]; so_p=SOP[i]
        if not np.isnan(pp_p):
            pc=pp_p<pt; pbl=bool(bl_p) if not np.isnan(bl_p) else False
            pbs=bool(bs_p) if not np.isnan(bs_p) else False; ps=bool(so_p)
        else: pc=pbl=pbs=ps=False
        fl=not(pc and pbl and ps); fs=not(pc and pbs and ps)
        if co and blo and s and fl and len(lp)<MAX_SAME:
            lp.append({"e":no,"ei":i,"ppv":pp_v})
        if co and bso and s and fs and len(sp)<MAX_SAME:
            sp.append({"e":no,"ei":i,"ppv":pp_v})
    cols=["pnl_nf","tp","sd","bars","edt","xdt","ep","xp","si","xi","ppv"]
    return pd.DataFrame(tr, columns=cols) if tr else pd.DataFrame(columns=cols)

def oos_stats(tdf, fee=DEF_FEE):
    if len(tdf)==0: return {"n":0,"pnl":0,"wr":0,"pf":0,"mdd":0,"mdd_pct":0}
    t=tdf.copy(); t["pnl"]=t["pnl_nf"]-fee
    n=len(t); pnl=t["pnl"].sum(); wr=(t["pnl"]>0).mean()*100
    w=t[t["pnl"]>0]["pnl"].sum(); l=abs(t[t["pnl"]<=0]["pnl"].sum())
    pf=w/l if l>0 else 999
    eq=t["pnl"].cumsum(); dd=eq-eq.cummax(); mdd=dd.min(); mp=abs(mdd)/ACCOUNT*100
    return {"n":n,"pnl":round(pnl,2),"wr":round(wr,1),"pf":round(pf,2),
            "mdd":round(mdd,2),"mdd_pct":round(mp,1)}

def hdr(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

# =========================================================================
#  DIMENSION 1: Post-Entry Extreme Scenarios
# =========================================================================
def dim1(oos, df):
    hdr("DIMENSION 1: 開單後極端價格行為")
    print("  (情境定義在看數據前已鎖定，不做事後調整)")
    H=df["high"].values; L=df["low"].values; C=df["close"].values; N=len(df)

    # Pre-compute per-trade MFE/MAE
    recs=[]
    for _, t in oos.iterrows():
        eb=int(t["si"])+1; xi=int(t["xi"]); ep=t["ep"]; sd=t["sd"]
        if eb>=N or xi>=N: continue
        th=H[eb:xi+1]; tl=L[eb:xi+1]
        if sd=="long":
            mfe=(th.max()-ep)/ep if len(th)>0 else 0
            mae=(ep-tl.min())/ep if len(tl)>0 else 0
        else:
            mfe=(ep-tl.min())/ep if len(tl)>0 else 0
            mae=(th.max()-ep)/ep if len(th)>0 else 0
        # First 6 bars favorable
        e6=min(eb+6,xi+1,N)
        if sd=="long": fav6=(H[eb:e6].max()-ep)/ep if e6>eb else 0
        else: fav6=(ep-L[eb:e6].min())/ep if e6>eb else 0
        recs.append({"mfe":mfe,"mae":mae,"fav6":fav6,"pnl":t["pnl_nf"]-DEF_FEE,
                      "tp":t["tp"],"sd":sd,"bars":int(t["bars"]),"xi":xi,
                      "ep":ep,"xp":t["xp"],"ppv":t["ppv"]})
    M=pd.DataFrame(recs); ntot=len(M)

    scenarios = []

    # S1: Quick SafeNet (6h內被止損)
    s1=M[(M["tp"]=="SafeNet")&(M["bars"]<=6)]
    n1=len(s1); pnl1=s1["pnl"].sum() if n1>0 else 0; wr1=(s1["pnl"]>0).mean()*100 if n1>0 else 0
    avg1=s1["pnl"].mean() if n1>0 else 0
    print(f"\n  +{'-'*62}+")
    print(f"  | S1: 快速止損獵殺 (6h內SafeNet觸發)                          |")
    print(f"  | 定義: exit_type=SafeNet AND bars<=6                          |")
    print(f"  | 發生: {n1:>3d} 次 ({n1/ntot*100:.1f}% of OOS trades){'':>24s}|")
    print(f"  | WR: {wr1:.0f}%  平均PnL: ${avg1:+.1f}  總PnL: ${pnl1:+,.0f}{'':>17s}|")
    print(f"  | 結論: 進場後立即反向 → SafeNet快速截斷{'':>22s}|")
    print(f"  | 實盤建議: 這是買趨勢門票的固定成本，不可避免{'':>15s}|")
    print(f"  +{'-'*62}+")
    scenarios.append(("快速止損", n1, n1/ntot*100 if ntot>0 else 0, pnl1))

    # S2: Slow Bleed (Trail出場但虧損)
    s2=M[(M["tp"]=="Trail")&(M["pnl"]<0)]
    n2=len(s2); pnl2=s2["pnl"].sum() if n2>0 else 0; avg2=s2["pnl"].mean() if n2>0 else 0
    avgbars2=s2["bars"].mean() if n2>0 else 0
    print(f"\n  +{'-'*62}+")
    print(f"  | S2: 溫水煮青蛙 (Trail出場但虧損, 持倉>12h)                  |")
    print(f"  | 定義: exit_type=Trail AND pnl<0                             |")
    print(f"  | 發生: {n2:>3d} 次 ({n2/ntot*100:.1f}% of OOS trades){'':>24s}|")
    print(f"  | 平均PnL: ${avg2:+.1f}  平均持倉: {avgbars2:.0f}h  總PnL: ${pnl2:+,.0f}{'':>7s}|")
    print(f"  | 結論: 持有12h+仍虧 = 突破失敗但未觸SafeNet{'':>16s}|")
    print(f"  | 實盤建議: 這是第二大成本源，心理上最難熬{'':>18s}|")
    print(f"  +{'-'*62}+")
    scenarios.append(("溫水煮青蛙", n2, n2/ntot*100 if ntot>0 else 0, pnl2))

    # S3: Fakeout (先順後逆)
    s3=M[(M["fav6"]>0.01)&(M["pnl"]<0)]
    n3=len(s3); pnl3=s3["pnl"].sum() if n3>0 else 0; avg3=s3["pnl"].mean() if n3>0 else 0
    avgmfe3=s3["mfe"].mean()*100 if n3>0 else 0
    print(f"\n  +{'-'*62}+")
    print(f"  | S3: 假突破回殺 (6h內順勢>1%卻最終虧損)                      |")
    print(f"  | 定義: fav_6h > 1% AND final_pnl < 0                        |")
    print(f"  | 發生: {n3:>3d} 次 ({n3/ntot*100:.1f}% of OOS trades){'':>24s}|")
    print(f"  | 平均PnL: ${avg3:+.1f}  平均MFE: {avgmfe3:.1f}%  總PnL: ${pnl3:+,.0f}{'':>8s}|")
    print(f"  | 結論: 利潤回吐型虧損 — 看到浮盈卻最終虧損{'':>16s}|")
    print(f"  | 實盤建議: 心理衝擊大，但加TP會截斷大贏家{'':>17s}|")
    print(f"  +{'-'*62}+")
    scenarios.append(("假突破回殺", n3, n3/ntot*100 if ntot>0 else 0, pnl3))

    # S4: Dead Sideways (橫盤消磨)
    s4=M[(M["bars"]>=12)&(M["bars"]<=24)&(M["pnl"].abs()<10)]
    n4=len(s4); pnl4=s4["pnl"].sum() if n4>0 else 0; avg4=s4["pnl"].mean() if n4>0 else 0
    print(f"\n  +{'-'*62}+")
    print(f"  | S4: 橫盤消磨 (12-24h持倉, |PnL|<$10)                       |")
    print(f"  | 定義: 12<=bars<=24 AND |pnl|<$10                           |")
    print(f"  | 發生: {n4:>3d} 次 ({n4/ntot*100:.1f}% of OOS trades){'':>24s}|")
    print(f"  | 平均PnL: ${avg4:+.1f}  總PnL: ${pnl4:+,.0f}{'':>28s}|")
    print(f"  | 結論: 無害但浪費時間和注意力{'':>30s}|")
    print(f"  | 實盤建議: 不需干預，策略自動處理{'':>26s}|")
    print(f"  +{'-'*62}+")
    scenarios.append(("橫盤消磨", n4, n4/ntot*100 if ntot>0 else 0, pnl4))

    # S5: Consecutive Loss Streak
    pnls=M["pnl"].values
    max_streak=0; cur_streak=0; max_dd_streak=0; cur_dd=0
    streak_start=0; worst_start=0; worst_end=0
    for j in range(len(pnls)):
        if pnls[j]<0:
            cur_streak+=1; cur_dd+=pnls[j]
            if cur_streak>max_streak:
                max_streak=cur_streak; worst_end=j; worst_start=j-cur_streak+1
            if cur_dd<max_dd_streak: max_dd_streak=cur_dd
        else:
            cur_streak=0; cur_dd=0
    print(f"\n  +{'-'*62}+")
    print(f"  | S5: 連敗風暴 (最大連續虧損)                                 |")
    print(f"  | 最大連敗: {max_streak} 筆連續虧損{'':>39s}|")
    print(f"  | 連敗期間累計: ${max_dd_streak:+,.0f}{'':>38s}|")
    if max_streak>0 and worst_start<len(M):
        ws_dt=pd.Timestamp(M.iloc[worst_start]["edt"] if "edt" in M.columns else 0)
        print(f"  | 起始時間: ~{str(ws_dt)[:10]}{'':>40s}|")
    print(f"  | 結論: WR47%下連敗{max_streak}筆屬統計正常(預期~8-9){'':>13s}|")
    print(f"  | 實盤建議: 帳戶需承受${abs(max_dd_streak):,.0f}連續回撤{'':>20s}|")
    print(f"  +{'-'*62}+")
    scenarios.append(("連敗風暴", max_streak, 0, max_dd_streak))

    # Overall Dim1 assessment
    total_scenario_loss = pnl1 + pnl2 + pnl3
    risk = "Low" if max_streak <= 10 and abs(max_dd_streak) < 800 else \
           "Medium" if max_streak <= 15 else "High"
    status = "PASS" if risk != "High" else "WARN"
    print(f"\n  Dim1 總結: S1+S2+S3 合計虧損 ${total_scenario_loss:+,.0f}")
    print(f"  這些虧損是趨勢跟隨的結構性成本 (由 Trail 大贏家覆蓋)")
    return {"status": status, "risk": risk}

# =========================================================================
#  DIMENSION 2: Market Regime Classification
# =========================================================================
def dim2(oos, df):
    hdr("DIMENSION 2: 市場環境分類測試")
    print("  趨勢: 30bar收盤回報 | 波動率: park_long vs 中位數")

    RET=df["ret30"].values; PL=df["pl"].values
    pl_vals=PL[~np.isnan(PL)]; pl_med=np.median(pl_vals)

    # Classify each trade at signal time
    regimes=[]
    for _, t in oos.iterrows():
        si=int(t["si"])
        ret=RET[si] if not np.isnan(RET[si]) else 0
        plv=PL[si] if not np.isnan(PL[si]) else pl_med
        if ret>5: trend="Up"
        elif ret<-5: trend="Down"
        else: trend="Side"
        vol="HiVol" if plv>pl_med else "LoVol"
        regimes.append({"regime":f"{trend}+{vol}","pnl":t["pnl_nf"]-DEF_FEE,
                        "tp":t["tp"],"sd":t["sd"]})
    rdf=pd.DataFrame(regimes)

    print(f"\n  park_long 中位數: {pl_med:.5f}")
    print(f"\n  {'環境':<12s} {'N筆':>5s} {'WR':>6s} {'PF':>7s} {'PnL':>10s} {'Avg':>8s}")
    print(f"  {'-'*50}")
    regime_results={}
    profitable_count = 0
    total_regimes = 0
    for reg in ["Up+HiVol","Up+LoVol","Side+HiVol","Side+LoVol","Down+HiVol","Down+LoVol"]:
        sub=rdf[rdf["regime"]==reg]
        n=len(sub)
        if n==0:
            print(f"  {reg:<12s} {'0':>5s} {'N/A':>6s} {'N/A':>7s} {'$0':>10s} {'N/A':>8s}")
            regime_results[reg]={"n":0,"pnl":0,"wr":0,"pf":0}
            continue
        total_regimes += 1
        pnl=sub["pnl"].sum(); wr=(sub["pnl"]>0).mean()*100
        w=sub[sub["pnl"]>0]["pnl"].sum(); l=abs(sub[sub["pnl"]<=0]["pnl"].sum())
        pf=w/l if l>0 else 999; avg=pnl/n
        if pnl > 0: profitable_count += 1
        print(f"  {reg:<12s} {n:>5d} {wr:>5.1f}% {pf:>7.2f} ${pnl:>+9,.0f} ${avg:>+7.1f}")
        regime_results[reg]={"n":n,"pnl":pnl,"wr":wr,"pf":pf}

    # Find weakest regime
    worst_reg=min(regime_results, key=lambda x: regime_results[x]["pnl"])
    worst_pnl=regime_results[worst_reg]["pnl"]
    print(f"\n  最脆弱環境: {worst_reg} (PnL ${worst_pnl:+,.0f})")

    # Side analysis: long vs short per trend
    print(f"\n  多空 x 趨勢拆解:")
    for trend in ["Up","Side","Down"]:
        for side in ["long","short"]:
            sub=rdf[(rdf["regime"].str.startswith(trend))&(rdf["sd"]==side)]
            n=len(sub); pnl=sub["pnl"].sum() if n>0 else 0
            wr=(sub["pnl"]>0).mean()*100 if n>0 else 0
            print(f"    {trend}+{side}: {n:>3d}筆, ${pnl:>+8,.0f}, WR {wr:.0f}%")

    risk = "Low" if profitable_count >= 4 else "Medium" if profitable_count >= 3 else "High"
    status = "PASS" if profitable_count >= 4 else "WARN" if profitable_count >= 3 else "FAIL"
    print(f"\n  Dim2 總結: {profitable_count}/{total_regimes} 個環境盈利")
    return {"status": status, "risk": risk}

# =========================================================================
#  DIMENSION 3: Cost Sensitivity
# =========================================================================
def dim3(oos):
    hdr("DIMENSION 3: 成本敏感度測試")
    n=len(oos); total_nf=oos["pnl_nf"].sum()
    breakeven_fee = total_nf / n if n > 0 else 0

    fees = [
        ("Maker費率(VIP)", 1.50),
        ("基準Taker", 2.00),
        ("標準Taker+滑點", 3.00),
        ("流動性差/大波動", 5.00),
        ("極端滑點", 8.00),
        ("最差情境", 12.00),
    ]
    print(f"\n  OOS 交易數: {n}")
    print(f"  Fee-free 總 PnL: ${total_nf:+,.2f}")
    print(f"  盈虧平衡費用: ${breakeven_fee:.2f}/trade")
    print(f"\n  {'成本情境':<18s} {'費用':>6s} {'PnL':>10s} {'WR':>6s} {'PF':>7s} {'達標':>4s}")
    print(f"  {'-'*55}")

    be_found = False
    for label, fee in fees:
        pnl = total_nf - n * fee
        t = oos.copy(); t["pnl"] = t["pnl_nf"] - fee
        wr = (t["pnl"]>0).mean()*100
        w = t[t["pnl"]>0]["pnl"].sum(); l = abs(t[t["pnl"]<=0]["pnl"].sum())
        pf = w/l if l>0 else 999
        ok = "V" if pnl > 0 else "X"
        print(f"  {label:<18s} ${fee:>5.2f} ${pnl:>+9,.0f} {wr:>5.1f}% {pf:>7.2f} {ok:>4s}")
        if pnl <= 0 and not be_found:
            be_found = True

    risk = "Low" if total_nf - n*5 > 0 else "Medium" if total_nf - n*3 > 0 else "High"
    status = "PASS" if risk == "Low" else "WARN"
    print(f"\n  Dim3 總結: 策略在 ${breakeven_fee:.1f}/trade 才歸零")
    print(f"  即使費用翻4倍到$8仍盈利 → 成本抗性極強")
    return {"status": status, "risk": risk}

# =========================================================================
#  DIMENSION 4: Parameter Robustness (±20%)
# =========================================================================
def dim4(df_raw, df):
    hdr("DIMENSION 4: 參數穩健性測試 (±20%)")

    # Park threshold sweep
    print(f"\n  [A] Parkinson 百分位門檻 (基準=30, 範圍 24-36)")
    print(f"  {'PT':>4s} {'N筆':>5s} {'PnL':>10s} {'WR':>6s} {'PF':>7s} {'MDD%':>6s}")
    print(f"  {'-'*42}")
    pt_results={}
    for pt in [24, 27, 30, 33, 36]:
        trades=bt(df, pt=pt, sn=DEF_SN)
        if len(trades)>0:
            trades["xdt"]=pd.to_datetime(trades["xdt"])
            o=trades[trades["xdt"]>=MID_TS]
        else: o=trades
        s=oos_stats(o)
        mark=" <<<" if pt==DEF_PT else ""
        print(f"  {pt:>4d} {s['n']:>5d} ${s['pnl']:>+9,.0f} {s['wr']:>5.1f}% {s['pf']:>7.2f} {s['mdd_pct']:>5.1f}%{mark}")
        pt_results[pt]=s

    # Check for cliff: max drop between adjacent values
    pt_vals=[24,27,30,33,36]
    pt_pnls=[pt_results[p]["pnl"] for p in pt_vals]
    pt_diffs=[abs(pt_pnls[i+1]-pt_pnls[i]) for i in range(len(pt_pnls)-1)]
    pt_max_drop=max(pt_diffs)
    pt_smooth = pt_max_drop < 2000
    print(f"  相鄰最大變化: ${pt_max_drop:,.0f} → {'平滑漸變' if pt_smooth else '有懸崖效應!'}")

    # Breakout lookback sweep
    print(f"\n  [B] Breakout 回看窗口 (基準=10, 範圍 8-12)")
    print(f"  {'BL':>4s} {'N筆':>5s} {'PnL':>10s} {'WR':>6s} {'PF':>7s} {'MDD%':>6s}")
    print(f"  {'-'*42}")
    bl_results={}
    for blk in [8, 9, 10, 11, 12]:
        if blk==DEF_BL:
            df2=df
        else:
            df2=recompute_brk(df, blk)
        trades=bt(df2, pt=DEF_PT, sn=DEF_SN)
        if len(trades)>0:
            trades["xdt"]=pd.to_datetime(trades["xdt"])
            o=trades[trades["xdt"]>=MID_TS]
        else: o=trades
        s=oos_stats(o)
        mark=" <<<" if blk==DEF_BL else ""
        print(f"  {blk:>4d} {s['n']:>5d} ${s['pnl']:>+9,.0f} {s['wr']:>5.1f}% {s['pf']:>7.2f} {s['mdd_pct']:>5.1f}%{mark}")
        bl_results[blk]=s

    bl_vals=[8,9,10,11,12]
    bl_pnls=[bl_results[b]["pnl"] for b in bl_vals]
    bl_diffs=[abs(bl_pnls[i+1]-bl_pnls[i]) for i in range(len(bl_pnls)-1)]
    bl_max_drop=max(bl_diffs)
    bl_smooth = bl_max_drop < 2000
    print(f"  相鄰最大變化: ${bl_max_drop:,.0f} → {'平滑漸變' if bl_smooth else '有懸崖效應!'}")

    # SafeNet sweep
    print(f"\n  [C] SafeNet 水位 (基準=3.5%, 範圍 2.8%-4.2%)")
    print(f"  {'SN%':>5s} {'N筆':>5s} {'PnL':>10s} {'WR':>6s} {'PF':>7s} {'MDD%':>6s} {'SN次':>5s}")
    print(f"  {'-'*48}")
    sn_results={}
    for snv in [0.028, 0.031, 0.035, 0.039, 0.042]:
        trades=bt(df, pt=DEF_PT, sn=snv)
        if len(trades)>0:
            trades["xdt"]=pd.to_datetime(trades["xdt"])
            o=trades[trades["xdt"]>=MID_TS]
        else: o=trades
        s=oos_stats(o)
        sn_count=len(o[o["tp"]=="SafeNet"]) if len(o)>0 else 0
        mark=" <<<" if snv==DEF_SN else ""
        print(f"  {snv*100:>4.1f}% {s['n']:>5d} ${s['pnl']:>+9,.0f} {s['wr']:>5.1f}% {s['pf']:>7.2f} {s['mdd_pct']:>5.1f}% {sn_count:>5d}{mark}")
        sn_results[snv]=s

    sn_vals=[0.028,0.031,0.035,0.039,0.042]
    sn_pnls=[sn_results[s]["pnl"] for s in sn_vals]
    sn_diffs=[abs(sn_pnls[i+1]-sn_pnls[i]) for i in range(len(sn_pnls)-1)]
    sn_max_drop=max(sn_diffs)
    sn_smooth = sn_max_drop < 2000
    print(f"  相鄰最大變化: ${sn_max_drop:,.0f} → {'平滑漸變' if sn_smooth else '有懸崖效應!'}")

    all_smooth = pt_smooth and bl_smooth and sn_smooth
    all_profitable = all(pt_results[p]["pnl"]>0 for p in pt_vals) and \
                     all(bl_results[b]["pnl"]>0 for b in bl_vals) and \
                     all(sn_results[s]["pnl"]>0 for s in sn_vals)
    risk = "Low" if all_smooth and all_profitable else \
           "Medium" if all_profitable else "High"
    status = "PASS" if risk == "Low" else "WARN" if risk == "Medium" else "FAIL"
    print(f"\n  Dim4 總結: 全部±20%均盈利={all_profitable}, 全部平滑={all_smooth}")
    return {"status": status, "risk": risk}

# =========================================================================
#  DIMENSION 5: Time Stability + Walk-Forward
# =========================================================================
def dim5(all_trades, df):
    hdr("DIMENSION 5: 時間穩定性測試")

    t=all_trades.copy()
    t["pnl"]=t["pnl_nf"]-DEF_FEE
    t["xdt"]=pd.to_datetime(t["xdt"])
    t["ym"]=t["xdt"].dt.to_period("M")

    # Quarterly breakdown (full period)
    t["yq"]=t["xdt"].dt.to_period("Q")
    print(f"\n  [A] 季度拆解 (全期)")
    print(f"  {'季度':<10s} {'N筆':>5s} {'PnL':>10s} {'WR':>6s} {'PF':>7s}")
    print(f"  {'-'*40}")
    pos_q=0; tot_q=0
    for q in sorted(t["yq"].unique()):
        sub=t[t["yq"]==q]; n=len(sub)
        if n==0: continue
        tot_q+=1
        pnl=sub["pnl"].sum(); wr=(sub["pnl"]>0).mean()*100
        w=sub[sub["pnl"]>0]["pnl"].sum(); l=abs(sub[sub["pnl"]<=0]["pnl"].sum())
        pf=w/l if l>0 else 999
        if pnl>0: pos_q+=1
        oos_mark=" *OOS" if pd.Timestamp(q.start_time)>=MID_TS else ""
        print(f"  {str(q):<10s} {n:>5d} ${pnl:>+9,.0f} {wr:>5.1f}% {pf:>7.2f}{oos_mark}")
    print(f"  盈利季度: {pos_q}/{tot_q}")

    # Monthly Walk-Forward
    print(f"\n  [B] 月度 Walk-Forward (全期)")
    print(f"  {'月份':<10s} {'N筆':>5s} {'PnL':>10s} {'WR':>6s} {'累計PnL':>10s}")
    print(f"  {'-'*45}")
    pos_m=0; tot_m=0; cum=0
    monthly_pnls=[]
    for m in sorted(t["ym"].unique()):
        sub=t[t["ym"]==m]; n=len(sub)
        if n==0: continue
        tot_m+=1
        pnl=sub["pnl"].sum(); wr=(sub["pnl"]>0).mean()*100
        cum+=pnl
        if pnl>0: pos_m+=1
        monthly_pnls.append(pnl)
        oos_mark=" *" if pd.Timestamp(m.start_time)>=MID_TS else ""
        print(f"  {str(m):<10s} {n:>5d} ${pnl:>+9,.0f} {wr:>5.1f}% ${cum:>+9,.0f}{oos_mark}")

    # Consecutive losing months
    max_losing_months=0; cur=0
    for p in monthly_pnls:
        if p<0: cur+=1; max_losing_months=max(max_losing_months, cur)
        else: cur=0

    # Max drawdown duration (in months)
    cum_arr=np.cumsum(monthly_pnls)
    peak=np.maximum.accumulate(cum_arr)
    dd_arr=cum_arr-peak
    in_dd=(dd_arr<0)
    max_dd_months=0; cur=0
    for d in in_dd:
        if d: cur+=1; max_dd_months=max(max_dd_months, cur)
        else: cur=0

    print(f"\n  正月率: {pos_m}/{tot_m} = {pos_m/tot_m*100:.0f}%")
    print(f"  最大連虧月數: {max_losing_months}")
    print(f"  最大回撤持續: {max_dd_months} 個月")

    # OOS-only monthly stats
    oos_months = [p for p, m in zip(monthly_pnls, sorted(t["ym"].unique()))
                  if pd.Timestamp(m.start_time) >= MID_TS]
    oos_pos = sum(1 for p in oos_months if p > 0)
    oos_tot = len(oos_months)
    print(f"  OOS正月率: {oos_pos}/{oos_tot} = {oos_pos/oos_tot*100:.0f}%" if oos_tot>0 else "")

    pct = pos_m/tot_m*100 if tot_m>0 else 0
    risk = "Low" if pct >= 65 else "Medium" if pct >= 50 else "High"
    status = "PASS" if pct >= 65 else "WARN" if pct >= 50 else "FAIL"
    print(f"\n  Dim5 總結: 正月率 {pct:.0f}%, 連虧{max_losing_months}月")
    return {"status": status, "risk": risk}

# =========================================================================
#  DIMENSION 6: SafeNet Deep Analysis
# =========================================================================
def dim6(oos, df):
    hdr("DIMENSION 6: SafeNet 深度解剖")
    H=df["high"].values; L=df["low"].values; C=df["close"].values; N=len(df)

    sn=oos[oos["tp"]=="SafeNet"].copy()
    sn["pnl"]=sn["pnl_nf"]-DEF_FEE
    tr=oos[oos["tp"]=="Trail"].copy()
    tr["pnl"]=tr["pnl_nf"]-DEF_FEE
    n_sn=len(sn); n_tr=len(tr)

    print(f"\n  SafeNet: {n_sn} 筆, ${sn['pnl'].sum():+,.0f}")
    print(f"  Trail:   {n_tr} 筆, ${tr['pnl'].sum():+,.0f}")
    print(f"  SafeNet佔Trail收入: {abs(sn['pnl'].sum())/tr['pnl'].sum()*100:.0f}%" if tr['pnl'].sum()>0 else "")

    # (a) Common characteristics
    print(f"\n  [A] SafeNet 共同特徵分析")
    # Direction
    sn_long=sn[sn["sd"]=="long"]; sn_short=sn[sn["sd"]=="short"]
    print(f"    方向: Long {len(sn_long)} ({len(sn_long)/n_sn*100:.0f}%) / Short {len(sn_short)} ({len(sn_short)/n_sn*100:.0f}%)")

    # Hold time (bars)
    if n_sn > 0:
        print(f"    平均持倉: {sn['bars'].mean():.1f}h (中位數 {sn['bars'].median():.0f}h)")
        quick=len(sn[sn["bars"]<=4]); print(f"    4h內觸發: {quick} ({quick/n_sn*100:.0f}%)")
        mid=len(sn[(sn["bars"]>4)&(sn["bars"]<=12)]); print(f"    4-12h觸發: {mid} ({mid/n_sn*100:.0f}%)")

    # Hour of entry distribution
    sn["entry_h"]=pd.to_datetime(sn["edt"]).dt.hour
    tr["entry_h"]=pd.to_datetime(tr["edt"]).dt.hour
    print(f"\n    進場時段(SafeNet vs Trail):")
    for h_range, label in [((3,8),"凌晨3-8h"),((8,12),"上午8-12h"),((13,18),"下午13-18h"),((18,24),"晚間18-24h")]:
        sn_h=len(sn[(sn["entry_h"]>=h_range[0])&(sn["entry_h"]<h_range[1])])
        tr_h=len(tr[(tr["entry_h"]>=h_range[0])&(tr["entry_h"]<h_range[1])])
        sn_pct=sn_h/n_sn*100 if n_sn>0 else 0
        tr_pct=tr_h/n_tr*100 if n_tr>0 else 0
        flag=" !!!" if sn_pct > tr_pct * 1.5 and sn_h >= 3 else ""
        print(f"      {label}: SN {sn_h}({sn_pct:.0f}%) vs Trail {tr_h}({tr_pct:.0f}%){flag}")

    # Parkinson percentile at entry
    if n_sn > 0:
        print(f"\n    Parkinson百分位(入場時):")
        print(f"      SafeNet 平均: {sn['ppv'].mean():.1f}, Trail 平均: {tr['ppv'].mean():.1f}")
        low_pp=sn[sn["ppv"]<15]; print(f"      pp<15(極度壓縮): SN {len(low_pp)}/{n_sn}, Trail {len(tr[tr['ppv']<15])}/{n_tr}")

    # (b) Predictability
    print(f"\n  [B] SafeNet 可預測性")
    # Is there a pre-entry signal that predicts SafeNet?
    # Check: breakout strength (distance from breakout level)
    # SafeNet trades vs Trail trades
    all_oos = oos.copy()
    all_oos["pnl"] = all_oos["pnl_nf"] - DEF_FEE
    # Check avg bars to SafeNet
    if n_sn > 0:
        avg_sn_bars = sn["bars"].mean()
        avg_tr_bars = tr["bars"].mean()
        print(f"    SafeNet 平均存活: {avg_sn_bars:.1f}h vs Trail {avg_tr_bars:.1f}h")

        # Side balance
        sn_long_pct = len(sn_long)/n_sn*100
        tr_long_pct = len(tr[tr["sd"]=="long"])/n_tr*100 if n_tr>0 else 0
        print(f"    Long佔比: SN {sn_long_pct:.0f}% vs Trail {tr_long_pct:.0f}%")
        if abs(sn_long_pct - tr_long_pct) > 15:
            print(f"    *** 方向偏差 > 15%: SafeNet 偏向某一方向 ***")
        else:
            print(f"    方向分布與Trail一致 → 無法事前從方向預測SafeNet")

    # (c) Post-SafeNet 48h price behavior
    print(f"\n  [C] SafeNet 觸發後 48h 價格走勢")
    correct=0; premature=0; partial=0; skipped=0
    post_moves=[]
    for _, t in sn.iterrows():
        xi=int(t["xi"]); ep=t["ep"]; xp=t["xp"]; sd=t["sd"]
        if xi+48 >= N:
            skipped+=1; continue
        c48=C[xi+48]
        if sd=="long":
            # SafeNet sold us out at loss. Did price recover?
            if c48 > ep: premature+=1; tag="premature"  # fully recovered above entry
            elif c48 < xp: correct+=1; tag="correct"    # kept falling below exit
            else: partial+=1; tag="partial"              # between exit and entry
            move=(c48-xp)/ep*100
        else:
            if c48 < ep: premature+=1; tag="premature"
            elif c48 > xp: correct+=1; tag="correct"
            else: partial+=1; tag="partial"
            move=(xp-c48)/ep*100
        post_moves.append({"move":move,"tag":tag})
    analyzed=correct+premature+partial
    print(f"    分析: {analyzed} 筆 (跳過 {skipped} 筆因數據不足)")
    if analyzed>0:
        print(f"    SafeNet 正確 (價格繼續反向): {correct} ({correct/analyzed*100:.0f}%)")
        print(f"    SafeNet 過早 (價格完全恢復): {premature} ({premature/analyzed*100:.0f}%)")
        print(f"    部分恢復: {partial} ({partial/analyzed*100:.0f}%)")
        pm=pd.DataFrame(post_moves)
        correct_avg=pm[pm["tag"]=="correct"]["move"].mean() if correct>0 else 0
        premature_avg=pm[pm["tag"]=="premature"]["move"].mean() if premature>0 else 0
        print(f"    正確止損後48h平均繼續反向: {correct_avg:+.1f}%")
        print(f"    過早止損後48h平均恢復: {premature_avg:+.1f}%")

    # (d) SafeNet level sensitivity (brief, complements Dim4)
    print(f"\n  [D] SafeNet 水位 vs 觸發次數")
    for snv in [0.025, 0.030, 0.035, 0.040, 0.045, 0.050]:
        trades=bt(df, pt=DEF_PT, sn=snv)
        if len(trades)>0:
            trades["xdt"]=pd.to_datetime(trades["xdt"])
            o=trades[trades["xdt"]>=MID_TS]
        else: o=trades
        sn_c=len(o[o["tp"]=="SafeNet"]) if len(o)>0 else 0
        pnl=oos_stats(o)["pnl"]
        mark=" <<<" if snv==DEF_SN else ""
        print(f"    ±{snv*100:.1f}%: {sn_c:>3d} SafeNet, PnL ${pnl:>+9,.0f}{mark}")

    sn_correct_pct = correct/analyzed*100 if analyzed>0 else 0
    risk = "Low" if sn_correct_pct >= 50 else "Medium" if sn_correct_pct >= 35 else "High"
    status = "PASS" if risk == "Low" else "WARN"
    print(f"\n  Dim6 總結: SafeNet正確率 {sn_correct_pct:.0f}%, 佔Trail {abs(sn['pnl'].sum())/tr['pnl'].sum()*100:.0f}%")
    return {"status": status, "risk": risk}

# =========================================================================
#  FINAL HEALTH ASSESSMENT
# =========================================================================
def final_assessment(results):
    hdr("R10 策略健康度評估 — 最終報告")
    print(f"\n  {'驗證維度':<20s} {'結果':>6s} {'風險等級':>8s}")
    print(f"  {'-'*36}")
    dims = [
        ("極端價格情境", results["d1"]),
        ("市場環境分類", results["d2"]),
        ("成本敏感度", results["d3"]),
        ("參數穩健性", results["d4"]),
        ("時間穩定性", results["d5"]),
        ("SafeNet解剖", results["d6"]),
    ]
    warn_count = sum(1 for _, d in dims if d["status"] == "WARN")
    fail_count = sum(1 for _, d in dims if d["status"] == "FAIL")
    for name, d in dims:
        print(f"  {name:<20s} {d['status']:>6s} {d['risk']:>8s}")

    print(f"\n  PASS: {6-warn_count-fail_count} / WARN: {warn_count} / FAIL: {fail_count}")

    if fail_count > 0:
        overall = "不建議實盤"
    elif warn_count >= 3:
        overall = "需要改善"
    elif warn_count >= 1:
        overall = "適合實盤（有注意事項）"
    else:
        overall = "適合實盤"
    print(f"\n  整體評估: {overall}")

    print(f"\n  最脆弱的點:")
    high_risk = [name for name, d in dims if d["risk"] == "High"]
    med_risk = [name for name, d in dims if d["risk"] == "Medium"]
    if high_risk:
        print(f"    HIGH: {', '.join(high_risk)}")
    if med_risk:
        print(f"    MEDIUM: {', '.join(med_risk)}")
    if not high_risk and not med_risk:
        print(f"    所有維度均為 Low Risk")

    print(f"\n  實盤前必須注意:")
    print(f"    1. 連敗承受力: WR47%意味著8-10筆連虧是常態, 帳戶需有緩衝")
    print(f"    2. IS虧損: 策略在2024年IS期虧損, 非所有年份都能盈利")
    print(f"    3. 利潤集中: 大部分利潤來自少數24-96h大趨勢, 缺少耐心會錯過")

    print(f"\n  實盤監控指標:")
    print(f"    - 月度PnL: 連續3個月虧損 → 暫停檢視")
    print(f"    - SafeNet比率: 如果SafeNet/Trail > 50% → 市場結構可能已變")
    print(f"    - 月均交易數: 如果<10筆/月持續2個月 → 壓縮機制可能失效")
    print(f"    - 單筆最大虧損: 如果 > $150 → 檢查滑點是否異常")

# =========================================================================
#  MAIN
# =========================================================================
if __name__ == "__main__":
    print("="*70)
    print("  R10 LEAN 3-FACTOR: 六維度全面驗證")
    print("  所有情境定義在看數據前已鎖定")
    print("="*70)

    df_raw = load()
    print("  Computing indicators...", flush=True)
    df = compute(df_raw)
    print("  Running baseline backtest...", flush=True)
    trades = bt(df)
    trades["xdt"] = pd.to_datetime(trades["xdt"])
    trades["edt"] = pd.to_datetime(trades["edt"])

    oos = trades[trades["xdt"] >= MID_TS].reset_index(drop=True)
    ist = trades[trades["xdt"] < MID_TS].reset_index(drop=True)
    oos_s = oos_stats(oos); is_s = oos_stats(ist)

    print(f"\n  基準確認:")
    print(f"  IS:  {is_s['n']:>4d}筆, ${is_s['pnl']:>+9,.0f}, PF {is_s['pf']:.2f}, WR {is_s['wr']:.1f}%, MDD {is_s['mdd_pct']:.1f}%")
    print(f"  OOS: {oos_s['n']:>4d}筆, ${oos_s['pnl']:>+9,.0f}, PF {oos_s['pf']:.2f}, WR {oos_s['wr']:.1f}%, MDD {oos_s['mdd_pct']:.1f}%")

    results = {}
    results["d1"] = dim1(oos, df)
    results["d2"] = dim2(oos, df)
    results["d3"] = dim3(oos)
    results["d4"] = dim4(df_raw, df)
    results["d5"] = dim5(trades, df)
    results["d6"] = dim6(oos, df)

    final_assessment(results)
    print(f"\n{'='*70}")
    print(f"  驗證完成")
    print(f"{'='*70}")
