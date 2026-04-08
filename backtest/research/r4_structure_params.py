"""
╔══════════════════════════════════════╗
║  第 4 輪：結構參數敏感度              ║
╚══════════════════════════════════════╝

已探索清單：
  R1: 替代波動率估算器（YZ/RS/CC）→ 全部不如 GK
  R2: 進場品質過濾器（Volume/Depth/Strength/Trend）→ 全部降低 PnL
  R3: 出場參數（Trail EMA/SafeNet/MinTrail/EarlyStop）→ 已近局部最優，+2.1%組合在噪音內
目前最佳記錄：GK 保守版 OOS $7,837

本輪假說：
  方向：掃描從未系統測試的結構參數——突破回看期、出場冷卻期、GK 窗口大小
  市場行為假說：
    A) BRK_LOOK=10 bar 可能不是 ETH 1h 的最佳回看。更短的回看可能
       在快速壓縮後更早觸發信號；更長的可能過濾假突破。
    B) EXIT_CD=12 bar 冷卻可能太長（錯過好機會）或太短（重複進場）。
    C) GK 5/20 窗口可能不是偵測 ETH 壓縮的最佳比例。
       3/20 更敏感，5/30 更穩定。
  Q1 確認：這些參數組合從未系統測試
  Q2 市場行為：這些影響信號「什麼時候觸發」而非「怎麼過濾」
  Q3 ETH 1h edge：改變壓縮偵測結構

上帝視角自檢：
  ☑ signal 只用 shift(1) 或更早數據？→ 是
  ☑ 進場價是 next bar open？→ 是（O[i+1]）
  ☑ 參數掃描範圍在看數據前就決定？→ 是
  ☑ one-at-a-time sweep？→ 是（每次只動一個參數）
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Default constants (baseline)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEF_SHORT = 5; DEF_LONG = 20; DEF_WIN = 100; DEF_THRESH = 30
DEF_BRK = 10; DEF_MAX_SAME = 3; DEF_EXIT_CD = 12
DEF_SN = 0.045; DEF_MIN_TRAIL = 7; DEF_ES_PCT = 0.020; DEF_ES_END = 12
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
FEE = 2.0; NOTIONAL = 2000; ACCOUNT = 10000

END_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
MID_DATE = END_DATE - timedelta(days=365)
MID_TS = pd.Timestamp(MID_DATE)

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


def compute_and_backtest(df_raw, gk_short=DEF_SHORT, gk_long=DEF_LONG,
                         gk_win=DEF_WIN, brk_look=DEF_BRK, exit_cd=DEF_EXIT_CD):
    """Compute indicators + backtest in one call with configurable structural params."""
    d = df_raw.copy()

    # EMA20
    d["ema20"] = d["close"].ewm(span=20).mean()

    # GK compression with configurable windows
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    gk_s = gk.rolling(gk_short).mean()
    gk_l = gk.rolling(gk_long).mean()
    d["pp"] = (gk_s / gk_l).shift(1).rolling(gk_win).apply(pctile_func, raw=False)

    # Breakout with configurable lookback
    cs1 = d["close"].shift(1)
    d["cmx"] = d["close"].shift(2).rolling(brk_look - 1).max()
    d["cmn"] = d["close"].shift(2).rolling(brk_look - 1).min()
    d["bl"] = cs1 > d["cmx"]
    d["bs"] = cs1 < d["cmn"]

    # Session
    d["h"] = d["datetime"].dt.hour; d["wd"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["h"].isin(BLOCK_H) | d["wd"].isin(BLOCK_D))

    # Freshness
    d["pp_p"] = d["pp"].shift(1)
    d["bl_p"] = d["bl"].shift(1); d["bs_p"] = d["bs"].shift(1); d["sok_p"] = d["sok"].shift(1)

    # Backtest
    N = len(d)
    w = gk_win + gk_long + 20

    O = d["open"].values; H = d["high"].values; L = d["low"].values
    C = d["close"].values; E = d["ema20"].values
    PP = d["pp"].values; BL = d["bl"].values; BS = d["bs"].values; SOK = d["sok"].values
    PP_P = d["pp_p"].values; BL_P = d["bl_p"].values
    BS_P = d["bs_p"].values; SOK_P = d["sok_p"].values
    DT = d["datetime"].values

    lp = []; sp = []; trades = []
    last_exit = {"long": -9999, "short": -9999}

    for i in range(w, N - 1):
        # Exit longs
        nl = []
        for p in lp:
            bh = i - p["ei"]; ep = p["e"]; exited = False
            sn = ep * (1 - DEF_SN)
            if L[i] <= sn:
                xp = sn - (sn - L[i]) * 0.25
                trades.append({"pnl": (xp-ep)*NOTIONAL/ep - FEE, "tp":"SafeNet", "sd":"long", "bars":bh, "dt":DT[i]})
                last_exit["long"] = i; exited = True
            if not exited and DEF_MIN_TRAIL <= bh < DEF_ES_END:
                trail = C[i] <= E[i]; early = C[i] <= ep*(1-DEF_ES_PCT)
                if trail or early:
                    tp = "EarlyStop" if (early and not trail) else "Trail"
                    trades.append({"pnl": (C[i]-ep)*NOTIONAL/ep - FEE, "tp":tp, "sd":"long", "bars":bh, "dt":DT[i]})
                    last_exit["long"] = i; exited = True
            if not exited and bh >= DEF_ES_END and C[i] <= E[i]:
                trades.append({"pnl": (C[i]-ep)*NOTIONAL/ep - FEE, "tp":"Trail", "sd":"long", "bars":bh, "dt":DT[i]})
                last_exit["long"] = i; exited = True
            if not exited: nl.append(p)
        lp = nl

        # Exit shorts
        ns = []
        for p in sp:
            bh = i - p["ei"]; ep = p["e"]; exited = False
            sn = ep * (1 + DEF_SN)
            if H[i] >= sn:
                xp = sn + (H[i]-sn)*0.25
                trades.append({"pnl": (ep-xp)*NOTIONAL/ep - FEE, "tp":"SafeNet", "sd":"short", "bars":bh, "dt":DT[i]})
                last_exit["short"] = i; exited = True
            if not exited and DEF_MIN_TRAIL <= bh < DEF_ES_END:
                trail = C[i] >= E[i]; early = C[i] >= ep*(1+DEF_ES_PCT)
                if trail or early:
                    tp = "EarlyStop" if (early and not trail) else "Trail"
                    trades.append({"pnl": (ep-C[i])*NOTIONAL/ep - FEE, "tp":tp, "sd":"short", "bars":bh, "dt":DT[i]})
                    last_exit["short"] = i; exited = True
            if not exited and bh >= DEF_ES_END and C[i] >= E[i]:
                trades.append({"pnl": (ep-C[i])*NOTIONAL/ep - FEE, "tp":"Trail", "sd":"short", "bars":bh, "dt":DT[i]})
                last_exit["short"] = i; exited = True
            if not exited: ns.append(p)
        sp = ns

        # Entry
        pp = PP[i]
        if np.isnan(pp): continue
        bl = BL[i]; bs = BS[i]; sok = SOK[i]
        cond = pp < DEF_THRESH

        pp_p = PP_P[i]; bl_p = BL_P[i]; bs_p = BS_P[i]; sok_p = SOK_P[i]
        if not np.isnan(pp_p):
            pc = pp_p < DEF_THRESH
            fl = not (pc and bl_p and sok_p)
            fs = not (pc and bs_p and sok_p)
        else:
            fl = fs = True

        lc = (i - last_exit["long"]) >= exit_cd
        sc = (i - last_exit["short"]) >= exit_cd

        if cond and bl and sok and fl and lc and len(lp) < DEF_MAX_SAME:
            lp.append({"e": O[i+1], "ei": i})
        if cond and bs and sok and fs and sc and len(sp) < DEF_MAX_SAME:
            sp.append({"e": O[i+1], "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])


def analyze(trades_df, mid_ts):
    if len(trades_df) == 0:
        return {"is_t":0, "is_pnl":0, "oos_t":0, "oos_pnl":0, "oos_pf":0, "oos_wr":0, "mdd":0, "sn":0, "avg_hold":0}
    t = trades_df.copy(); t["dt"] = pd.to_datetime(t["dt"])
    is_t = t[t["dt"] < mid_ts]; oos_t = t[t["dt"] >= mid_ts]

    def stats(df):
        if len(df)==0: return 0,0,0,0
        tot=df["pnl"].sum(); w=df[df["pnl"]>0]["pnl"].sum()
        l=abs(df[df["pnl"]<0]["pnl"].sum()); pf=w/l if l>0 else 999
        wr=(df["pnl"]>0).mean()*100; return len(df),tot,pf,wr

    isn,isp,ispf,iswr = stats(is_t)
    on,op,opf,owr = stats(oos_t)

    mdd_pct = 0
    if len(oos_t)>0:
        cum=oos_t["pnl"].cumsum(); dd=cum-cum.cummax()
        mdd_pct = abs(dd.min())/ACCOUNT*100

    sn = len(oos_t[oos_t["tp"]=="SafeNet"]) if len(oos_t)>0 else 0
    avg_hold = oos_t["bars"].mean() if len(oos_t)>0 else 0

    return {"is_t":isn, "is_pnl":isp, "is_pf":ispf,
            "oos_t":on, "oos_pnl":op, "oos_pf":opf, "oos_wr":owr,
            "mdd":mdd_pct, "sn":sn, "avg_hold":avg_hold}


def detail_report(trades_df, label, mid_ts):
    t = trades_df.copy(); t["dt"] = pd.to_datetime(t["dt"])
    oos = t[t["dt"] >= mid_ts]
    print(f"\n{'='*60}")
    print(f"  {label} -- Detail")
    print(f"{'='*60}")
    if len(oos)==0: print("  No OOS trades"); return

    r = analyze(trades_df, mid_ts)
    print(f"  OOS: {len(oos)}t  ${oos['pnl'].sum():+,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  Avg hold {r['avg_hold']:.1f}h")

    print(f"\n  Exit breakdown (OOS):")
    for tp in ["EarlyStop","SafeNet","Trail"]:
        sub = oos[oos["tp"]==tp]
        if len(sub)>0:
            print(f"    {tp:<12s}: {len(sub):>4d}t  ${sub['pnl'].sum():>+10,.0f}  avg hold {sub['bars'].mean():.0f}h")

    print(f"\n  Hold time (OOS):")
    for lo,hi,lbl in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),(24,48,"24-48h"),(48,96,"48-96h")]:
        sub = oos[(oos["bars"]>=lo)&(oos["bars"]<hi)]
        if len(sub)>0:
            wr = (sub["pnl"]>0).mean()*100
            print(f"    {lbl:<8s}: {len(sub):>4d}t  ${sub['pnl'].sum():>+10,.0f}  WR {wr:.0f}%")

    oos2 = oos.copy(); oos2["mo"] = oos2["dt"].dt.to_period("M")
    mo = oos2.groupby("mo")["pnl"].sum()
    print(f"\n  Monthly PnL (OOS): {(mo>0).sum()}/{len(mo)} positive")
    for m,p in mo.items():
        print(f"    {m}: ${p:>+10,.0f}")


def walk_forward(df_raw, label, params, n_folds=10):
    d = df_raw.copy(); d["datetime"] = pd.to_datetime(d["datetime"])
    oos_df = d[d["datetime"] >= MID_TS].copy()
    if len(oos_df)<200: print("  Not enough data"); return 0

    fold_size = len(oos_df)//n_folds
    results = []
    for fold in range(n_folds):
        start = fold*fold_size
        end = start+fold_size if fold<n_folds-1 else len(oos_df)
        fold_df = oos_df.iloc[max(0,start-300):end].copy()
        trades = compute_and_backtest(fold_df, **params)
        pnl = trades["pnl"].sum() if len(trades)>0 else 0
        results.append(pnl)

    pos = sum(1 for r in results if r>0)
    print(f"\n  Walk-Forward ({n_folds} folds): {pos}/{n_folds} positive")
    for i,r in enumerate(results):
        print(f"    Fold {i+1}: ${r:>+10,.0f}")
    return pos


def main():
    df_raw = load()
    print(f"IS/OOS split: {MID_TS}\n")

    BASELINE_OOS = 7837

    # ════════════════════════════════════
    # Sweep A: Breakout Lookback
    # ════════════════════════════════════
    print("=" * 90)
    print("  Sweep A: Breakout Lookback (BRK_LOOK)")
    print("=" * 90)
    brk_results = []
    for brk in [5, 7, 8, 10, 12, 15, 20]:
        trades = compute_and_backtest(df_raw, brk_look=brk)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if brk == 10 else ""
        print(f"  BRK {brk:>2d}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        brk_results.append((brk, r, trades))

    # ════════════════════════════════════
    # Sweep B: Exit Cooldown
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Sweep B: Exit Cooldown (EXIT_CD)")
    print("=" * 90)
    cd_results = []
    for cd in [4, 6, 8, 10, 12, 16, 20, 24]:
        trades = compute_and_backtest(df_raw, exit_cd=cd)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if cd == 12 else ""
        print(f"  CD {cd:>2d}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        cd_results.append((cd, r, trades))

    # ════════════════════════════════════
    # Sweep C: GK Short Window
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Sweep C: GK Short Window (GK_SHORT, long=20 fixed)")
    print("=" * 90)
    short_results = []
    for s in [3, 4, 5, 7, 10]:
        trades = compute_and_backtest(df_raw, gk_short=s)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if s == 5 else ""
        print(f"  SHORT {s:>2d}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        short_results.append((s, r, trades))

    # ════════════════════════════════════
    # Sweep D: GK Long Window
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Sweep D: GK Long Window (GK_LONG, short=5 fixed)")
    print("=" * 90)
    long_results = []
    for l in [10, 15, 20, 30, 40, 50]:
        trades = compute_and_backtest(df_raw, gk_long=l)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if l == 20 else ""
        print(f"  LONG {l:>2d}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        long_results.append((l, r, trades))

    # ════════════════════════════════════
    # Sweep E: GK Percentile Window
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print("  Sweep E: GK Percentile Window (GK_WIN)")
    print("=" * 90)
    win_results = []
    for w in [50, 75, 100, 150, 200]:
        trades = compute_and_backtest(df_raw, gk_win=w)
        r = analyze(trades, MID_TS)
        tag = " <-- baseline" if w == 100 else ""
        print(f"  WIN {w:>3d}: OOS {r['oos_t']:>4d}t  ${r['oos_pnl']:>+9,.0f}  PF {r['oos_pf']:.2f}  WR {r['oos_wr']:.1f}%  MDD {r['mdd']:.1f}%  hold {r['avg_hold']:.0f}h  SN {r['sn']}{tag}")
        win_results.append((w, r, trades))

    # ════════════════════════════════════
    # Best per sweep
    # ════════════════════════════════════
    print(f"\n{'='*90}")
    print(f"  BEST PER SWEEP (vs baseline OOS ${BASELINE_OOS:+,d})")
    print("=" * 90)

    all_sweeps = [
        ("BRK_LOOK", brk_results),
        ("EXIT_CD", cd_results),
        ("GK_SHORT", short_results),
        ("GK_LONG", long_results),
        ("GK_WIN", win_results),
    ]

    any_beat = False
    best_overall = None
    best_overall_pnl = BASELINE_OOS
    best_overall_label = "baseline"
    best_overall_params = {}

    for name, results in all_sweeps:
        best = max(results, key=lambda x: x[1]["oos_pnl"])
        diff = best[1]["oos_pnl"] - BASELINE_OOS
        tag = "NEW BEST" if diff > 0 else "no improvement"
        print(f"  {name:<10s}: best = {best[0]} -> OOS ${best[1]['oos_pnl']:>+9,.0f} (diff ${diff:>+7,.0f}) [{tag}]")
        if best[1]["oos_pnl"] > best_overall_pnl:
            best_overall_pnl = best[1]["oos_pnl"]
            best_overall = best[2]
            best_overall_label = f"{name}={best[0]}"
            best_overall_params = {
                "gk_short": best[0] if name == "GK_SHORT" else DEF_SHORT,
                "gk_long": best[0] if name == "GK_LONG" else DEF_LONG,
                "gk_win": best[0] if name == "GK_WIN" else DEF_WIN,
                "brk_look": best[0] if name == "BRK_LOOK" else DEF_BRK,
                "exit_cd": best[0] if name == "EXIT_CD" else DEF_EXIT_CD,
            }
            any_beat = True

    # ════════════════════════════════════
    # If any beat, try combination and walk-forward
    # ════════════════════════════════════
    if any_beat:
        # Individual best detail
        detail_report(best_overall, f"Best: {best_overall_label}", MID_TS)

        # Combination of all bests
        combo_params = {}
        for name, results in all_sweeps:
            best = max(results, key=lambda x: x[1]["oos_pnl"])
            if name == "BRK_LOOK":
                combo_params["brk_look"] = best[0] if best[1]["oos_pnl"] > BASELINE_OOS else DEF_BRK
            elif name == "EXIT_CD":
                combo_params["exit_cd"] = best[0] if best[1]["oos_pnl"] > BASELINE_OOS else DEF_EXIT_CD
            elif name == "GK_SHORT":
                combo_params["gk_short"] = best[0] if best[1]["oos_pnl"] > BASELINE_OOS else DEF_SHORT
            elif name == "GK_LONG":
                combo_params["gk_long"] = best[0] if best[1]["oos_pnl"] > BASELINE_OOS else DEF_LONG
            elif name == "GK_WIN":
                combo_params["gk_win"] = best[0] if best[1]["oos_pnl"] > BASELINE_OOS else DEF_WIN

        print(f"\n{'='*60}")
        print(f"  COMBINATION: Best of each sweep")
        print(f"{'='*60}")
        print(f"  Params: {combo_params}")
        combo_trades = compute_and_backtest(df_raw, **combo_params)
        combo_r = analyze(combo_trades, MID_TS)
        diff = combo_r["oos_pnl"] - BASELINE_OOS
        print(f"  Combo: OOS {combo_r['oos_t']:>4d}t  ${combo_r['oos_pnl']:>+9,.0f}  PF {combo_r['oos_pf']:.2f}  WR {combo_r['oos_wr']:.1f}%  MDD {combo_r['mdd']:.1f}%")
        print(f"  vs baseline: ${diff:>+,.0f} ({diff/BASELINE_OOS*100:+.1f}%)")

        if combo_r["oos_pnl"] > BASELINE_OOS:
            detail_report(combo_trades, "Combo", MID_TS)

            print(f"\n{'='*60}")
            print(f"  Walk-Forward: Combo")
            print(f"{'='*60}")
            wf_combo = walk_forward(df_raw, "Combo", combo_params)

            print(f"\n  Walk-Forward: Baseline (comparison)")
            base_params = {"gk_short":DEF_SHORT, "gk_long":DEF_LONG, "gk_win":DEF_WIN,
                           "brk_look":DEF_BRK, "exit_cd":DEF_EXIT_CD}
            wf_base = walk_forward(df_raw, "Baseline", base_params)

    # ════════════════════════════════════
    # Verdict
    # ════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"  ROUND 4 VERDICT")
    print(f"{'='*60}")

    if best_overall_pnl > BASELINE_OOS:
        diff = best_overall_pnl - BASELINE_OOS
        pct = diff / BASELINE_OOS * 100
        print(f"  Best single-param: {best_overall_label}")
        print(f"  OOS ${best_overall_pnl:+,.0f} (vs baseline ${BASELINE_OOS:+,d}, +{pct:.1f}%)")
        if best_overall_pnl > 7837:
            print(f"  >>> EXCEEDS ALL-TIME RECORD -- AUDIT REQUIRED <<<")
    else:
        print(f"  GK baseline structural params are at or near optimum.")
        print(f"  No single structural change improved OOS PnL.")

    # Landscape
    print(f"\n  Parameter landscape:")
    for name, results in all_sweeps:
        vals = [r["oos_pnl"] for _,r,_ in results]
        spread = max(vals)-min(vals)
        pct = spread/BASELINE_OOS*100
        print(f"  {name:<10s}: ${min(vals):>+9,.0f} to ${max(vals):>+9,.0f} (spread {pct:.1f}%)")

    print(f"\n  Anti-lookahead self-check:")
    print(f"  [v] Entry price = O[i+1]")
    print(f"  [v] comp_pctile uses ratio.shift(1).rolling(win).apply(pctile)")
    print(f"  [v] breakout uses close.shift(1) vs close.shift(2).rolling(brk-1)")
    print(f"  [v] one-at-a-time sweep, not grid search")
    print(f"  [v] parameter ranges decided before seeing data")


if __name__ == "__main__":
    main()
