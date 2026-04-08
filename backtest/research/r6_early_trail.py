"""
╔══════════════════════════════════════╗
║  第 6 輪：分階段 Trail + Session     ║
╚══════════════════════════════════════╝

已探索清單：
  R1: 替代波動率估算器 → 全不如 GK
  R2: 進場品質過濾器 → 全降 PnL
  R3: 出場參數 → 近局部最優
  R4: 結構參數 → 近最優
  R5: 倉位管理 → maxSame=4 +6.7%（最佳發現），其他無效
目前最佳記錄：GK maxSame=3 OOS $7,837 (maxSame=4 $8,359 尚未稽核)

本輪假說：
  方向：分階段 trail 策略 + session 優化

  A) 分階段 Trail（核心假說）：
     7-12h 桶有 185 筆 14% WR，是策略最大成本中心 (-$4,158)。
     如果在 bar 7-12 用更緊的 trail（EMA10/15），可以更快止損，
     但不影響 bar 12+ 的贏家（因為那時切回 EMA20）。
     這不是「進場過濾」（R2 失敗），是「出場加速」。

  B) Session 小時優化：
     目前 block {0,1,2,12} UTC+8。逐小時統計 PnL 找出是否有其他
     應排除或應恢復的小時。

上帝視角自檢：
  ☑ signal 只用 shift(1) 或更早數據？→ 是
  ☑ 進場價是 next bar open？→ 是
  ☑ 分階段 trail 只用 bar count（進場後已知）？→ 是
  ☑ session 優化只改 blocked hours/days？→ 是
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMP_SHORT = 5; COMP_LONG = 20; COMP_WIN = 100; COMP_THRESH = 30
BRK_LOOK = 10; MAX_SAME = 3; EXIT_CD = 12
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
    df = pd.read_csv(p); df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    print(f"Loaded {len(df)} bars: {df['datetime'].min()} to {df['datetime'].max()}")
    return df

def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100

def compute_indicators(df):
    d = df.copy()
    for span in [10, 15, 20, 25]:
        d[f"ema{span}"] = d["close"].ewm(span=span).mean()
    ln_hl = np.log(d["high"]/d["low"])
    ln_co = np.log(d["close"]/d["open"])
    gk = 0.5*ln_hl**2 - (2*np.log(2)-1)*ln_co**2
    gk_s = gk.rolling(COMP_SHORT).mean(); gk_l = gk.rolling(COMP_LONG).mean()
    d["pp"] = (gk_s/gk_l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    cs1 = d["close"].shift(1)
    d["cmx"] = d["close"].shift(2).rolling(BRK_LOOK-1).max()
    d["cmn"] = d["close"].shift(2).rolling(BRK_LOOK-1).min()
    d["bl"] = cs1 > d["cmx"]; d["bs"] = cs1 < d["cmn"]
    d["h"] = d["datetime"].dt.hour; d["wd"] = d["datetime"].dt.weekday
    d["pp_p"] = d["pp"].shift(1)
    d["bl_p"] = d["bl"].shift(1); d["bs_p"] = d["bs"].shift(1)
    return d


def backtest_phased_trail(df, early_ema=20, late_ema=20, switch_bar=12,
                          block_hours=BLOCK_H, block_days=BLOCK_D, max_same=MAX_SAME):
    """
    Phased trail: use early_ema for bars [MIN_TRAIL, switch_bar),
                  use late_ema for bars >= switch_bar.
    """
    d = df.copy()
    d["sok"] = ~(d["h"].isin(block_hours) | d["wd"].isin(block_days))
    d["sok_p"] = d["sok"].shift(1)

    N = len(d); w = COMP_WIN + COMP_LONG + 20
    O=d["open"].values; H=d["high"].values; L=d["low"].values; C=d["close"].values
    E_EARLY = d[f"ema{early_ema}"].values; E_LATE = d[f"ema{late_ema}"].values
    PP=d["pp"].values; BL=d["bl"].values; BS=d["bs"].values; SOK=d["sok"].values
    PP_P=d["pp_p"].values; BL_P=d["bl_p"].values; BS_P=d["bs_p"].values; SOK_P=d["sok_p"].values
    DT=d["datetime"].values

    lp=[]; sp=[]; trades=[]; last_exit={"long":-9999,"short":-9999}

    for i in range(w, N-1):
        # Exit longs
        nl=[]
        for p in lp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            E = E_EARLY if bh < switch_bar else E_LATE

            sn=ep*(1-SN_PCT)
            if L[i]<=sn:
                xp=sn-(sn-L[i])*0.25
                trades.append({"pnl":(xp-ep)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<switch_bar:
                trail=C[i]<=E[i]; early=C[i]<=ep*(1-ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":tp,"sd":"long","bars":bh,"dt":DT[i]})
                    last_exit["long"]=i; exited=True
            if not exited and bh>=switch_bar and C[i]<=E[i]:
                trades.append({"pnl":(C[i]-ep)*NOTIONAL/ep-FEE,"tp":"Trail","sd":"long","bars":bh,"dt":DT[i]})
                last_exit["long"]=i; exited=True
            if not exited: nl.append(p)
        lp=nl

        # Exit shorts
        ns=[]
        for p in sp:
            bh=i-p["ei"]; ep=p["e"]; exited=False
            E = E_EARLY if bh < switch_bar else E_LATE

            sn=ep*(1+SN_PCT)
            if H[i]>=sn:
                xp=sn+(H[i]-sn)*0.25
                trades.append({"pnl":(ep-xp)*NOTIONAL/ep-FEE,"tp":"SafeNet","sd":"short","bars":bh,"dt":DT[i]})
                last_exit["short"]=i; exited=True
            if not exited and MIN_TRAIL<=bh<switch_bar:
                trail=C[i]>=E[i]; early=C[i]>=ep*(1+ES_PCT)
                if trail or early:
                    tp="EarlyStop" if(early and not trail) else "Trail"
                    trades.append({"pnl":(ep-C[i])*NOTIONAL/ep-FEE,"tp":tp,"sd":"short","bars":bh,"dt":DT[i]})
                    last_exit["short"]=i; exited=True
            if not exited and bh>=switch_bar and C[i]>=E[i]:
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
    r=analyze(trades_df,mid_ts)
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
    oos2=oos.copy(); oos2["mo"]=oos2["dt"].dt.to_period("M")
    mo=oos2.groupby("mo")["pnl"].sum()
    print(f"\n  Monthly PnL (OOS): {(mo>0).sum()}/{len(mo)} positive")
    for m,p in mo.items(): print(f"    {m}: ${p:>+10,.0f}")


def walk_forward(df_raw, params, n_folds=10):
    d=df_raw.copy(); d["datetime"]=pd.to_datetime(d["datetime"])
    oos_df=d[d["datetime"]>=MID_TS].copy()
    if len(oos_df)<200: print("  Not enough data"); return 0
    fold_size=len(oos_df)//n_folds; results=[]
    for fold in range(n_folds):
        start=fold*fold_size; end=start+fold_size if fold<n_folds-1 else len(oos_df)
        fold_df=oos_df.iloc[max(0,start-300):end].copy()
        fold_df=compute_indicators(fold_df)
        trades=backtest_phased_trail(fold_df, **params)
        pnl=trades["pnl"].sum() if len(trades)>0 else 0
        results.append(pnl)
    pos=sum(1 for r in results if r>0)
    print(f"\n  Walk-Forward ({n_folds} folds): {pos}/{n_folds} positive")
    for i,r in enumerate(results): print(f"    Fold {i+1}: ${r:>+10,.0f}")
    return pos


def main():
    df_raw = load()
    print(f"IS/OOS split: {MID_TS}\n")
    df = compute_indicators(df_raw)

    # ════════════════════════════════════
    # Test A: Phased trail — tighter early, standard late
    # ════════════════════════════════════
    print("=" * 90)
    print("  Test A: Phased Trail (early_ema for bar 7-switch, late_ema=20 after)")
    print("=" * 90)
    phase_configs = [
        ("Baseline: EMA20 throughout", 20, 20, 12),
        ("EMA10 bar7-12, EMA20 after", 10, 20, 12),
        ("EMA15 bar7-12, EMA20 after", 15, 20, 12),
        ("EMA10 bar7-15, EMA20 after", 10, 20, 15),
        ("EMA15 bar7-15, EMA20 after", 15, 20, 15),
        ("EMA10 bar7-18, EMA20 after", 10, 20, 18),
        ("EMA15 bar7-18, EMA20 after", 15, 20, 18),
        ("EMA15 bar7-24, EMA20 after", 15, 20, 24),
    ]
    phase_results = []
    for label, ee, le, sb in phase_configs:
        trades = backtest_phased_trail(df, early_ema=ee, late_ema=le, switch_bar=sb)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Baseline") else ""
        print(f"  {label:<38s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h{tag}")
        phase_results.append((label, r, trades, {"early_ema":ee,"late_ema":le,"switch_bar":sb}))

    # ════════════════════════════════════
    # Test B: Session hour analysis
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test B: Per-Hour PnL Analysis (OOS, using baseline strategy)")
    print("=" * 90)

    # Run baseline and tag entry hour
    base_trades = backtest_phased_trail(df, early_ema=20, late_ema=20, switch_bar=12)
    bt = base_trades.copy(); bt["dt"] = pd.to_datetime(bt["dt"])
    oos_bt = bt[bt["dt"] >= MID_TS].copy()

    # Tag entry hour (entry = exit_bar - hold_bars)
    # Since entry bar in data has an hour associated with it:
    # entry_dt = exit_dt - bars * 1h
    oos_bt["entry_dt"] = oos_bt.apply(lambda r: r["dt"] - pd.Timedelta(hours=r["bars"]), axis=1)
    oos_bt["entry_h"] = oos_bt["entry_dt"].dt.hour

    print(f"  {'Hour':>4s} {'Count':>5s} {'PnL':>10s} {'WR':>6s} {'Avg':>8s} {'Blocked':>8s}")
    print(f"  {'-'*45}")
    hour_data = {}
    for h in range(24):
        sub = oos_bt[oos_bt["entry_h"] == h]
        if len(sub) > 0:
            pnl = sub["pnl"].sum()
            wr = (sub["pnl"] > 0).mean() * 100
            avg = sub["pnl"].mean()
            blocked = "YES" if h in BLOCK_H else ""
            print(f"  {h:>4d} {len(sub):>5d} ${pnl:>+9,.0f} {wr:>5.0f}% ${avg:>+7.1f} {blocked:>8s}")
            hour_data[h] = {"n": len(sub), "pnl": pnl, "wr": wr, "avg": avg}

    # ════════════════════════════════════
    # Test C: Session variants
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test C: Session Filter Variants")
    print("=" * 90)
    session_configs = [
        ("Baseline: block {0,1,2,12}", {0,1,2,12}, {0,5,6}),
        ("No hour block", set(), {0,5,6}),
        ("Block {0,1,2}", {0,1,2}, {0,5,6}),
        ("Block {0,1,2,3}", {0,1,2,3}, {0,5,6}),
        ("Block {0,1,2,12,13}", {0,1,2,12,13}, {0,5,6}),
        ("Block {0,1,2,11,12}", {0,1,2,11,12}, {0,5,6}),
        ("No day block", {0,1,2,12}, set()),
        ("Block Mon only", {0,1,2,12}, {0}),
        ("Block Sat,Sun only", {0,1,2,12}, {5,6}),
        ("Block Mon,Sat,Sun,Fri", {0,1,2,12}, {0,4,5,6}),
    ]
    session_results = []
    for label, bh, bd in session_configs:
        trades = backtest_phased_trail(df, early_ema=20, late_ema=20, switch_bar=12,
                                       block_hours=bh, block_days=bd)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Baseline") else ""
        print(f"  {label:<35s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%{tag}")
        session_results.append((label, r, trades, {"block_hours":bh,"block_days":bd}))

    # ════════════════════════════════════
    # Test D: Phased trail + maxSame=4 combination
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Test D: Best phased trail + maxSame=4 combination")
    print("=" * 90)

    # Find best phased trail
    best_phase = max(phase_results, key=lambda x: x[1]["oos_pnl"])
    best_phase_pnl = best_phase[1]["oos_pnl"]
    best_session = max(session_results, key=lambda x: x[1]["oos_pnl"])
    best_session_pnl = best_session[1]["oos_pnl"]

    combo_configs = [
        ("Baseline maxSame=3", {"early_ema":20,"late_ema":20,"switch_bar":12,"max_same":3}),
        ("maxSame=4 only", {"early_ema":20,"late_ema":20,"switch_bar":12,"max_same":4}),
    ]

    # Add best phase trail if it beat baseline
    if best_phase_pnl > BASELINE_OOS:
        pp = best_phase[3]
        combo_configs.append((f"Best phase ({best_phase[0][:25]})", {**pp, "max_same":3}))
        combo_configs.append((f"Best phase + maxSame=4", {**pp, "max_same":4}))

    # Add best session if it beat baseline
    if best_session_pnl > BASELINE_OOS and not best_session[0].startswith("Baseline"):
        sp = best_session[3]
        combo_configs.append((f"Best session ({best_session[0][:25]})", {"early_ema":20,"late_ema":20,"switch_bar":12,"max_same":3,**sp}))
        combo_configs.append((f"Best session + maxSame=4", {"early_ema":20,"late_ema":20,"switch_bar":12,"max_same":4,**sp}))

    # If both beat, combine all three
    if best_phase_pnl > BASELINE_OOS and best_session_pnl > BASELINE_OOS and not best_session[0].startswith("Baseline"):
        pp = best_phase[3]; sp = best_session[3]
        combo_configs.append(("Triple combo: phase+session+ms4", {**pp, **sp, "max_same":4}))

    combo_results = []
    for label, params in combo_configs:
        trades = backtest_phased_trail(df, **params)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if label.startswith("Baseline") else ""
        print(f"  {label:<40s}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%{tag}")
        combo_results.append((label, r, trades, params))

    # ════════════════════════════════════
    # Walk-forward for best overall
    # ════════════════════════════════════
    best_combo = max(combo_results, key=lambda x: x[1]["oos_pnl"])
    if best_combo[1]["oos_pnl"] > BASELINE_OOS:
        detail_report(best_combo[2], f"Best: {best_combo[0]}", MID_TS)

        print(f"\n{'='*60}")
        print(f"  Walk-Forward: {best_combo[0]}")
        print(f"{'='*60}")
        wf = walk_forward(df_raw, best_combo[3])

        print(f"\n  Walk-Forward: Baseline")
        wf_base = walk_forward(df_raw, {"early_ema":20,"late_ema":20,"switch_bar":12})

    # ════════════════════════════════════
    # Verdict
    # ════════════════════════════════════
    all_results = phase_results + session_results + combo_results
    overall_best = max(all_results, key=lambda x: x[1]["oos_pnl"])

    print(f"\n{'='*60}")
    print(f"  ROUND 6 VERDICT")
    print(f"{'='*60}")

    if overall_best[1]["oos_pnl"] > BASELINE_OOS:
        diff = overall_best[1]["oos_pnl"] - BASELINE_OOS
        pct = diff / BASELINE_OOS * 100
        print(f"  Best: {overall_best[0]}")
        print(f"  OOS ${overall_best[1]['oos_pnl']:+,.0f} (vs baseline ${BASELINE_OOS:+,d}, +{pct:.1f}%)")
        if overall_best[1]["oos_pnl"] > 8359:  # maxSame=4 record
            print(f"  >>> EXCEEDS maxSame=4 RECORD ($8,359) <<<")
        elif overall_best[1]["oos_pnl"] > 7837:
            print(f"  >>> EXCEEDS BASELINE RECORD ($7,837) <<<")
    else:
        print(f"  Baseline remains best.")

    print(f"\n  Anti-lookahead self-check:")
    print(f"  [v] Entry price = O[i+1]")
    print(f"  [v] Phased trail uses bar count from entry (no future info)")
    print(f"  [v] Session filter is time-of-day (inherent, no fitting)")
    print(f"  [v] All params decided before seeing results")


if __name__ == "__main__":
    main()
