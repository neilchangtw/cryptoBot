"""
Exploration Round 3: S Strategy — CMP + Bearish Close Position Filter
======================================================================
Hypothesis: The position of the signal bar's close within its range reflects
breakout conviction. A close in the bottom 40% of the bar's range = genuine
bearish breakout. A close in the upper portion = false breakdown / wick.

Theory: This is a well-known candlestick analysis concept ("bearish close").
Genuine breakdowns close near their lows because selling pressure dominated
the entire bar. False breakdowns dip below support but recover, closing higher.

Changes from baseline CMP:
  - NEW: Signal bar close position < 0.40 (bottom 40% of range)
    Formula: (close.shift(1) - low.shift(1)) / (high.shift(1) - low.shift(1)) < 0.40
  - All other parameters unchanged

Also test 6-sub expanded portfolio if trade count is too low.
"""

import os, sys, io, time, warnings
import numpy as np, pandas as pd
import requests
from datetime import timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings("ignore")

NOTIONAL = 2000; FEE = 2.0; ACCOUNT = 10000
SN_PCT = 0.055; SN_PEN = 0.25
BLOCK_H = {0, 1, 2, 12}; BLOCK_D = {0, 5, 6}
GK_SHORT = 5; GK_LONG = 20; GK_WIN = 100
TP_PCT = 0.02; MAX_HOLD = 12; EXIT_CD = 6; MAX_SAME = 5

# Round 3: Close position threshold
CLOSE_POS_THRESH = 0.40  # signal bar close must be in bottom 40% of range


def fetch_klines(symbol="ETHUSDT", interval="1h", days=730):
    url = "https://fapi.binance.com/fapi/v1/klines"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000
    all_data = []; cur = start_ms
    print(f"  Fetching {symbol} {interval} last {days} days...")
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1500}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status(); data = r.json(); break
            except:
                if attempt == 2: raise
                time.sleep(2)
        if not data: break
        all_data.extend(data); cur = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.1)
    df = pd.DataFrame(all_data, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","qv","trades","tbv","tqv","ignore"])
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["datetime"] = (df["datetime"] + timedelta(hours=8)).dt.tz_localize(None)
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    print(f"  {len(df)} bars: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    return df


def pctile_func(x):
    if x.max() == x.min(): return 50
    return (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100


def calc_indicators(df):
    d = df.copy()
    d["ret"] = d["close"].pct_change()
    log_hl = np.log(d["high"] / d["low"])
    log_co = np.log(d["close"] / d["open"])
    d["gk"] = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    d["gk"] = d["gk"].replace([np.inf, -np.inf], np.nan)
    d["gk_s"] = d["gk"].rolling(GK_SHORT).mean()
    d["gk_l"] = d["gk"].rolling(GK_LONG).mean()
    d["gk_r"] = (d["gk_s"] / d["gk_l"]).replace([np.inf, -np.inf], np.nan)
    d["gk_pct"] = d["gk_r"].shift(1).rolling(GK_WIN).apply(pctile_func)
    for t in [25, 30, 40]:
        d[f"gk{t}"] = d["gk_pct"] < t

    d["cs1"] = d["close"].shift(1)
    for bl in [8, 10, 12, 15, 20]:
        d[f"cmn{bl}"] = d["close"].shift(2).rolling(bl - 1).min()
        d[f"bs{bl}"] = d["cs1"] < d[f"cmn{bl}"]

    d["hour_utc8"] = d["datetime"].dt.hour
    d["dow"] = d["datetime"].dt.weekday
    d["sok"] = ~(d["hour_utc8"].isin(BLOCK_H) | d["dow"].isin(BLOCK_D))

    # Close position of signal bar (shift(1) = yesterday's bar)
    bar_range = d["high"].shift(1) - d["low"].shift(1)
    bar_range = bar_range.replace(0, np.nan)  # avoid div by zero
    d["close_pos"] = (d["close"].shift(1) - d["low"].shift(1)) / bar_range
    d["bearish_close"] = d["close_pos"] < CLOSE_POS_THRESH

    return d


def _b(v):
    try:
        if pd.isna(v): return False
    except: pass
    return bool(v)


def bt_cmp(df, max_same=5, gk_col="gk40", brk_look=10,
           tp_pct=0.02, max_hold=12, sn_pct=SN_PCT, exit_cd=EXIT_CD,
           use_closepos=False):
    W = 160
    H = df["high"].values; L = df["low"].values
    C = df["close"].values; O = df["open"].values; DT = df["datetime"].values
    BSa = df[f"bs{brk_look}"].values; SOKa = df["sok"].values; GKa = df[gk_col].values
    BCa = df["bearish_close"].values if use_closepos else None

    pos = []; trades = []; lx = -999
    for i in range(W, len(df) - 1):
        h, lo, c, dt, nxo = H[i], L[i], C[i], DT[i], O[i + 1]
        npos = []
        for p in pos:
            b = i - p["ei"]; done = False
            if h >= p["e"] * (1 + sn_pct):
                ep_ = p["e"] * (1 + sn_pct) + (h - p["e"] * (1 + sn_pct)) * SN_PEN
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "SN", "b": b, "dt": dt}); lx = i; done = True
            if not done and lo <= p["e"] * (1 - tp_pct):
                ep_ = p["e"] * (1 - tp_pct)
                pnl = (p["e"] - ep_) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "TP", "b": b, "dt": dt}); lx = i; done = True
            if not done and b >= max_hold:
                pnl = (p["e"] - c) * NOTIONAL / p["e"] - FEE
                trades.append({"pnl": pnl, "t": "MH", "b": b, "dt": dt}); lx = i; done = True
            if not done: npos.append(p)
        pos = npos

        gk_ok = _b(GKa[i]); brk = _b(BSa[i]); sok = _b(SOKa[i])
        bc_ok = _b(BCa[i]) if BCa is not None else True
        if gk_ok and brk and sok and bc_ok and (i - lx >= exit_cd) and len(pos) < max_same:
            pos.append({"e": nxo, "ei": i})
    return pd.DataFrame(trades) if trades else pd.DataFrame(columns=["pnl","t","b","dt"])


def bt_portfolio(df, strats):
    all_t = []
    for cfg in strats:
        t = bt_cmp(df, **cfg)
        if len(t) > 0: all_t.append(t)
    if not all_t: return pd.DataFrame(columns=["pnl","t","b","dt"])
    return pd.concat(all_t, ignore_index=True).sort_values("dt").reset_index(drop=True)


def evaluate(tdf, start_dt, end_dt, label=""):
    tdf = tdf.copy(); tdf["dt"] = pd.to_datetime(tdf["dt"])
    p = tdf[(tdf["dt"] >= start_dt) & (tdf["dt"] < end_dt)].reset_index(drop=True)
    n = len(p)
    if n == 0: return None
    pnl = p["pnl"].sum()
    w = p[p["pnl"] > 0]["pnl"].sum(); l_ = abs(p[p["pnl"] <= 0]["pnl"].sum())
    pf = w / l_ if l_ > 0 else 999; wr = (p["pnl"] > 0).mean() * 100
    eq = p["pnl"].cumsum(); dd = eq - eq.cummax(); mdd = abs(dd.min()) / ACCOUNT * 100
    p["m"] = p["dt"].dt.to_period("M"); ms = p.groupby("m")["pnl"].sum()
    pos_m = (ms > 0).sum(); mt = len(ms)
    top_v = ms.max(); top_n = str(ms.idxmax()); top_pct = top_v / pnl * 100 if pnl > 0 else 999
    nb = pnl - top_v; worst_v = ms.min(); worst_n = str(ms.idxmin())
    days = (end_dt - start_dt).days; tpm = n / (days / 30.44) if days > 0 else 0
    ed = p.groupby("t")["pnl"].agg(["count", "sum", "mean"])
    return {"label": label, "n": n, "pnl": pnl, "pf": pf, "wr": wr, "mdd": mdd,
            "months": mt, "pos_months": pos_m, "top_pct": top_pct, "top_m": top_n,
            "top_v": top_v, "nb": nb, "worst_m": worst_n, "worst_v": worst_v,
            "tpm": tpm, "ms": ms, "ed": ed, "avg": pnl / n if n else 0}


def walk_forward(df, strats, fs, fe):
    os_ = fs + pd.DateOffset(months=12); results = []
    for fold in range(6):
        ts = os_ + pd.DateOffset(months=fold * 2)
        te = min(ts + pd.DateOffset(months=2), fe)
        t = bt_portfolio(df, strats); t["dt"] = pd.to_datetime(t["dt"])
        tt = t[(t["dt"] >= ts) & (t["dt"] < te)]
        fp = tt["pnl"].sum() if len(tt) > 0 else 0
        results.append({"fold": fold+1, "ts": ts.strftime("%Y-%m-%d"),
                         "te": te.strftime("%Y-%m-%d"), "pnl": fp, "n": len(tt), "pos": fp > 0})
    return results


def print_result(r, r_is, label, fs, fe, strats, df):
    print(f"\n  ┌──────────────────────────────────────────┐")
    print(f"  │ {label:<40} │")
    print(f"  │ 回測：{fs.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}        │")
    if r_is: print(f"  │ IS PnL：${r_is['pnl']:+,.0f}  ({r_is['n']}t)                │")
    print(f"  │ OOS PnL：${r['pnl']:+,.0f}  ({r['n']}t)  avg ${r['avg']:+.1f}  │")
    print(f"  │ OOS PF：{r['pf']:.2f}  MDD：{r['mdd']:.1f}%  月均：{r['tpm']:.1f}筆 │")
    print(f"  │ OOS WR：{r['wr']:.1f}%                              │")
    print(f"  │ 正收益月：{r['pos_months']}/{r['months']}  topM：{r['top_pct']:.1f}% ({r['top_m']}) │")
    print(f"  │ 移除最佳月：${r['nb']:+,.0f}  最差月：${r['worst_v']:+,.0f} │")
    print(f"  └──────────────────────────────────────────┘")

    print(f"\n  ─── 月度 OOS ───")
    print(f"  | 月份    |   PnL   |  累計   |")
    print(f"  |---------|--------:|--------:|")
    cum = 0
    for m, v in r["ms"].items():
        cum += v; print(f"  | {m} | ${v:>+7,.0f} | ${cum:>+7,.0f} |")

    print(f"\n  ─── 出場分佈 ───")
    print(f"  {r['ed'].to_string()}")

    print(f"\n  ─── Walk-Forward ───")
    wf = walk_forward(df, strats, fs, fe)
    wf_pos = sum(1 for w in wf if w["pos"])
    for w in wf:
        print(f"  F{w['fold']}: {w['ts']}~{w['te']} | {w['n']}t ${w['pnl']:+,.0f} {'✓' if w['pos'] else '✗'}")
    print(f"  WF: {wf_pos}/6 正向")

    checks = {
        "PnL≥$10K": r["pnl"] >= 10000, "PF≥1.5": r["pf"] >= 1.5,
        "MDD≤25%": r["mdd"] <= 25, "月均≥10": r["tpm"] >= 10,
        "WR≥70%": r["wr"] >= 70,
        "正月≥75%": r["pos_months"] / max(r["months"], 1) >= 0.75,
        "topM≤20%": r["top_pct"] <= 20, "去佳月≥$8K": r["nb"] >= 8000,
        "WF≥5/6": wf_pos >= 5,
    }
    print(f"\n  ─── 達標 ───")
    for k, v in checks.items():
        print(f"  {'✓' if v else '✗'} {k}: {v}")
    fails = [k for k, v in checks.items() if not v]
    return len(fails) == 0, fails


if __name__ == "__main__":
    print("=" * 70)
    print("  ROUND 3: S CMP + Bearish Close Position Filter")
    print("=" * 70)

    df_raw = fetch_klines("ETHUSDT", "1h", 730)
    last_dt = df_raw["datetime"].iloc[-1]
    fs = last_dt - pd.Timedelta(days=730)
    mid = last_dt - pd.Timedelta(days=365)
    fe = last_dt

    print(f"\n  IS: {fs.strftime('%Y-%m-%d')} ~ {mid.strftime('%Y-%m-%d')}")
    print(f"  OOS: {mid.strftime('%Y-%m-%d')} ~ {fe.strftime('%Y-%m-%d')}")

    df = calc_indicators(df_raw)

    # Stats on close position
    oos = df[df["datetime"] >= mid]
    cp = oos["close_pos"].dropna()
    print(f"\n  Close position stats (OOS signal bars):")
    print(f"    Mean: {cp.mean():.3f}, Median: {cp.median():.3f}")
    print(f"    < 0.40: {(cp < 0.40).mean()*100:.1f}% of bars")

    # ---- Config A: 4-sub baseline ----
    base4 = [
        dict(max_same=5, gk_col="gk40", brk_look=8,  use_closepos=False),
        dict(max_same=5, gk_col="gk40", brk_look=15, use_closepos=False),
        dict(max_same=5, gk_col="gk30", brk_look=10, use_closepos=False),
        dict(max_same=5, gk_col="gk40", brk_look=12, use_closepos=False),
    ]

    # ---- Config B: 4-sub + close position filter ----
    cp4 = [
        dict(max_same=5, gk_col="gk40", brk_look=8,  use_closepos=True),
        dict(max_same=5, gk_col="gk40", brk_look=15, use_closepos=True),
        dict(max_same=5, gk_col="gk30", brk_look=10, use_closepos=True),
        dict(max_same=5, gk_col="gk40", brk_look=12, use_closepos=True),
    ]

    # ---- Config C: 6-sub + close position filter (if trade count too low) ----
    cp6 = [
        dict(max_same=5, gk_col="gk40", brk_look=8,  use_closepos=True),
        dict(max_same=5, gk_col="gk40", brk_look=15, use_closepos=True),
        dict(max_same=5, gk_col="gk30", brk_look=10, use_closepos=True),
        dict(max_same=5, gk_col="gk40", brk_look=12, use_closepos=True),
        dict(max_same=5, gk_col="gk30", brk_look=8,  use_closepos=True),
        dict(max_same=5, gk_col="gk40", brk_look=20, use_closepos=True),
    ]

    configs = [
        ("A: Baseline 4-sub", base4),
        ("B: 4-sub + ClosePos<0.40", cp4),
        ("C: 6-sub + ClosePos<0.40", cp6),
    ]

    for name, strats in configs:
        print(f"\n{'='*70}")
        print(f"  {name}")
        print(f"{'='*70}")
        t = bt_portfolio(df, strats)
        r = evaluate(t, mid, fe, name)
        r_is = evaluate(t, fs, mid, f"{name} IS")
        if r:
            passed, fails = print_result(r, r_is, name, fs, fe, strats, df)
            if passed:
                print(f"\n  ★ {name}: 全部達標！")
            else:
                print(f"\n  ✗ 失敗：{', '.join(fails)}")

    # God-view check
    print(f"\n{'='*70}")
    print("  上帝視角自檢")
    print(f"{'='*70}")
    print("  □ signal shift(1)+？ → 是 (gk_r.shift(1), close.shift(1), close_pos uses shift(1))")
    print("  □ 進場 next bar open？ → 是 (O[i+1])")
    print("  □ 滾動指標 shift(1)？ → 是")
    print("  □ 參數非數據驅動？ → 是 (0.40 = standard bearish close definition)")
    print("  □ OOS 後未調參？ → 是")
    print("  □ rolling 無洩漏？ → 是")
