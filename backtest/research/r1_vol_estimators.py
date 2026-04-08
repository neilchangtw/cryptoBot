"""
╔══════════════════════════════════════╗
║  第 1 輪：替代波動率估算器            ║
╚══════════════════════════════════════╝

已探索清單：（第一輪，空）
目前最佳記錄：GK 保守版 OOS $7,837

本輪假說：
  方向：用理論上更優的 OHLC 波動率估算器替換 Garman-Klass
  市場行為假說：ETH 年漂移 ±50%，GK 假設零漂移會高估趨勢期的波動率，
    導致在穩定單向移動中誤判「未壓縮」。漂移修正的估算器應更準確偵測
    真正的低波動壓縮 vs 緩慢方向性移動。
  Q1 確認：Yang-Zhang / Rogers-Satchell / Close-to-Close 從未在本專案測試
  Q2 市場行為：更精確的壓縮偵測 → 更好的進場時機 → 更高品質的突破信號
  Q3 ETH 1h edge：壓縮偵測是核心 edge，估算器精度直接影響信號品質

量化規則：
  進場：comp_pctile < 30 + Close Breakout(10bar) + Session + Freshness + ExitCD12
  進場價：next bar open
  出場：SafeNet ±4.5% + EMA20 trail(min 7bar) + EarlyStop(bar7-12, loss>2%, OR trail)
  參數：SHORT=5, LONG=20, WIN=100, THRESH=30, MaxSame=3, ExitCD=12（全部沿用 GK 基準）

測試方法：
  1. Garman-Klass（基準線，用於比對）
  2. Yang-Zhang（最小方差，漂移修正）
  3. Rogers-Satchell（漂移獨立，無零漂移假設）
  4. Close-to-Close（最簡，只用收盤價，測試是否 OHLC 真的必要）

上帝視角自檢：
  ☑ signal 只用 shift(1) 或更早數據？→ 是（ratio.shift(1).rolling(100)）
  ☑ 進場價是 next bar open？→ 是（O[i+1]）
  ☑ 所有滾動指標有 .shift(1)？→ 是
  ☑ 參數在看數據前就決定？→ 是（全部沿用 GK 基準）
  ☑ 沒有在看結果後調整任何參數？→ 是（一次跑完不調整）
  ☑ percentile 是純滾動窗口？→ 是（rolling(100)）
"""
import os, sys, pandas as pd, numpy as np
from datetime import datetime, timedelta
import warnings; warnings.filterwarnings("ignore")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Constants (locked — identical to GK baseline)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMP_SHORT = 5; COMP_LONG = 20; COMP_WIN = 100; COMP_THRESH = 30
BRK_LOOK = 10; MAX_SAME = 3; EXIT_CD = 12
SN_PCT = 0.045; MIN_TRAIL = 7; ES_PCT = 0.020; ES_END = 12
BLOCK_H = {0,1,2,12}; BLOCK_D = {0,5,6}
FEE = 2.0; NOTIONAL = 2000; ACCOUNT = 10000

END_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
MID_DATE = END_DATE - timedelta(days=365)
MID_TS = pd.Timestamp(MID_DATE)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared indicators
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_common(df):
    d = df.copy()
    d["ema20"] = d["close"].ewm(span=20).mean()
    cs1 = d["close"].shift(1)
    d["cmx"] = d["close"].shift(2).rolling(BRK_LOOK - 1).max()
    d["cmn"] = d["close"].shift(2).rolling(BRK_LOOK - 1).min()
    d["bl"] = cs1 > d["cmx"]
    d["bs"] = cs1 < d["cmn"]
    d["h"] = d["datetime"].dt.hour
    d["wd"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["h"].isin(BLOCK_H) | d["wd"].isin(BLOCK_D))
    return d

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Volatility estimators
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_gk(d):
    """Garman-Klass: gk = 0.5*ln(H/L)^2 - (2ln2-1)*ln(C/O)^2
    Assumes zero drift. Uses OHLC (4 prices)."""
    ln_hl = np.log(d["high"] / d["low"])
    ln_co = np.log(d["close"] / d["open"])
    gk = 0.5 * ln_hl**2 - (2*np.log(2)-1) * ln_co**2
    s = gk.rolling(COMP_SHORT).mean()
    l = gk.rolling(COMP_LONG).mean()
    d["pp"] = (s / l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    return d

def compute_yang_zhang(d):
    """Yang-Zhang: sigma^2 = sigma_o^2 + k*sigma_c^2 + (1-k)*sigma_RS^2
    Drift-corrected, minimum variance OHLC estimator.
    - sigma_o: overnight variance (open vs prev close)
    - sigma_c: close-to-open variance
    - sigma_RS: Rogers-Satchell (drift-independent component)
    - k = 0.34 / (1.34 + (n+1)/(n-1))"""
    overnight = np.log(d["open"] / d["close"].shift(1))
    co = np.log(d["close"] / d["open"])
    ln_hc = np.log(d["high"] / d["close"])
    ln_ho = np.log(d["high"] / d["open"])
    ln_lc = np.log(d["low"] / d["close"])
    ln_lo = np.log(d["low"] / d["open"])
    rs = ln_hc * ln_ho + ln_lc * ln_lo

    def yz_window(n):
        k = 0.34 / (1.34 + (n + 1) / (n - 1))
        var_o = overnight.rolling(n).var()   # ddof=1
        var_c = co.rolling(n).var()          # ddof=1
        mean_rs = rs.rolling(n).mean()
        return var_o + k * var_c + (1 - k) * mean_rs

    s = yz_window(COMP_SHORT)
    l = yz_window(COMP_LONG)
    d["pp"] = (s / l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    return d

def compute_rogers_satchell(d):
    """Rogers-Satchell: sigma^2 = E[ln(H/C)*ln(H/O) + ln(L/C)*ln(L/O)]
    Drift-independent by construction. Uses OHLC."""
    ln_hc = np.log(d["high"] / d["close"])
    ln_ho = np.log(d["high"] / d["open"])
    ln_lc = np.log(d["low"] / d["close"])
    ln_lo = np.log(d["low"] / d["open"])
    rs = ln_hc * ln_ho + ln_lc * ln_lo
    s = rs.rolling(COMP_SHORT).mean()
    l = rs.rolling(COMP_LONG).mean()
    d["pp"] = (s / l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    return d

def compute_close_to_close(d):
    """Close-to-Close: sigma^2 = E[ln(C_t/C_{t-1})^2]
    Simplest estimator. Only uses close prices (2 prices)."""
    cc = np.log(d["close"] / d["close"].shift(1))**2
    s = cc.rolling(COMP_SHORT).mean()
    l = cc.rolling(COMP_LONG).mean()
    d["pp"] = (s / l).shift(1).rolling(COMP_WIN).apply(pctile_func, raw=False)
    return d

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Backtest engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def backtest(df):
    """Standard backtest — identical logic for all estimators."""
    N = len(df)
    w = COMP_WIN + COMP_LONG + 20

    # Freshness: shifted versions
    d = df.copy()
    d["pp_p"] = d["pp"].shift(1)
    d["bl_p"] = d["bl"].shift(1)
    d["bs_p"] = d["bs"].shift(1)
    d["sok_p"] = d["sok"].shift(1)

    O = d["open"].values; H = d["high"].values; L = d["low"].values
    C = d["close"].values; E = d["ema20"].values
    PP = d["pp"].values; BL = d["bl"].values; BS = d["bs"].values; SOK = d["sok"].values
    PP_P = d["pp_p"].values; BL_P = d["bl_p"].values
    BS_P = d["bs_p"].values; SOK_P = d["sok_p"].values
    DT = d["datetime"].values

    lp = []; sp = []; trades = []
    last_exit = {"long": -9999, "short": -9999}

    for i in range(w, N - 1):
        # ── Exit: longs ──
        nl = []
        for p in lp:
            bh = i - p["ei"]; ep = p["e"]; exited = False

            sn = ep * (1 - SN_PCT)
            if L[i] <= sn:
                xp = sn - (sn - L[i]) * 0.25
                trades.append({"pnl": (xp - ep) * NOTIONAL / ep - FEE,
                               "tp": "SafeNet", "sd": "long", "bars": bh, "dt": DT[i]})
                last_exit["long"] = i; exited = True

            if not exited and MIN_TRAIL <= bh < ES_END:
                trail = C[i] <= E[i]
                early = C[i] <= ep * (1 - ES_PCT)
                if trail or early:
                    tp = "EarlyStop" if (early and not trail) else "Trail"
                    trades.append({"pnl": (C[i] - ep) * NOTIONAL / ep - FEE,
                                   "tp": tp, "sd": "long", "bars": bh, "dt": DT[i]})
                    last_exit["long"] = i; exited = True

            if not exited and bh >= ES_END and C[i] <= E[i]:
                trades.append({"pnl": (C[i] - ep) * NOTIONAL / ep - FEE,
                               "tp": "Trail", "sd": "long", "bars": bh, "dt": DT[i]})
                last_exit["long"] = i; exited = True

            if not exited:
                nl.append(p)
        lp = nl

        # ── Exit: shorts ──
        ns = []
        for p in sp:
            bh = i - p["ei"]; ep = p["e"]; exited = False

            sn = ep * (1 + SN_PCT)
            if H[i] >= sn:
                xp = sn + (H[i] - sn) * 0.25
                trades.append({"pnl": (ep - xp) * NOTIONAL / ep - FEE,
                               "tp": "SafeNet", "sd": "short", "bars": bh, "dt": DT[i]})
                last_exit["short"] = i; exited = True

            if not exited and MIN_TRAIL <= bh < ES_END:
                trail = C[i] >= E[i]
                early = C[i] >= ep * (1 + ES_PCT)
                if trail or early:
                    tp = "EarlyStop" if (early and not trail) else "Trail"
                    trades.append({"pnl": (ep - C[i]) * NOTIONAL / ep - FEE,
                                   "tp": tp, "sd": "short", "bars": bh, "dt": DT[i]})
                    last_exit["short"] = i; exited = True

            if not exited and bh >= ES_END and C[i] >= E[i]:
                trades.append({"pnl": (ep - C[i]) * NOTIONAL / ep - FEE,
                               "tp": "Trail", "sd": "short", "bars": bh, "dt": DT[i]})
                last_exit["short"] = i; exited = True

            if not exited:
                ns.append(p)
        sp = ns

        # ── Entry ──
        pp = PP[i]
        if np.isnan(pp):
            continue

        bl = BL[i]; bs = BS[i]; sok = SOK[i]
        cond = pp < COMP_THRESH

        pp_p = PP_P[i]; bl_p = BL_P[i]; bs_p = BS_P[i]; sok_p = SOK_P[i]
        if not np.isnan(pp_p):
            pc = pp_p < COMP_THRESH
            fl = not (pc and bl_p and sok_p)
            fs = not (pc and bs_p and sok_p)
        else:
            fl = fs = True

        lc = (i - last_exit["long"]) >= EXIT_CD
        sc = (i - last_exit["short"]) >= EXIT_CD

        if cond and bl and sok and fl and lc and len(lp) < MAX_SAME:
            lp.append({"e": O[i + 1], "ei": i})
        if cond and bs and sok and fs and sc and len(sp) < MAX_SAME:
            sp.append({"e": O[i + 1], "ei": i})

    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","tp","sd","bars","dt"])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Analysis helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def analyze(trades_df, label):
    t = trades_df.copy()
    t["dt"] = pd.to_datetime(t["dt"])
    is_t = t[t["dt"] < MID_TS]
    oos_t = t[t["dt"] >= MID_TS]

    def stats(sub, tag):
        n = len(sub)
        if n == 0:
            return {"tag": tag, "n": 0, "pnl": 0, "pf": 0, "wr": 0, "mdd": 0,
                    "monthly": 0, "sn": 0, "avg_hold": 0}
        pnl = sub["pnl"].sum()
        w = sub[sub["pnl"] > 0]["pnl"].sum()
        l_abs = abs(sub[sub["pnl"] < 0]["pnl"].sum())
        pf = w / l_abs if l_abs > 0 else 999
        wr = (sub["pnl"] > 0).mean() * 100
        # MDD
        eq = ACCOUNT + sub["pnl"].cumsum().values
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        mdd = abs(dd.min())
        # Monthly trades
        months = max((sub["dt"].max() - sub["dt"].min()).days / 30.4, 1)
        monthly = n / months
        sn = (sub["tp"] == "SafeNet").sum()
        avg_hold = sub["bars"].mean()
        return {"tag": tag, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
                "monthly": monthly, "sn": sn, "avg_hold": avg_hold}

    is_s = stats(is_t, "IS")
    oos_s = stats(oos_t, "OOS")

    # Monthly PnL for OOS
    if len(oos_t) > 0:
        oos_t = oos_t.copy()
        oos_t["month"] = oos_t["dt"].dt.to_period("M")
        monthly_pnl = oos_t.groupby("month")["pnl"].sum()
        pos_months = (monthly_pnl > 0).sum()
        total_months = len(monthly_pnl)
        max_month_pct = monthly_pnl.max() / oos_s["pnl"] * 100 if oos_s["pnl"] > 0 else 0
    else:
        pos_months = total_months = 0
        max_month_pct = 0
        monthly_pnl = pd.Series(dtype=float)

    # Exit breakdown for OOS
    if len(oos_t) > 0:
        exit_breakdown = oos_t.groupby("tp").agg(
            n=("pnl", "count"), pnl=("pnl", "sum"), avg_bars=("bars", "mean")
        ).to_dict("index")
    else:
        exit_breakdown = {}

    # Hold time buckets for OOS
    hold_buckets = {}
    if len(oos_t) > 0:
        for lo, hi, lbl in [(0,7,"<7h"),(7,12,"7-12h"),(12,24,"12-24h"),
                            (24,48,"24-48h"),(48,96,"48-96h"),(96,9999,">96h")]:
            sub = oos_t[(oos_t["bars"] >= lo) & (oos_t["bars"] < hi)]
            if len(sub) > 0:
                wr_b = (sub["pnl"] > 0).mean() * 100
                hold_buckets[lbl] = {"n": len(sub), "pnl": sub["pnl"].sum(), "wr": wr_b}

    return {
        "label": label, "is": is_s, "oos": oos_s,
        "pos_months": pos_months, "total_months": total_months,
        "max_month_pct": max_month_pct, "monthly_pnl": monthly_pnl,
        "exit_breakdown": exit_breakdown, "hold_buckets": hold_buckets,
    }

def print_comparison(results):
    print("\n" + "=" * 100)
    print("  COMPARISON TABLE")
    print("=" * 100)
    hdr = f"{'#':<3} {'Method':<20} {'IS t':>5} {'IS PnL':>10} {'IS PF':>7} " \
          f"{'OOS t':>6} {'OOS PnL':>10} {'OOS PF':>7} {'OOS WR':>7} " \
          f"{'MDD':>6} {'Annual':>9} {'Mo avg':>7} {'SN':>4}"
    print(hdr)
    print("-" * 100)
    for i, r in enumerate(results):
        iss = r["is"]; oos = r["oos"]
        annual = oos["pnl"] * 365 / max((MID_TS - pd.Timestamp(MID_DATE - timedelta(days=365))).days, 365)
        print(f"{i+1:<3} {r['label']:<20} {iss['n']:>5} ${iss['pnl']:>+9,.0f} {iss['pf']:>7.2f} "
              f"{oos['n']:>6} ${oos['pnl']:>+9,.0f} {oos['pf']:>7.2f} {oos['wr']:>6.1f}% "
              f"{oos['mdd']:>5.1f}% ${oos['pnl']:>+8,.0f} {oos['monthly']:>6.1f} {oos['sn']:>4}")

def print_detail(r):
    print(f"\n{'─'*60}")
    print(f"  {r['label']} — Detail")
    print(f"{'─'*60}")

    oos = r["oos"]
    print(f"  OOS: {oos['n']}t  ${oos['pnl']:+,.0f}  PF {oos['pf']:.2f}  WR {oos['wr']:.1f}%  "
          f"MDD {oos['mdd']:.1f}%  Avg hold {oos['avg_hold']:.1f}h")

    if r["exit_breakdown"]:
        print(f"\n  Exit breakdown (OOS):")
        for tp, v in r["exit_breakdown"].items():
            print(f"    {tp:12s}: {v['n']:>4}t  ${v['pnl']:>+8,.0f}  avg hold {v['avg_bars']:.0f}h")

    if r["hold_buckets"]:
        print(f"\n  Hold time (OOS):")
        for lbl, v in r["hold_buckets"].items():
            print(f"    {lbl:8s}: {v['n']:>4}t  ${v['pnl']:>+8,.0f}  WR {v['wr']:.0f}%")

    if len(r["monthly_pnl"]) > 0:
        print(f"\n  Monthly PnL (OOS): {r['pos_months']}/{r['total_months']} positive")
        for m, p in r["monthly_pnl"].items():
            print(f"    {m}: ${p:>+8,.0f}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Walk-forward (for best method)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def walk_forward(df, compute_fn, n_folds=10):
    d = compute_common(df)
    d = compute_fn(d)
    total_bars = len(d)
    fold_size = total_bars // n_folds
    results = []
    for fold in range(n_folds):
        start = fold * fold_size
        end = min(start + fold_size, total_bars)
        fold_df = d.iloc[max(0, start - 200):end].copy().reset_index(drop=True)
        t = backtest(fold_df)
        pnl = t["pnl"].sum() if len(t) > 0 else 0
        n = len(t)
        results.append({"fold": fold + 1, "n": n, "pnl": pnl})
    return results

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pass rate analysis
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def check_pass_rate(df, compute_fn, label):
    d = compute_common(df)
    d = compute_fn(d)
    pp = d["pp"].dropna()
    rate = (pp < COMP_THRESH).mean() * 100
    print(f"  {label}: {rate:.1f}% of bars pass comp_pctile < {COMP_THRESH}")
    return rate

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    df_raw = load()
    print(f"IS/OOS split: {MID_TS}")

    methods = [
        ("GK (baseline)", compute_gk),
        ("Yang-Zhang", compute_yang_zhang),
        ("Rogers-Satchell", compute_rogers_satchell),
        ("Close-to-Close", compute_close_to_close),
    ]

    # ── Pass rate check ──
    print(f"\n{'='*60}")
    print(f"  Pass Rate Analysis (comp_pctile < {COMP_THRESH})")
    print(f"{'='*60}")
    for label, fn in methods:
        check_pass_rate(df_raw, fn, label)

    # ── Run backtests ──
    results = []
    for label, fn in methods:
        print(f"\n  Running {label}...")
        d = compute_common(df_raw)
        d = fn(d)
        t = backtest(d)
        r = analyze(t, label)
        results.append(r)

    # ── Comparison ──
    print_comparison(results)

    # ── Detail for each ──
    for r in results:
        print_detail(r)

    # ── Walk-Forward for best non-baseline ──
    best_idx = 0
    best_pnl = results[0]["oos"]["pnl"]
    for i, r in enumerate(results):
        if r["oos"]["pnl"] > best_pnl:
            best_pnl = r["oos"]["pnl"]
            best_idx = i

    if best_idx > 0:
        best_label = results[best_idx]["label"]
        best_fn = methods[best_idx][1]
        print(f"\n{'='*60}")
        print(f"  Walk-Forward: {best_label} (best non-baseline)")
        print(f"{'='*60}")
        wf = walk_forward(df_raw, best_fn)
        pos = sum(1 for f in wf if f["pnl"] > 0)
        total_wf = sum(f["pnl"] for f in wf)
        for f in wf:
            sign = "[+]" if f["pnl"] > 0 else "[-]"
            print(f"  Fold {f['fold']:>2}: {f['n']:>4}t  ${f['pnl']:>+8,.0f}  {sign}")
        print(f"  Total: ${total_wf:>+8,.0f}  Positive folds: {pos}/{len(wf)}")
    else:
        print(f"\n  No method beat GK baseline — skipping walk-forward")

    # ── vs GK baseline ──
    gk_oos = results[0]["oos"]["pnl"]
    print(f"\n{'='*60}")
    print(f"  vs GK Baseline (OOS ${gk_oos:+,.0f})")
    print(f"{'='*60}")
    for r in results[1:]:
        diff = r["oos"]["pnl"] - gk_oos
        pct = diff / abs(gk_oos) * 100 if gk_oos != 0 else 0
        status = "BEAT" if diff > 0 else "BELOW"
        print(f"  {r['label']:<20}: OOS ${r['oos']['pnl']:>+8,.0f}  "
              f"diff ${diff:>+7,.0f} ({pct:>+5.1f}%)  [{status}]")

    # ── Final verdict ──
    print(f"\n{'='*60}")
    print(f"  ROUND 1 VERDICT")
    print(f"{'='*60}")
    if best_idx == 0:
        print(f"  GK remains best. No alternative estimator beat the baseline.")
        print(f"  Implication: The edge is NOT in estimator precision — GK's specific")
        print(f"  noise characteristics or OHLC weighting may match ETH 1h structure.")
    else:
        bl = results[best_idx]["label"]
        bp = results[best_idx]["oos"]["pnl"]
        print(f"  NEW BEST: {bl} — OOS ${bp:+,.0f} (vs GK ${gk_oos:+,.0f})")
        print(f"  → Proceed to audit if this is a new record vs $7,837")

    print(f"\n  Anti-lookahead self-check (final confirmation):")
    print(f"  [v] All comp_pctile use ratio.shift(1).rolling({COMP_WIN}).apply(pctile)")
    print(f"  [v] Entry price = O[i+1] (next bar open)")
    print(f"  [v] Parameters fixed before seeing data: {COMP_SHORT}/{COMP_LONG}/{COMP_WIN}/{COMP_THRESH}")
    print(f"  [v] No parameter adjustments after seeing results")
    print(f"  [v] Percentile = pure rolling window, no OOS data")
